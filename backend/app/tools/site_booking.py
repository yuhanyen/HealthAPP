"""site_booking（SPEC §7.1）：mode=query 只查時段；mode=book 是高風險動作，本人確認後才建立。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import model_validator

from app.db.engines import DbRole, session_scope
from app.i18n import t
from app.services import booking, geo
from app.tools._common import profile, site_name, tz
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class SiteBookingArgs(CitizenToolArgs):
    mode: Literal["query", "book"] = "query"
    site_id: str | None = None
    slot_at: datetime | None = None
    purpose: str = "血壓複測"
    days: int = 3

    @model_validator(mode="after")
    def _book_needs_slot(self) -> SiteBookingArgs:
        if self.mode == "book" and (self.site_id is None or self.slot_at is None):
            raise ValueError("預約需要 site_id 與 slot_at")
        return self


def _summary(args: SiteBookingArgs) -> str:
    assert args.slot_at is not None
    with session_scope(DbRole.IDN_APP) as s:
        name = site_name(s, args.site_id)
    when = args.slot_at.astimezone(tz())
    return t(
        "booking.summary",
        site=name,
        when=f"{when.month}/{when.day} {when:%H:%M}",
        purpose=args.purpose,
    )


@registry.tool(
    name="site_booking",
    schema=SiteBookingArgs,
    side="citizen",
    high_risk=lambda args: args.mode == "book",
    action_type="booking",
    summary=_summary,
)
def site_booking(ctx: ToolContext, args: SiteBookingArgs) -> dict[str, Any]:
    """查詢據點可預約時段（query），或在本人確認後預約（book）。預設找住家最近的據點。"""
    site_id = args.site_id or geo.nearest_site(ctx.db, profile(ctx.db, args.user_id).village_code)
    if site_id is None:
        raise booking.BookingError("site_not_found")
    if args.mode == "query":
        slots = booking.open_slots(ctx.db, site_id, now=datetime.now(UTC), days=args.days)
        return {
            "site_id": site_id,
            "site_name": site_name(ctx.db, site_id),
            "slots": [s.isoformat() for s in slots],
        }
    if not args.confirmed:
        raise PermissionError("預約需本人確認")
    assert args.slot_at is not None
    created = booking.book(
        ctx.db,
        user_id=args.user_id,
        site_id=site_id,
        slot_at=args.slot_at,
        purpose=args.purpose,
        created_by="a1" if ctx.agent == "A1" else "user",
    )
    return {
        "booking_id": str(created.id),
        "site_id": site_id,
        "site_name": site_name(ctx.db, site_id),
        "slot_at": created.slot_at.isoformat(),
    }
