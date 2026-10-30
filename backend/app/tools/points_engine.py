"""points_engine（SPEC §7.1）。

- status：餘額與到期
- evaluate：達標判定發點
- redeem：兌換（高風險）

防作弊：單小時 > 3,000 步或單日 > 40,000 步 → 標記待審、不發點。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import model_validator
from sqlalchemy import select

from app.db.engines import DbRole, session_scope
from app.db.models_idn import PointsLedger
from app.db.models_pub import Offer
from app.i18n import t
from app.services import health_data as hd
from app.services import points
from app.sysconfig import cached_value
from app.tools._common import today
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class PointsArgs(CitizenToolArgs):
    action: Literal["status", "evaluate", "redeem"] = "status"
    day: date | None = None
    offer_id: str | None = None

    @model_validator(mode="after")
    def _redeem_needs_offer(self) -> PointsArgs:
        if self.action == "redeem" and not self.offer_id:
            raise ValueError("兌換需要 offer_id")
        return self


def _required(args: PointsArgs) -> Mapping[str, Sequence[str]]:
    if args.action == "evaluate":
        return {"wearable": ["steps"]}
    if args.action == "redeem":
        return {"merchant": ["redemptions"]}
    return {}


def _summary(args: PointsArgs) -> str:
    with session_scope(DbRole.IDN_APP) as s:
        offer = s.get(Offer, args.offer_id)
        return t(
            "points.summary",
            points=offer.points_cost if offer else "?",
            offer=offer.title if offer else args.offer_id,
        )


@registry.tool(
    name="points_engine",
    schema=PointsArgs,
    side="citizen",
    required_scope=_required,
    high_risk=lambda args: args.action == "redeem",
    action_type="redeem",
    summary=_summary,
)
def points_engine(ctx: ToolContext, args: PointsArgs) -> dict[str, Any]:
    """查點數餘額與即將到期的點數（status）、判定某日是否達標並發點（evaluate）、兌換優惠（redeem，需本人確認）。"""
    if args.action == "status":
        recent = ctx.db.scalars(
            select(PointsLedger)
            .where(PointsLedger.user_id == args.user_id)
            .order_by(PointsLedger.id.desc())
            .limit(5)
        ).all()
        return {
            "balance": points.balance(ctx.db, args.user_id),
            "expiring": points.expiring(ctx.db, args.user_id, today()),
            "recent": [
                {"delta": r.delta, "reason": r.reason, "at": r.created_at.isoformat()}
                for r in recent
            ],
        }
    if args.action == "evaluate":
        day = args.day or today() - timedelta(days=1)
        hourly = hd.hourly_steps(ctx.db, args.user_id, day)
        total = hd.daily_steps(ctx.db, args.user_id, day, day + timedelta(days=1)).get(day, 0)
        goal = int(cached_value("step_goal_default", 6000))
        result = points.evaluate_day(
            ctx.db, args.user_id, day, goal=goal, hourly=hourly, total=total
        )
        return {
            "day": day.isoformat(),
            "steps": total,
            "goal": goal,
            "granted": result.granted,
            "flagged": result.flagged,
            "already_granted": result.already,
            "balance": points.balance(ctx.db, args.user_id),
        }
    if not args.confirmed:
        raise PermissionError("兌換需本人確認")
    assert args.offer_id is not None
    return points.redeem(ctx.db, args.user_id, args.offer_id, today())
