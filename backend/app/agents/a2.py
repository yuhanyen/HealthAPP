"""A2 公衛 Agent（SPEC §8.2；行為見 skills/a2-public-health-orchestrator/SKILL.md）。

- 承辦提問（/agency/chat，SSE）：runtime（Claude 或規則式 mock），工具含 delegate_a3／delegate_a4。
- 預警事件（sentiment.alert）：委派 A3（該里近 8 週數據）＋A4（同期事件）→ 對照 → 寫入警訊的分析。
- 每日 05:30 關懷代號清單：care_rules（規則在 idn 執行、只回代號）→ 各類數量寫入群體倉儲。
A2 只用 anon_reader（C-2），看不到任何個人；所有建議標示需承辦核定。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterator
from datetime import date
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert

from app.agents import agency, runtime
from app.agents.mock_a2 import MockA2Planner, villages
from app.db.engines import DbRole, session_scope
from app.db.models_anon import CareListDaily, SentimentAlert
from app.i18n import t
from app.sysconfig import cached_value
from app.tools._common import today

log = logging.getLogger(__name__)

AGENT = "A2"
SPEC = runtime.AGENTS[AGENT]


def context_text() -> str:
    names = villages()
    return t(
        "a2.context",
        today=today().isoformat(),
        k=int(cached_value("k", 5)),
        villages="、".join(
            f"{code}={name}" for name, code in sorted(names.items(), key=lambda x: x[1])
        ),
    )


def chat(actor_id: str, message: str) -> Iterator[runtime.AgentEvent]:
    """承辦提問一輪（不保存對話：每個問題獨立、可稽核）。"""
    rc = runtime.RunContext(
        agent=SPEC,
        tool_context=agency.context_factory(AGENT, actor_id),
        context_text=context_text(),
        actor_id=actor_id,
    )
    planner = runtime.pick_planner(SPEC, MockA2Planner)
    yield from runtime.run(rc, history=[], user_message=message, planner=planner)


def analyze_alert(alert_id: int) -> dict[str, Any]:
    """SKILL A2「預警事件」：數字與事件吻合 → 預警摘要與建議；不吻合 → 標記需人工了解。"""
    with session_scope(DbRole.ANON_READER) as s:
        alert = s.get_one(SentimentAlert, alert_id)
        village, topic = alert.village_code, alert.topic
    actor = f"{AGENT}:alert-{alert_id}"
    a3 = agency.call(
        AGENT,
        actor,
        "delegate_a3",
        question=t("a2.alert.question_a3", village=village),
        village_codes=[village],
        metrics=["site_usage", "measure_freq", "bp_uncontrolled_rate"],
        weeks=8,
    )
    a4 = agency.call(
        AGENT,
        actor,
        "delegate_a4",
        question=t("a2.alert.question_a4", village=village, topic=topic),
        village_codes=[village],
        days=56,
    )
    usable3 = a3.get("status") == "accepted"
    usable4 = a4.get("status") == "accepted"
    declining = usable3 and any(
        f.get("direction") == "down" and f.get("metric") in ("site_usage", "measure_freq")
        for f in a3.get("findings", [])
    )
    events = (
        [e for e in a4.get("timeline", []) if e.get("village_code") in (village, None)]
        if usable4
        else []
    )
    consistent = bool(declining and events)
    analysis = {
        "consistent": consistent,
        "summary": t("a2.alert.consistent", village=village, topic=topic)
        if consistent
        else t("a2.alert.inconsistent", village=village, topic=topic),
        "suggestion": t("a2.suggest.access", villages=village)
        if consistent
        else t("a2.alert.manual"),
        "findings": a3.get("findings", []) if usable3 else [],
        "events": events[-5:],
        "queries": a3.get("queries", []),
        "report": a3.get("report"),
        "rejected": [p for r in (a3, a4) for p in r.get("problems", [])],
        "approval": t("a2.approval"),
    }
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(
            update(SentimentAlert)
            .where(SentimentAlert.id == alert_id)
            .values(analysis=json.loads(json.dumps(analysis, default=str)), status="analyzed")
        )
    log.info("[A2] 警訊 %s 對照完成：%s", alert_id, "吻合" if consistent else "不一致")
    return analysis


def daily_care_list(day: date | None = None) -> dict[str, Any]:
    """05:30 關懷代號清單（SKILL A2「每日批次」）。只把 n ≥ k 的數量寫進群體倉儲（不含代號）。"""
    day = day or today()
    result = agency.call(AGENT, f"{AGENT}:care-list", "care_rules", date=day.isoformat())
    k = int(cached_value("k", 5))
    tasks = result.get("care_tasks", [])
    counts = Counter((task["site_id"], task["task_type"]) for task in tasks)
    totals = Counter(task["task_type"] for task in tasks)
    rows = [{"site_id": s, "task_type": tt, "n": n} for (s, tt), n in counts.items() if n >= k]
    rows += [{"site_id": "ALL", "task_type": tt, "n": n} for tt, n in totals.items() if n >= k]
    if len(tasks) >= k:
        rows.append({"site_id": "ALL", "task_type": "ALL", "n": len(tasks)})
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(CareListDaily).where(CareListDaily.day == day))
        if rows:
            s.execute(insert(CareListDaily), [{"day": day, **row} for row in rows])
    return {"day": day.isoformat(), "count": len(tasks), "published_rows": len(rows)}
