"""M5 API（SPEC §9、§15 情境 5）。

- 據點端：工作板、代號清單、解碼、訪視回報 → anon.visit_outcomes
- 機關端：總覽、警訊、KPI、A2 對話、談參
- 新工具：sentiment_search／delegate_a3
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.auth.security import create_access_token
from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentItem, VisitOutcome
from app.deidentify.aggregate import run_deidentify, week_start
from app.tools.registry import ToolContext, ToolRejected, registry
from tests.conftest import Factory
from tests.fixtures import TODAY
from tests.helpers import OTHER_SITE, TEST_SITE, TEST_VILLAGE, audit_rows, auth

API = "/api/v1"


def staff_token(factory: Factory, site: str = TEST_SITE) -> str:
    return create_access_token(factory.account("site_staff", site_id=site))


def test_site_board_and_care_list_are_own_site_only(client: TestClient, factory: Factory) -> None:
    user = factory.user()
    task = factory.care_task(user.id)
    token = staff_token(factory)
    board = client.get(f"{API}/sites/{TEST_SITE}/today", headers=auth(token)).json()
    assert board["site_name"] == "測試據點" and board["care_tasks"]["total"] >= 1
    tasks = client.get(f"{API}/sites/{TEST_SITE}/care-tasks", headers=auth(token)).json()
    mine = [t for t in tasks if t["code"] == task.code]
    assert mine and set(mine[0]) == {
        "code",
        "task_type",
        "rule_id",
        "status",
        "created_at",
        "decoded",
    }
    assert (
        client.get(f"{API}/sites/{OTHER_SITE}/care-tasks", headers=auth(token)).status_code == 403
    )


def test_decode_needs_task_ref_and_returns_no_health_data(
    client: TestClient, factory: Factory
) -> None:
    user = factory.user(name="林測試")
    task = factory.care_task(user.id)
    token = staff_token(factory)
    url = f"{API}/sites/{TEST_SITE}/care-tasks/{task.code}/decode"
    assert client.post(url, json={"task_ref": ""}, headers=auth(token)).status_code == 422
    body = client.post(url, json={"task_ref": "T-1030-01"}, headers=auth(token)).json()
    assert body["name"] == "林測試" and body["display_name"] == "林○試" and body["visit_guide"]
    forbidden = {"bp", "systolic", "risk", "conditions", "steps", "medications"}
    assert not forbidden & set(body)
    assert audit_rows(tool="decode_care_list", decision="executed")
    # 他據點的人員不能解碼
    other = staff_token(factory, OTHER_SITE)
    resp = client.post(
        f"{API}/sites/{OTHER_SITE}/care-tasks/{task.code}/decode",
        json={"task_ref": "T-1"},
        headers=auth(other),
    )
    assert resp.status_code == 403


def test_visit_reports_reach_anon_only_when_k_or_more(client: TestClient, factory: Factory) -> None:
    token = staff_token(factory)
    week = week_start(TODAY)
    tasks = [factory.care_task(factory.user().id) for _ in range(5)]

    def report(code: str) -> None:
        resp = client.post(
            f"{API}/sites/{TEST_SITE}/care-tasks/{code}/report",
            json={
                "task_ref": "T-9",
                "reason_class": "distance",
                "needs": ["替代量測點"],
                "note": "阿伯說活動中心血壓計壞了",
            },
            headers=auth(token),
        )
        assert resp.status_code == 200, resp.text

    def outcomes() -> dict[str, int]:
        with session_scope(DbRole.ANON_READER) as s:
            return {
                r.village_code: r.n
                for r in s.scalars(
                    select(VisitOutcome).where(
                        VisitOutcome.week == week,
                        VisitOutcome.task_type == "phone_followup",
                        VisitOutcome.reason_class == "distance",
                    )
                )
            }

    for task in tasks[:4]:
        report(task.code)
    run_deidentify(weeks=[week], today=TODAY)
    assert outcomes() == {}  # 4 < k：不公開
    report(tasks[4].code)
    run_deidentify(weeks=[week], today=TODAY)
    published = outcomes()
    assert published[TEST_VILLAGE] >= 5 and published["ALL"] >= 5
    bad = client.post(
        f"{API}/sites/{TEST_SITE}/care-tasks/{tasks[0].code}/report",
        json={"task_ref": "T-9", "reason_class": "unknown"},
        headers=auth(token),
    )
    assert bad.status_code == 422


@pytest.fixture
def agency_token(factory: Factory) -> str:
    return create_access_token(factory.account("agency"))


def test_agency_overview_and_permissions(
    client: TestClient, factory: Factory, agency_token: str
) -> None:
    _, _, citizen = factory.citizen()
    assert client.get(f"{API}/agency/overview", headers=auth(citizen)).status_code == 403
    body = client.get(f"{API}/agency/overview?metric=steps_avg", headers=auth(agency_token)).json()
    assert body["k"] == 5 and len(body["kpis"]) == 6
    assert {"series", "hotspots"} <= set(body["cohort"]) or body["cohort"] == {}
    assert isinstance(body["alerts"], list) and "rows" in body["care_list"]
    for row in body["cohort"].get("hotspots", {}).get("features", []):
        assert row["properties"]["masked"] or row["properties"]["n"] >= 5
    kpi = client.get(f"{API}/agency/kpi?period=month", headers=auth(agency_token)).json()
    assert kpi["period"] == "month" and kpi["items"]


def _sse(client: TestClient, token: str, message: str) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST", f"{API}/agency/chat", json={"message": message}, headers=auth(token)
    ) as r:
        body = "".join(r.iter_text())
    out = []
    for chunk in body.strip().split("\n\n"):
        kind = re.search(r"^event: (.+)$", chunk, re.M)
        data = re.search(r"^data: (.+)$", chunk, re.M)
        assert kind and data
        out.append((kind.group(1), json.loads(data.group(1))))
    return out


def test_agency_chat_and_briefing(client: TestClient, agency_token: str) -> None:
    events = _sse(client, agency_token, "這位阿伯的地址在哪？")
    assert events[-1][0] == "done"
    assert "個人層級" in "".join(d["text"] for k, d in events if k == "text")
    resp = client.post(
        f"{API}/agency/briefing",
        json={"occasion": "議會", "topic": "北區據點關閉"},
        headers=auth(agency_token),
    ).json()
    assert resp["markdown"] and ("主管定稿" in resp["markdown"] or "需承辦核定" in resp["markdown"])
    assert [t["tool"] for t in resp["tools"]][:2] == ["delegate_a3", "delegate_a4"]


# --- 新工具 ---


@pytest.fixture
def items() -> Any:
    now = datetime.now(UTC)
    rows = [
        SentimentItem(
            collected_at=now - timedelta(days=d),
            source_class="forum",
            source_url=f"https://forum.example.tw/t/s{d}",
            village_code=village,
            topic=topic,
            sentiment="negative",
            intensity=Decimal("0.5"),
            summary="摘要",
        )
        for d, village, topic in (
            (1, "QS1", "據點開放"),
            (2, "QS1", "排隊等候"),
            (40, "QS1", "據點開放"),
            (1, "QS2", "據點開放"),
        )
    ]
    with session_scope(DbRole.ANON_WRITER) as s:
        s.add_all(rows)
    yield
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(SentimentItem).where(SentimentItem.village_code.in_(["QS1", "QS2"])))


def _agency_call(tool: str, agent: str = "A4", **args: Any) -> dict[str, Any]:
    from app.agents.agency import tool_context

    with tool_context(agent, "test") as ctx:
        return registry.call(tool, args, ctx).data or {}


def test_sentiment_search_filters_village_period_topic(items: None) -> None:
    data = _agency_call("sentiment_search", village_codes=["QS1"], days=7)
    urls = {i["source_url"] for i in data["items"] if i["village_code"] == "QS1"}
    assert urls == {"https://forum.example.tw/t/s1", "https://forum.example.tw/t/s2"}
    assert all(i["village_code"] in ("QS1", None) for i in data["items"])
    assert {"user", "author", "author_name"}.isdisjoint(data["items"][0])
    topic = _agency_call("sentiment_search", village_codes=["QS1"], days=7, topic="排隊等候")
    assert [i["topic"] for i in topic["items"] if i["village_code"] == "QS1"] == ["排隊等候"]


def test_agency_tools_are_not_available_to_a1_or_other_agents(factory: Factory) -> None:
    user = factory.user()
    from app.tools import CITIZEN_TOOLS

    with session_scope(DbRole.IDN_USER_SCOPED) as s:
        ctx = ToolContext(
            agent="A1", db=s, principal_user_id=user.id, allowed_tools=frozenset(CITIZEN_TOOLS)
        )
        with pytest.raises(ToolRejected):
            registry.call("sentiment_search", {}, ctx)
    with pytest.raises(ToolRejected):  # A3 不能委派（只有 A2 可以）
        _agency_call("delegate_a4", agent="A3", question="q")


def test_delegate_a3_rejects_unknown_metric() -> None:
    with pytest.raises(ValidationError):
        _agency_call("delegate_a3", agent="A2", question="q", metrics=["income"])
