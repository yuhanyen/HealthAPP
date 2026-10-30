"""event_register（SPEC §17.3）：報名社區活動（高風險：本人確認後才執行）、取消報名。

報名會把姓名與聯絡電話交給主辦單位（據點人員報到用），等同一次分享，所以走 C-5 確認。
取消報名只影響本人，直接執行。
"""

from __future__ import annotations

from typing import Any, Literal

from app.db.engines import DbRole, session_scope
from app.db.models_pub import Event
from app.i18n import t
from app.services import events as ev
from app.tools._common import safe_all
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class EventRegisterArgs(CitizenToolArgs):
    event_id: str
    action: Literal["register", "cancel"] = "register"


def _summary(args: EventRegisterArgs) -> str:
    with session_scope(DbRole.IDN_APP) as s:
        event = s.get(Event, args.event_id)
        if event is None:  # 執行時才會以 not_found 失敗（確認狀態機記為 failed 並留稽核）
            return t("events.summary_unknown", event_id=args.event_id)
        points = t("events.push.points", points=event.points_reward) if event.points_reward else ""
        return t(
            "events.summary",
            title=event.title,
            when=ev.when_text(event.start_at, event.end_at),
            organizer=event.organizer,
            points=points,
        )


@registry.tool(
    name="event_register",
    schema=EventRegisterArgs,
    side="citizen",
    high_risk=lambda args: args.action == "register",
    action_type="event_register",
    summary=_summary,
)
def event_register(ctx: ToolContext, args: EventRegisterArgs) -> dict[str, Any]:
    """報名社區活動（register，需本人確認；報名會把姓名與電話交給主辦單位）或取消報名（cancel）。"""
    if args.action == "cancel":
        row = ev.cancel(ctx.db, user_id=args.user_id, event_id=args.event_id)
        return safe_all({"event_id": row.event_id, "status": row.status})
    if not args.confirmed:
        raise PermissionError("報名需本人確認")
    row = ev.register(
        ctx.db,
        user_id=args.user_id,
        event_id=args.event_id,
        created_by="a1" if ctx.agent == "A1" else "user",
    )
    event = ctx.db.get_one(Event, args.event_id)
    return safe_all(
        {
            "registration_id": str(row.id),
            "event_id": event.id,
            "title": event.title,
            "when": ev.when_text(event.start_at, event.end_at),
            "venue": event.venue,
            "points_reward": event.points_reward,
            "status": row.status,
        }
    )
