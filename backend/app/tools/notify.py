"""notify（SPEC §7.1）：推播／LINE／App 收件匣。內容一律經 C-7 輸出過濾。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field

from app.services.notify import notify as send
from app.tools._common import safe
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class NotifyArgs(CitizenToolArgs):
    channel: Literal["push", "line", "inbox"] = "push"
    kind: Literal["daily_report", "alert", "reminder", "event"] = "reminder"
    title: str = Field(max_length=60)
    body: str = Field(max_length=500)
    report_date: date | None = Field(default=None, description="點開通知時要顯示的每日報告日期")
    event_id: str | None = Field(default=None, description="點開通知時要顯示的活動（kind=event）")


@registry.tool(name="notify", schema=NotifyArgs, side="citizen")
def notify(ctx: ToolContext, args: NotifyArgs) -> dict[str, Any]:
    """通知本人（每日報告 07:00 推送、異常即時提醒、近日活動）。活動推播不可含健康理由。"""
    payload: dict[str, Any] = {}
    if args.report_date:
        payload["report_date"] = args.report_date.isoformat()
    if args.event_id:
        payload["event_id"] = args.event_id
    note = send(
        ctx.db,
        user_id=args.user_id,
        channel=args.channel,
        kind=args.kind,
        title=safe(args.title),
        body=safe(args.body),
        payload=payload or None,
    )
    return {"notification_id": str(note.id), "status": note.status}
