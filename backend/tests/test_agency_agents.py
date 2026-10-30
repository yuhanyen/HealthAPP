"""機關側 Agent（SPEC §8.2、§8.3、§15 情境 4、6）：A2 委派 A3／A4、證據驗證、警訊對照、關懷清單。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select, text

from app.agents import a2, a3, a4, agency
from app.agents.delegate import delegate
from app.agents.runtime import DoneEvent, TextEvent, ToolEvent
from app.db.engines import DbRole, session_scope
from app.db.models_anon import CareListDaily, CohortWeekly, SentimentAlert, SentimentItem
from app.db.models_sys import AnalysisQuery
from app.events.bus import EventBus
from app.services import artifacts
from tests.helpers import audit_rows

# 遠未來週：查詢以「最新一週」為基準，避免與其他測試的群體資料混在一起
BASE = date(2099, 1, 5)
WEEKS = [BASE + timedelta(weeks=i) for i in range(13)]  # 最後一週視為「本週」（不完整、不納入）


@dataclass
class Scenario:
    rising: str
    rising_name: str
    stable: str


def _square(lng: float, lat: float) -> str:
    ring = [[lng, lat], [lng + 0.01, lat], [lng + 0.01, lat + 0.01], [lng, lat + 0.01]]
    return json.dumps({"type": "Polygon", "coordinates": [[*ring, ring[0]]]})


@pytest.fixture
def scenario() -> Iterator[Scenario]:
    tag = uuid.uuid4().hex[:5].upper()
    rising, stable = f"ZA{tag}", f"ZB{tag}"
    name = f"甲{tag}里"
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text(
                "INSERT INTO pub.villages (code, name, district, population, geom) VALUES "
                "(:a, :an, '測試區', 3000, :ga), (:b, :bn, '測試區', 2000, :gb)"
            ),
            {
                "a": rising,
                "an": name,
                "ga": _square(121.0, 25.0),
                "b": stable,
                "bn": f"乙{tag}里",
                "gb": _square(121.03, 25.0),
            },
        )
    cells = []
    for i, week in enumerate(WEEKS):
        late = i >= 8  # 最近 4 個完整週＋本週
        for village, unc, freq, n in (
            (
                rising,
                Decimal("0.62") if late else Decimal("0.40"),
                Decimal("0.08") if late else Decimal("0.20"),
                20,
            ),
            (stable, Decimal("0.40"), Decimal("0.20"), 3),  # n < k：會被遮蔽
            ("ALL", Decimal("0.45"), Decimal("0.18"), 60),
        ):
            for metric, value in (
                ("bp_uncontrolled_rate", unc),
                ("measure_freq", freq),
                ("site_usage", freq),
            ):
                cells.append(
                    CohortWeekly(
                        week=week,
                        village_code=village,
                        age_band="ALL",
                        sex="ALL",
                        metric=metric,
                        value=value,
                        n=n,
                    )
                )
    now = datetime.now(UTC)
    items = [
        SentimentItem(
            collected_at=now - timedelta(days=d, hours=1),
            source_class=src,
            source_url=f"https://{src}.example.tw/{tag}/{d}",
            village_code=rising,
            topic="據點開放",
            sentiment="negative",
            intensity=Decimal("0.6"),
            summary=f"{name}健康站關閉，量血壓要跑很遠（{d}）",
        )
        for d, src in ((1, "forum"), (2, "news"), (3, "forum"), (4, "google_places"))
    ]
    with session_scope(DbRole.ANON_WRITER) as s:
        s.add_all(cells)
        s.add_all(items)
    yield Scenario(rising, name, stable)
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(CohortWeekly).where(CohortWeekly.week.in_(WEEKS)))
        s.execute(delete(SentimentItem).where(SentimentItem.village_code.in_([rising, stable])))
        s.execute(delete(SentimentAlert).where(SentimentAlert.village_code.in_([rising, stable])))
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text("DELETE FROM pub.villages WHERE code IN (:a, :b)"), {"a": rising, "b": stable}
        )


def spec_a3(s: Scenario, **extra: Any) -> dict[str, Any]:
    return {
        "question": "未控制率為什麼上升",
        "village_codes": [s.rising, s.stable],
        "metrics": ["bp_uncontrolled_rate", "measure_freq", "site_usage"],
        "weeks": 12,
        **extra,
    }


def test_a3_returns_queries_charts_and_reproducible_report(scenario: Scenario) -> None:
    result = delegate("A3", spec_a3(scenario), requested_by="A2:test")
    assert result["status"] == "accepted", result["problems"]
    assert len(result["queries"]) == 3 and all(q.startswith("Q-") for q in result["queries"])
    assert result["masked_rows"] > 0  # 小里被遮蔽、不補值
    rising = [f for f in result["findings"] if f.get("village_code") == scenario.rising]
    assert rising and all("Q-" in f["evidence"] for f in result["findings"])
    assert any("下降" in f["claim"] for f in rising)  # 量測頻率同步下降
    assert result["data_cutoff"] == (WEEKS[-2] + timedelta(days=6)).isoformat()
    path = artifacts.path_of(result["report"])
    assert path is not None
    html = path.read_text(encoding="utf-8")
    assert (
        "data:image/svg+xml;base64," in html and "anon.cohort_weekly" in html and "pearson" in html
    )
    with session_scope(DbRole.IDN_APP) as s:
        logged = s.scalars(
            select(AnalysisQuery.agent).where(AnalysisQuery.query_id.in_(result["queries"]))
        ).all()
    assert logged == ["A3", "A3", "A3"]


def test_a4_timeline_every_item_has_source(scenario: Scenario) -> None:
    result = delegate(
        "A4",
        {"question": "最近發生什麼事", "village_codes": [scenario.rising], "days": 14},
        requested_by="A2:test",
    )
    assert result["status"] == "accepted"
    assert len(result["timeline"]) == 4 and all(e["source_url"] for e in result["timeline"])
    [trend] = [t for t in result["trends"] if t["village_code"] == scenario.rising]
    assert trend["is_alert"] and len(trend["source_classes"]) == 3


def test_delegation_without_evidence_is_rejected(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(a3, "run", lambda task: {"findings": [{"claim": "上升了"}], "queries": []})
    result = delegate("A3", spec_a3(scenario), requested_by="A2:test")
    assert result["status"] == "rejected" and len(result["problems"]) == 2
    monkeypatch.setattr(
        a4,
        "run",
        lambda task: {"timeline": [{"date": "2099-01-01", "event": "傳聞", "source_url": ""}]},
    )
    assert delegate("A4", {"village_codes": []}, requested_by="A2:test")["status"] == "rejected"
    assert audit_rows(action="agent.delegate", decision="rejected")


def _chat(question: str) -> list[Any]:
    return list(a2.chat("A2:test", question))


def test_a2_answers_why_with_evidence_and_approval(scenario: Scenario) -> None:
    events = _chat(f"{scenario.rising_name}高血壓未控制率為什麼上升？")
    assert [(e.tool, e.status) for e in events if isinstance(e, ToolEvent)] == [
        ("delegate_a3", "ok"),
        ("delegate_a4", "ok"),
    ]
    text = "".join(e.text for e in events if isinstance(e, TextEvent))
    for part in (
        "【問題】",
        "【觀察】",
        "【外部事件】",
        "【可能原因】",
        "【建議】",
        "【不確定之處】",
    ):
        assert part in text
    assert "Q-" in text and ".example.tw/" in text  # 數字附查詢編號、事件附來源
    assert f"{scenario.rising} 未控制率上升" in text and "可近性" in text
    assert text.rstrip().endswith("以上為分析建議，需承辦核定後執行。")
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.meta and done.meta["report"]
    # 稽核：A2 委派、A3 每個工具、A4 每個工具都留痕
    assert audit_rows(tool="delegate_a3", actor_id="A2:test")
    assert any(r.actor_id.startswith("A3:D-") for r in audit_rows(tool="sandbox_sql"))


def test_a2_declines_individual_questions() -> None:
    events = _chat("這位阿伯的電話是多少？")
    assert not [e for e in events if isinstance(e, ToolEvent)]
    assert "個人層級" in "".join(e.text for e in events if isinstance(e, TextEvent))


def test_a2_briefing_is_marked_draft(scenario: Scenario) -> None:
    events = _chat(f"議會要質詢{scenario.rising_name}據點關閉，幫我準備談參")
    tools = [e.tool for e in events if isinstance(e, ToolEvent)]
    assert tools == ["delegate_a3", "delegate_a4", "briefing_pack"]
    text = "".join(e.text for e in events if isinstance(e, TextEvent))
    assert "## 一、事實" in text and "Q-" in text
    assert text.rstrip().endswith("本談參為草稿，發言內容由主管定稿。")


def test_a2_resource_simulation_states_assumptions(scenario: Scenario) -> None:
    events = _chat("要在哪裡加開巡迴量測點？")
    tools = [e.tool for e in events if isinstance(e, ToolEvent)]
    assert tools[0] == "cohort_analysis"
    text = "".join(e.text for e in events if isinstance(e, TextEvent))
    if "resource_sim" in tools:
        assert "假設與範圍" in text and "需承辦核定" in text


def test_a4_schedule_emits_alert_and_a2_checks_it(scenario: Scenario, bus: EventBus) -> None:
    emitted = a4.scheduled_run()
    with session_scope(DbRole.ANON_READER) as s:
        alerts = s.scalars(
            select(SentimentAlert).where(SentimentAlert.village_code == scenario.rising)
        ).all()
    assert len(alerts) == 1 and alerts[0].id in emitted and alerts[0].category == "service_gap"
    [event] = [
        d.event
        for d in bus.recent(20)
        if d.event["type"] == "sentiment.alert" and d.event["village_code"] == scenario.rising
    ]
    assert "user_id" not in event
    # 3 天內同里同主題不重發
    assert scenario.rising not in [a.village_code for a in _alerts(a4.scheduled_run())]
    analysis = a2.analyze_alert(alerts[0].id)
    assert analysis["consistent"] is True and analysis["queries"]
    with session_scope(DbRole.ANON_READER) as s:
        saved = s.get_one(SentimentAlert, alerts[0].id)
        assert saved.status == "analyzed" and saved.analysis and saved.analysis["approval"]


def _alerts(ids: list[int]) -> list[SentimentAlert]:
    with session_scope(DbRole.ANON_READER) as s:
        rows = s.scalars(select(SentimentAlert).where(SentimentAlert.id.in_(ids))).all()
        for row in rows:
            s.expunge(row)
        return list(rows)


def test_daily_care_list_publishes_only_k_or_more(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2098, 5, 1)
    tasks = [{"site_id": "S-BIG", "task_type": "revisit_reminder"}] * 6 + [
        {"site_id": "S-SMALL", "task_type": "phone_followup"}
    ] * 2
    monkeypatch.setattr(agency, "call", lambda *a, **k: {"care_tasks": tasks})
    result = a2.daily_care_list(day)
    assert result["count"] == 8
    with session_scope(DbRole.ANON_READER) as s:
        rows = {
            (r.site_id, r.task_type): r.n
            for r in s.scalars(select(CareListDaily).where(CareListDaily.day == day))
        }
    assert rows == {
        ("S-BIG", "revisit_reminder"): 6,
        ("ALL", "revisit_reminder"): 6,
        ("ALL", "ALL"): 8,
    }
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(CareListDaily).where(CareListDaily.day == day))
