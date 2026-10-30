"""機關側（SPEC §7.2）與據點側（§7.3）工具：正常輸入、k 遮蔽、權限邊界。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import ProgrammingError

from app.db.engines import DbRole, session_scope
from app.db.models_anon import CohortWeekly, SentimentAlert, SentimentItem
from app.db.models_idn import CareTask
from app.db.models_sys import AnalysisQuery
from app.deidentify.k_anon import KAnonViolation
from app.events.bus import EventBus
from app.services import artifacts
from app.services.sandbox import SandboxRejected
from app.tools.registry import ToolContext, ToolResult, registry
from tests.conftest import Factory
from tests.fixtures import Person
from tests.helpers import TEST_SITE, audit_rows

# 遠未來週，避免與其他測試混在一起
WEEKS = [date(2097, 1, 7) + timedelta(weeks=i) for i in range(3)]


def _square(lng: float, lat: float, size: float = 0.01) -> dict[str, Any]:
    ring = [[lng, lat], [lng + size, lat], [lng + size, lat + size], [lng, lat + size]]
    return {"type": "Polygon", "coordinates": [[*ring, ring[0]]]}


def agency(tool: str, agent: str = "A3", **args: Any) -> ToolResult:
    with session_scope(DbRole.ANON_READER) as s:
        return registry.call(tool, args, ToolContext(agent=agent, db=s))


def agency_data(tool: str, agent: str = "A3", **args: Any) -> dict[str, Any]:
    data = agency(tool, agent, **args).data
    assert data is not None
    return data


@pytest.fixture
def cohort() -> Iterator[None]:
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text(
                "INSERT INTO pub.villages (code, name, district, population, geom) VALUES "
                "('TV1', '甲里', '測試區', 3000, :g1), ('TV2', '乙里', '測試區', 2000, :g2) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "g1": json.dumps(_square(121.00, 25.00)),
                "g2": json.dumps(_square(121.02, 25.00)),
            },
        )
    with session_scope(DbRole.ANON_WRITER) as s:
        for week, rising in zip(WEEKS, ("0.40", "0.50", "0.60"), strict=True):
            for village, value, n in (("TV1", rising, 12), ("TV2", "0.30", 3), ("ALL", "0.38", 40)):
                s.add(
                    CohortWeekly(
                        week=week,
                        village_code=village,
                        age_band="ALL",
                        sex="ALL",
                        metric="bp_uncontrolled_rate",
                        value=Decimal(value),
                        n=n,
                    )
                )
    yield
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(CohortWeekly).where(CohortWeekly.week.in_(WEEKS)))


class TestCohortAndSandbox:
    def test_cohort_series_and_hotspots_are_k_masked(self, cohort: None) -> None:
        data = agency(
            "cohort_analysis",
            village_codes=["TV1", "TV2"],
            metrics=["bp_uncontrolled_rate"],
            weeks=3,
        ).data
        assert data is not None
        metric = data["metrics"]["bp_uncontrolled_rate"]
        assert [p["value"] for p in metric["series"]["TV1"]] == [0.4, 0.5, 0.6]
        assert all(p["value"] is None and p["n"] is None for p in metric["series"]["TV2"])
        spots = {f["properties"]["village_code"]: f for f in metric["hotspots"]["features"]}
        assert spots["TV1"]["properties"]["value"] == 0.5  # 最近「完整」週＝倒數第二週
        assert spots["TV2"]["properties"]["masked"] is True
        assert data["masked_rows"] > 0

    def test_sandbox_sql_gets_query_id_and_masks(self, cohort: None) -> None:
        result = agency(
            "sandbox_sql",
            sql="SELECT village_code, value, n FROM anon.cohort_weekly "
            "WHERE week = '2097-01-07' ORDER BY village_code",
        ).data
        assert result is not None and result["query_id"].startswith("Q-")
        assert result["masked_rows"] == 1
        with session_scope(DbRole.IDN_APP) as s:
            logged = s.get_one(AnalysisQuery, result["query_id"])
            assert (logged.agent, logged.masked_rows) == ("A3", 1)

    @pytest.mark.parametrize(
        ("sql", "error"),
        [
            ("SELECT village_code, avg(value) FROM anon.cohort_weekly GROUP BY 1", KAnonViolation),
            ("DELETE FROM anon.cohort_weekly WHERE true", SandboxRejected),
            ("SELECT 1 AS x; SELECT 2 AS y", SandboxRejected),
            ("SELECT * FROM idn.users LIMIT 1", ProgrammingError),  # anon_reader 沒有 idn 權限
        ],
    )
    def test_sandbox_sql_rejections(self, sql: str, error: type[Exception]) -> None:
        with pytest.raises(error):
            agency("sandbox_sql", sql=sql)

    def test_sandbox_python_runs_without_network(self) -> None:
        ok = agency(
            "sandbox_python",
            code=(
                "print(sum(r['n'] for r in INPUTS['rows']))\n"
                "open(OUT_DIR + '/out.csv', 'w').write('a,b\\n1,2\\n')"
            ),
            inputs={"rows": [{"n": 5}, {"n": 7}]},
        ).data
        assert ok is not None and ok["stdout"].strip() == "12" and len(ok["artifacts"]) == 1
        blocked = agency(
            "sandbox_python", code=("import socket\nsocket.create_connection(('example.com', 80))")
        ).data
        assert blocked is not None and blocked["exit_code"] != 0
        assert "沙箱禁止網路" in blocked["stderr"]


def test_chart_and_report_artifacts() -> None:
    chart = agency(
        "chart_render",
        spec={
            "type": "line",
            "title": "未控制率",
            "x": ["W1", "W2"],
            "series": [{"name": "光明里", "values": [0.4, 0.6]}],
        },
    ).data
    assert chart is not None
    svg = artifacts.path_of(chart["artifact"])
    assert svg is not None and "光明里" in svg.read_text(encoding="utf-8")
    report = agency(
        "report_build",
        title="分析<報告>",
        sections=[
            {
                "heading": "結論",
                "body": "- 第一點 **重要**\n- <script>x</script>",
                "charts": [chart["artifact"]],
            }
        ],
        appendix=["查詢編號 Q-0001", "k = 5"],
    ).data
    assert report is not None
    html = artifacts.path_of(report["artifact"]).read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "<strong>重要</strong>" in html and "<script>" not in html and chart["artifact"] in html


def test_resource_sim_reports_coverage_gain(cohort: None) -> None:
    data = agency(
        "resource_sim",
        agent="A2",
        candidate_sites=[{"name": "候選點", "lat": 25.005, "lng": 121.025}],
        radius_km=0.3,
    ).data
    assert data is not None and data["coverage_after"] > data["coverage_before"]
    assert "乙里" in data["newly_covered"] and data["assumptions"]


def test_nlp_tools_rule_based() -> None:
    texts = ["光明里衛生所排隊很久，建議改預約制", "和平里志工很親切，推薦大家來"]
    sentiment = agency_data("nlp_sentiment", agent="A4", texts=texts)
    assert sentiment["labels"][0]["sentiment"] == "negative"
    topics = agency_data("nlp_topic", agent="A4", texts=texts)
    assert topics["labels"] == ["排隊等候", "服務態度"]


@pytest.fixture
def spike() -> Iterator[str]:
    village = f"SV{uuid.uuid4().hex[:4]}"
    now = datetime.now(UTC)
    rows = [
        (now - timedelta(days=d), src)
        for d, src in (
            (1, "google_places"),
            (2, "forum"),
            (2, "google_places"),
            (3, "forum"),
            (4, "news"),
            (5, "google_places"),
            (15, "forum"),
            (30, "forum"),
        )
    ]
    with session_scope(DbRole.ANON_WRITER) as s:
        s.add_all(
            SentimentItem(
                collected_at=at,
                source_class=src,
                source_url=f"https://x/{i}",
                village_code=village,
                topic="排隊等候",
                sentiment="negative",
                intensity=Decimal("0.6"),
                summary="排隊很久",
            )
            for i, (at, src) in enumerate(rows)
        )
    yield village
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(SentimentItem).where(SentimentItem.village_code == village))


def test_trend_detect_flags_multi_source_spike(spike: str) -> None:
    data = agency("trend_detect", agent="A4", topic="排隊等候", village_code=spike).data
    assert data is not None
    assert (data["window_count"], data["baseline_weekly"]) == (6, 0.5)
    assert data["is_alert"] and len(data["source_classes"]) == 3 and data["source_urls"]


def test_alert_emit_writes_alert_and_event(spike: str, bus: EventBus) -> None:
    data = agency(
        "alert_emit",
        agent="A4",
        village_code=spike,
        topic="排隊等候",
        count=6,
        baseline=0.5,
        ratio=12.0,
    ).data
    assert data is not None
    with session_scope(DbRole.ANON_READER) as s:
        assert s.get_one(SentimentAlert, data["alert_id"]).status == "open"
    [event] = [d.event for d in bus.recent(5) if d.event["type"] == "sentiment.alert"]
    assert "user_id" not in event and event["village_code"] == spike


def test_briefing_separates_facts_and_stance() -> None:
    md = agency_data(
        "briefing_pack",
        agent="A2",
        topic="北區血壓未控制率",
        facts=[{"claim": "光明里近 4 週未控制率上升", "evidence": "Q-0003"}],
        stance="建議增開光明里量測時段",
    )["markdown"]
    assert "## 一、事實" in md and "Q-0003" in md and "需承辦核定" in md


def test_care_rules_outputs_codes_only(wang: Person, bus: EventBus) -> None:
    data = agency("care_rules", agent="A2").data
    assert data is not None
    mine = [c for c in data["care_tasks"] if c["rule_id"] == "R-BP-L3"]
    assert mine and set(mine[0]) == {"code", "task_type", "rule_id", "site_id"}
    with session_scope(DbRole.IDN_APP) as s:
        task = s.scalars(select(CareTask).where(CareTask.user_id == wang.user_id)).one()
        assert task.code in {c["code"] for c in mine}
    events = [d.event for d in bus.recent(10) if d.event["type"] == "care.list.ready"]
    assert events and all("user_id" not in e for e in events)
    again = agency("care_rules", agent="A2").data
    assert again is not None and wang.user_id not in [c.get("user_id") for c in again["care_tasks"]]
    with session_scope(DbRole.IDN_APP) as s:
        assert len(s.scalars(select(CareTask).where(CareTask.user_id == wang.user_id)).all()) == 1


def test_site_tools_decode_guide_and_stubs(factory: Factory) -> None:
    user = factory.user(name="林測試")
    task = factory.care_task(user.id)
    staff = factory.account("site_staff", site_id=TEST_SITE)
    with session_scope(DbRole.IDN_APP) as s:
        ctx = ToolContext(
            agent="A5", db=s, actor_type="staff", actor_id=str(staff.id), site_id=TEST_SITE
        )
        decoded = registry.call(
            "decode_care_list", {"code": task.code, "task_ref": "T-1"}, ctx
        ).data
        guide = registry.call("visit_guide", {"task_type": "phone_followup"}, ctx).data
        stub = registry.call("dispatch", {"anything": 1}, ctx).data
    assert decoded is not None and decoded["display_name"] == "林○試"
    assert guide is not None and len(guide["steps"]) == 3
    assert stub == {
        "status": "not_implemented",
        "tool": "dispatch",
        "message": "此功能於二期提供，一期僅記錄呼叫",
    }
    assert [r.decision for r in audit_rows(tool="dispatch", actor_id=str(staff.id))] == ["executed"]
