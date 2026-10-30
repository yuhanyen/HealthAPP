"""A1 個人數位雙生（SPEC §8.2、§8.4；行為見 skills/a1-personal-digital-twin/SKILL.md）。

- 每日批次（05:00 `daily.batch`）：依 SKILL「每日批次」固定步驟經工具層組報告，
  不呼叫 LLM（見 DECISIONS）。
- 07:00 推送：每份未送出的報告呼叫 notify。
- 異常事件（`measurement.created`）：risk_score 確認連續性 → 即時提醒＋最近可預約時段。
- 對話：agents/runtime（Claude 或規則式 mock），身分與授權範圍由 runtime 注入。

所有工具呼叫都經 registry（C-3 授權、C-5 確認、C-6 稽核）；
連線一律 idn_user_scoped（RLS 只見本人）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.agents import runtime
from app.agents.mock_a1 import MockA1Planner
from app.config import get_settings
from app.consent.service import effective_scope
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_idn import ChatMessage, DailyReport, Notification, TwinSnapshot
from app.db.models_sys import Account
from app.i18n import t, t_list
from app.services import health_data as hd
from app.services import prefs
from app.sysconfig import cached_value
from app.tools import CITIZEN_TOOLS
from app.tools._common import md, profile, safe, today, tz
from app.tools.registry import ConsentScopeError, ToolContext, registry

log = logging.getLogger(__name__)

AGENT = runtime.AGENTS["A1"]
HISTORY_TURNS = 12
LEVELS = ("L0", "L1", "L2", "L3")


@contextmanager
def tool_context(user_id: uuid.UUID) -> Iterator[ToolContext]:
    """A1 的每次工具呼叫：一個 idn_user_scoped 交易，RLS 只放行本人。"""
    with user_scoped_session(user_id) as s:
        yield ToolContext(
            agent="A1",
            db=s,
            principal_user_id=user_id,
            allowed_tools=frozenset(AGENT.allowed_tools),
        )


def current_scope(user_id: uuid.UUID) -> dict[str, list[str]]:
    with user_scoped_session(user_id) as s:
        return effective_scope(s, user_id)


@dataclass
class Caller:
    """批次流程用：以本人身分與授權範圍呼叫工具；被拒絕（例如未授權）就當作沒有這項資料。"""

    user_id: uuid.UUID
    scope: dict[str, list[str]]
    rejected: list[str] = field(default_factory=list)

    def __call__(self, tool: str, **args: Any) -> dict[str, Any] | None:
        assert tool in CITIZEN_TOOLS
        with tool_context(self.user_id) as ctx:
            try:
                result = registry.call(
                    tool, {**args, "user_id": self.user_id, "consent_scope": self.scope}, ctx
                )
            except ConsentScopeError:
                self.rejected.append(tool)
                return None
        return result.data


# --- 雙生（SPEC §8.4） ---


def build_twin(
    user_id: uuid.UUID,
    day: date,
    scope: dict[str, list[str]],
    risk: dict[str, Any],
    *,
    missing: list[str],
) -> dict[str, Any]:
    evidence = risk.get("evidence", {})
    with user_scoped_session(user_id) as s:
        me = profile(s, user_id, day)
        conditions = hd.condition_codes(s, user_id) if "conditions" in scope.get("nhi", []) else []
        meds = hd.medications(s, user_id) if "medications" in scope.get("nhi", []) else []
        last_chat = s.scalar(
            select(ChatMessage.created_at)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
    return {
        "as_of": day.isoformat(),
        "vitals": {
            "bp_last3": [[r["systolic"], r["diastolic"]] for r in evidence.get("bp_last", [])],
            "steps_14d_avg": evidence.get("steps_14d_avg"),
            "steps_prev14d_avg": evidence.get("steps_prev14d_avg"),
        },
        "conditions": sorted(set(conditions)),
        "medications": meds,
        "risk": {"level": risk.get("level", "L0"), "triggers": risk.get("triggers", [])},
        "prefs": {"elder_mode": me.elder_mode, "channel": "push", "exercise_time": "morning"},
        "profile": {"age_band": me.age_band, "village_code": me.village_code},
        "consent_scope": scope,
        "missing_sources": missing,
        "last_dialog_summary": t("a1.twin.last_dialog", date=md(last_chat)) if last_chat else None,
    }


def save_twin(user_id: uuid.UUID, day: date, state: dict[str, Any]) -> None:
    with user_scoped_session(user_id) as s:
        stmt = insert(TwinSnapshot).values(user_id=user_id, as_of=day, state=state)
        s.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "as_of"], set_={"state": stmt.excluded.state}
            )
        )


def latest_twin(user_id: uuid.UUID) -> TwinSnapshot | None:
    with user_scoped_session(user_id) as s:
        twin = s.scalars(
            select(TwinSnapshot)
            .where(TwinSnapshot.user_id == user_id)
            .order_by(TwinSnapshot.as_of.desc())
            .limit(1)
        ).first()
        if twin is not None:
            s.expunge(twin)
        return twin


# --- 每日批次（SKILL「每日批次」1–9） ---


def slot_text(slot: str) -> str:
    at = datetime.fromisoformat(slot).astimezone(tz())
    weekday = t_list("a1.weekday")[at.weekday()]
    return f"{at.month}/{at.day}（{weekday}）{at:%H:%M}"


def _projection(user_id: uuid.UUID, day: date, goal: int) -> dict[str, Any] | None:
    """在雙生上試算：若今天完成步數目標，近 7 日平均的變化。"""
    with user_scoped_session(user_id) as s:
        steps = hd.daily_steps(s, user_id, day - timedelta(days=6), day)
    if len(steps) < 3:
        return None
    current = sum(steps.values()) / len(steps)
    projected = (sum(steps.values()) + goal) / (len(steps) + 1)
    if projected <= current:
        return None
    return {
        "goal": goal,
        "current_avg": round(current),
        "projected_avg": round(projected),
        "text": t(
            "a1.report.projection",
            goal=f"{goal:,}",
            current=f"{round(current):,}",
            projected=f"{round(projected):,}",
        ),
    }


def _rx_basis(rx: dict[str, Any]) -> str:
    if rx.get("basis"):
        return "、".join(rx["basis"])
    env = rx.get("env") or {}
    if env.get("aqi") is not None and env.get("heat_index") is not None:
        return t("a1.report.basis_env", aqi=env["aqi"], heat=round(env["heat_index"]))
    return t("a1.report.basis_steps")


def _priority(level: str, triggers: list[str]) -> str:
    if LEVELS.index(level) >= 2:
        return "safety"
    if "no_revisit_30d" in triggers:
        return "revisit"
    return "exercise"


def run_daily(user_id: uuid.UUID, day: date | None = None) -> dict[str, Any] | None:
    """一位民眾的每日批次。沒有任何授權就不產生報告。回傳報告內容。"""
    day = day or today()
    scope = current_scope(user_id)
    if not scope:
        return None
    call = Caller(user_id, scope)

    # 1–2. 風險（雙生的核心狀態）
    risk = call("risk_score") or {"level": "L0", "triggers": [], "evidence": {}}
    level = risk["level"]
    priority = _priority(level, risk["triggers"])

    # 4. 運動處方（依就醫紀錄與天氣，工具內已判斷保守版本與室內外）
    rx = call("exercise_rx")
    recommendation = None
    projection = None
    if rx:
        recommendation = {
            "text": t("a1.report.line.recommendation", plan=rx["plan"], basis=_rx_basis(rx)),
            "intensity": rx["intensity"],
            "minutes": rx["minutes"],
            "venue": rx["venue"],
            "step_goal": rx["step_goal"],
            "contraindications": rx["contraindications"],
        }
        if "steps" in scope.get("wearable", []):
            projection = _projection(user_id, day, rx["step_goal"])

    # 5. 需要複測時只查時段、不預約，讓本人決定
    decision = None
    if priority == "safety":
        slots = call("site_booking", mode="query", days=2)
        if slots and slots["slots"]:
            first = slots["slots"][:3]
            decision = {
                "kind": "booking",
                "site_id": slots["site_id"],
                "site_name": slots["site_name"],
                "slots": first,
                "text": t(
                    "a1.report.line.decision_booking",
                    when=slot_text(first[0]),
                    site=slots["site_name"],
                ),
            }

    # 6. 點數：昨日達標發點、到期提醒；有到期點數再找商家
    yesterday = day - timedelta(days=1)
    evaluated = call("points_engine", action="evaluate", day=yesterday.isoformat())
    status = call("points_engine", action="status") or {"balance": 0, "expiring": []}
    merchants: list[dict[str, Any]] = []
    if status["expiring"]:
        found = call("merchant_lookup", points=status["balance"])
        merchants = (found or {}).get("merchants", [])[:2]
    points_text = t("a1.report.line.points", balance=status["balance"])
    if evaluated and evaluated.get("granted"):
        points_text += t("a1.report.points_granted", points=evaluated["granted"])
    if status["expiring"]:
        soon = status["expiring"][0]
        points_text += t(
            "a1.report.points_expiring",
            points=soon["points"],
            date=md(date.fromisoformat(soon["expires_on"])),
        )
        if merchants:
            points_text += t(
                "a1.report.points_merchants", merchants="、".join(m["name"] for m in merchants)
            )

    # 6b. 這週附近的活動（SPEC §17.4；理由只在 App 內顯示）
    found_events = (call("event_recommend", days=7, limit=3) or {}).get("events", [])
    events = [
        {
            k: e[k]
            for k in (
                "event_id",
                "title",
                "when",
                "venue",
                "points_reward",
                "reasons",
                "push_title",
                "push_body",
            )
        }
        for e in found_events
        if e.get("registration") is None
    ]

    # 7. 組裝報告（daily_report 工具＋上面各步結論）
    report = call("daily_report", date=day.isoformat())
    if report is None:
        return None
    missing = [m["metric"] for m in report["metrics"] if m.get("value") is None]
    lines = [t("a1.report.line.metric", text=m["text"]) for m in report["metrics"]]
    if recommendation:
        lines.append(recommendation["text"])
    if projection:
        lines.append(t("a1.report.line.projection", text=projection["text"]))
    if decision:
        lines.append(decision["text"])
    lines.append(points_text)
    if events:
        top = events[0]
        lines.append(
            t(
                "a1.report.line.event",
                title=top["title"],
                when=top["when"],
                venue=top["venue"],
                reason=top["reasons"][0] if top["reasons"] else "",
            )
        )
    lines = [safe(line) for line in lines]
    text = "\n".join([report["title"], t("a1.report.status", text=report["status_sentence"])])
    content = {
        **report,
        "priority": priority,
        "recommendation": recommendation,
        "projection": projection,
        "decision": decision,
        "points": {
            "balance": status["balance"],
            "expiring": status["expiring"],
            "granted": (evaluated or {}).get("granted", 0),
            "merchants": merchants,
        },
        "events": events,
        "lines": lines,
        "text": "\n".join([text, *lines]),
        "sources_used": sorted(scope),
        "missing_sources": missing,
        "skipped_tools": call.rejected,
    }

    # 1. 更新雙生快照；7. 存報告（同一天重跑時覆寫，送出時間保留）
    save_twin(user_id, day, build_twin(user_id, day, scope, risk, missing=missing))
    with user_scoped_session(user_id) as s:
        stmt = insert(DailyReport).values(user_id=user_id, report_date=day, content=content)
        s.execute(
            stmt.on_conflict_do_update(
                index_elements=["user_id", "report_date"], set_={"content": stmt.excluded.content}
            )
        )
    return content


@dataclass
class BatchSummary:
    day: str
    users: int = 0
    reports: int = 0
    skipped: int = 0
    failed: int = 0


def app_users() -> list[uuid.UUID]:
    """有 App 帳號的民眾（背景居民沒有帳號，不跑 A1）。服務連線只取 user_id 清單。"""
    with session_scope(DbRole.IDN_APP) as s:
        return list(
            s.scalars(
                select(Account.user_id).where(
                    Account.role == "citizen", Account.user_id.is_not(None), Account.active
                )
            )
        )


def run_daily_batch(
    day: date | None = None,
    user_ids: list[uuid.UUID] | None = None,
    parallel: int | None = None,
) -> BatchSummary:
    day = day or today()
    users = user_ids if user_ids is not None else app_users()
    summary = BatchSummary(day=day.isoformat(), users=len(users))

    def one(user_id: uuid.UUID) -> str:
        try:
            return "ok" if run_daily(user_id, day) is not None else "skipped"
        except Exception:
            log.exception("[A1] %s 每日批次失敗", user_id)
            return "failed"

    workers = parallel or get_settings().a1_parallelism
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="a1") as pool:
        for outcome in pool.map(one, users):
            if outcome == "ok":
                summary.reports += 1
            elif outcome == "skipped":
                summary.skipped += 1
            else:
                summary.failed += 1
    log.info("[A1] 每日批次 %s：%s", day, summary)
    return summary


# --- 07:00 推送（SKILL 步驟 8） ---


def undelivered(day: date) -> list[uuid.UUID]:
    with session_scope(DbRole.IDN_APP) as s:
        return list(
            s.scalars(
                select(DailyReport.user_id).where(
                    DailyReport.report_date == day, DailyReport.delivered_at.is_(None)
                )
            )
        )


def deliver(user_id: uuid.UUID, day: date) -> bool:
    scope = current_scope(user_id)
    if not scope:
        return False
    with tool_context(user_id) as ctx:
        report = ctx.db.scalars(
            select(DailyReport).where(
                DailyReport.user_id == user_id, DailyReport.report_date == day
            )
        ).one()
        content = report.content
        body = "\n".join([content["status_sentence"], *content["lines"][:2]])
        registry.call(
            "notify",
            {
                "user_id": user_id,
                "consent_scope": scope,
                "kind": "daily_report",
                "title": content["title"],
                "body": body[:480],
                "report_date": day.isoformat(),
            },
            ctx,
        )
        ctx.db.execute(
            update(DailyReport)
            .where(DailyReport.id == report.id)
            .values(delivered_at=datetime.now(tz()))
        )
        push_event(ctx, user_id, scope, content.get("events") or [])
    return True


def push_event(
    ctx: ToolContext, user_id: uuid.UUID, scope: dict[str, list[str]], events: list[dict[str, Any]]
) -> bool:
    """推播最推薦的一場活動（SPEC §17.4）：只有名稱、時間、地點、點數；每週上限；本人可關閉。"""
    if not events or not prefs.get_prefs(ctx.db, user_id).event_push:
        return False
    week_ago = datetime.now(tz()) - timedelta(days=7)
    pushed = list(
        ctx.db.scalars(
            select(Notification.payload).where(
                Notification.user_id == user_id,
                Notification.kind == "event",
                Notification.created_at >= week_ago,
            )
        )
    )
    if len(pushed) >= int(cached_value("event_push_weekly_cap", 2)):
        return False
    seen = {(p or {}).get("event_id") for p in pushed}
    top = next((e for e in events if e["event_id"] not in seen), None)
    if top is None:
        return False
    registry.call(
        "notify",
        {
            "user_id": user_id,
            "consent_scope": scope,
            "kind": "event",
            "title": top["push_title"][:60],
            "body": top["push_body"][:480],
            "event_id": top["event_id"],
        },
        ctx,
    )
    return True


def deliver_reports(day: date | None = None) -> int:
    day = day or today()
    sent = 0
    for user_id in undelivered(day):
        try:
            sent += deliver(user_id, day)
        except Exception:
            log.exception("[A1] %s 報告推送失敗", user_id)
    log.info("[A1] %s 已推送 %d 份每日報告", day, sent)
    return sent


# --- 異常事件（SKILL「異常事件觸發」） ---


def on_measurement(user_id: uuid.UUID) -> bool:
    """新的據點量測：若連續偏高達 L2 以上，且今天還沒提醒過，立即通知本人並附可預約時段。"""
    scope = current_scope(user_id)
    if "bp" not in scope.get("site", []):
        return False
    call = Caller(user_id, scope)
    risk = call("risk_score")
    if risk is None or LEVELS.index(risk["level"]) < 2:
        return False
    start_of_day = datetime.combine(today(), datetime.min.time(), tz())
    with user_scoped_session(user_id) as s:
        already = s.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id,
                Notification.kind == "alert",
                Notification.created_at >= start_of_day,
            )
        )
    if already:
        return False
    readings = len(risk["evidence"].get("bp_last", []))
    slots = call("site_booking", mode="query", days=2)
    body = t("a1.alert.body", n=readings)
    if slots and slots["slots"]:
        body += t("a1.alert.slot", when=slot_text(slots["slots"][0]), site=slots["site_name"])
    call("notify", kind="alert", title=t("a1.alert.title"), body=body)
    return True


# --- 對話 ---


def history(user_id: uuid.UUID, limit: int = HISTORY_TURNS) -> list[ChatMessage]:
    with user_scoped_session(user_id) as s:
        rows = list(
            s.scalars(
                select(ChatMessage)
                .where(ChatMessage.user_id == user_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
        )
        for row in rows:
            s.expunge(row)
    return rows[::-1]


def _save_turn(
    user_id: uuid.UUID, role: str, content: str, meta: dict[str, Any] | None = None
) -> None:
    with user_scoped_session(user_id) as s:
        s.add(ChatMessage(user_id=user_id, role=role, content=content, meta=meta))


def context_text(user_id: uuid.UUID, scope: dict[str, list[str]]) -> str:
    """接在 SKILL.md 後面的執行期情境。不含姓名、電話、地址、身分證。"""
    twin = latest_twin(user_id)
    with user_scoped_session(user_id) as s:
        me = profile(s, user_id)
    return t(
        "a1.context",
        today=today().isoformat(),
        age_band=me.age_band,
        elder=t("a1.yes") if me.elder_mode else t("a1.no"),
        scope=json.dumps(scope, ensure_ascii=False),
        twin=json.dumps(twin.state if twin else {}, ensure_ascii=False),
    )


def alternate(turns: list[ChatMessage]) -> list[dict[str, Any]]:
    """Messages API 要求 user／assistant 交替且由 user 開頭：合併相鄰同角色（例如上次中斷）。"""
    out: list[dict[str, Any]] = []
    for m in turns:
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n" + m.content
        elif out or m.role == "user":
            out.append({"role": m.role, "content": m.content})
    if out and out[-1]["role"] == "user":
        out.append({"role": "assistant", "content": "…"})
    return out


def chat(user_id: uuid.UUID, message: str) -> Iterator[runtime.AgentEvent]:
    """一輪對話。先存使用者訊息，結束時存 A1 回覆（含待決定方案 meta）。"""
    scope = current_scope(user_id)
    past = history(user_id)
    last_meta = next((m.meta for m in reversed(past) if m.role == "assistant" and m.meta), None)
    _save_turn(user_id, "user", message)
    rc = runtime.RunContext(
        agent=AGENT,
        tool_context=lambda: tool_context(user_id),
        context_text=context_text(user_id, scope),
        actor_id=f"A1:{user_id}",
        inject={"user_id": str(user_id), "consent_scope": scope},
    )
    planner = runtime.pick_planner(AGENT, lambda: MockA1Planner(last_meta=last_meta))
    for event in runtime.run(rc, history=alternate(past), user_message=message, planner=planner):
        if isinstance(event, runtime.DoneEvent):
            _save_turn(user_id, "assistant", event.text or "…", event.meta)
        yield event
