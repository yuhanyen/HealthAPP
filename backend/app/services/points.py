"""點數帳本（append-only）：達標發點、防作弊、到期（FIFO，180 天）、兌換。

同一人的帳本寫入以 advisory lock 序列化，balance_after 不會因並行而錯亂。
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.writer import write_audit
from app.config import get_settings
from app.db.models_idn import PointsLedger, PointsReview, Redemption
from app.db.models_pub import Offer
from app.events.outbox import enqueue
from app.i18n import t
from app.sysconfig import cached_value

GOAL_POINTS = 10
EXPIRY_DAYS = 180
DEFAULT_ANTI_CHEAT = {"max_steps_per_hour": 3000, "max_steps_per_day": 40000}


class PointsError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(t(f"points.error.{key}"))


@dataclass
class DayEvaluation:
    day: date
    steps: int
    goal: int
    granted: int = 0
    flagged: str | None = None
    already: bool = False


def _lock(session: Session, user_id: uuid.UUID) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:u))"), {"u": str(user_id)})


def balance(session: Session, user_id: uuid.UUID) -> int:
    value = session.scalar(
        select(PointsLedger.balance_after)
        .where(PointsLedger.user_id == user_id)
        .order_by(PointsLedger.id.desc())
        .limit(1)
    )
    return int(value or 0)


def append(
    session: Session, user_id: uuid.UUID, delta: int, reason: str, ref_type: str, ref_id: str
) -> PointsLedger:
    _lock(session, user_id)
    new_balance = balance(session, user_id) + delta
    if new_balance < 0:
        raise PointsError("insufficient")
    row = PointsLedger(
        user_id=user_id,
        delta=delta,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        balance_after=new_balance,
    )
    session.add(row)
    session.flush()
    return row


def expiring(
    session: Session, user_id: uuid.UUID, today: date, within_days: int = 30
) -> list[dict[str, Any]]:
    """先進先出：扣點先抵最舊的點；剩下的點各自在發放後 180 天到期。"""
    rows = session.execute(
        select(PointsLedger.delta, PointsLedger.created_at)
        .where(PointsLedger.user_id == user_id)
        .order_by(PointsLedger.id)
    ).all()
    tz = ZoneInfo(get_settings().timezone)
    lots: list[list[Any]] = []  # [剩餘點數, 發放日（台北）]
    for delta, created in rows:
        if delta > 0:
            lots.append([delta, created.astimezone(tz).date()])
            continue
        debt = -delta
        for lot in lots:
            take = min(lot[0], debt)
            lot[0] -= take
            debt -= take
            if debt == 0:
                break
    horizon = today + timedelta(days=within_days)
    buckets: dict[date, int] = {}
    for remaining, granted in lots:
        expires = granted + timedelta(days=EXPIRY_DAYS)
        if remaining > 0 and today <= expires <= horizon:
            buckets[expires] = buckets.get(expires, 0) + remaining
    return [{"expires_on": d.isoformat(), "points": p} for d, p in sorted(buckets.items())]


def evaluate_day(
    session: Session,
    user_id: uuid.UUID,
    day: date,
    *,
    goal: int,
    hourly: dict[int, int],
    total: int,
) -> DayEvaluation:
    """某日是否達標發點；異常步數先標記待審、不發點。"""
    result = DayEvaluation(day=day, steps=total, goal=goal)
    already = session.scalar(
        select(func.count())
        .select_from(PointsLedger)
        .where(
            PointsLedger.user_id == user_id,
            PointsLedger.ref_type == "date",
            PointsLedger.ref_id == day.isoformat(),
        )
    )
    if already:
        result.already = True
        return result
    limits = {**DEFAULT_ANTI_CHEAT, **cached_value("anti_cheat", {})}
    peak = max(hourly.values(), default=0)
    if peak > limits["max_steps_per_hour"] or total > limits["max_steps_per_day"]:
        result.flagged = "hourly_peak" if peak > limits["max_steps_per_hour"] else "daily_total"
        session.execute(
            insert(PointsReview)
            .values(
                user_id=user_id,
                day=day,
                reason=result.flagged,
                detail={"peak_hour_steps": peak, "total": total},
            )
            .on_conflict_do_nothing()
        )
        return result
    if total >= goal:
        append(session, user_id, GOAL_POINTS, "goal.steps", "date", day.isoformat())
        result.granted = GOAL_POINTS
        enqueue(session, "goal.achieved", user_id=str(user_id), goal="steps", date=day.isoformat())
    return result


def resolve_review(
    session: Session, review_id: int, *, cleared: bool, actor_id: str, goal: int
) -> tuple[PointsReview, int]:
    """人工審核防作弊標記：放行（cleared）且當天步數達標才補發點；駁回（rejected）不發點。"""
    review = session.get(PointsReview, review_id, with_for_update=True)
    if review is None:
        raise PointsError("review_not_found")
    if review.status != "pending":
        raise PointsError("review_done")
    review.status = "cleared" if cleared else "rejected"
    review.resolved_at = datetime.now(UTC)
    granted = 0
    total = int((review.detail or {}).get("total", 0))
    already = session.scalar(
        select(func.count())
        .select_from(PointsLedger)
        .where(
            PointsLedger.user_id == review.user_id,
            PointsLedger.ref_type == "date",
            PointsLedger.ref_id == review.day.isoformat(),
        )
    )
    if cleared and total >= goal and not already:
        append(session, review.user_id, GOAL_POINTS, "goal.steps", "date", review.day.isoformat())
        granted = GOAL_POINTS
    write_audit(
        session,
        actor_type="admin",
        actor_id=actor_id,
        action="points.review",
        decision="executed",
        input={"review_id": review_id, "decision": review.status},
        output={"granted": granted},
    )
    return review, granted


def redeem(session: Session, user_id: uuid.UUID, offer_id: str, today: date) -> dict[str, Any]:
    offer = session.get(Offer, offer_id, with_for_update=True)
    if offer is None:
        raise PointsError("offer_not_found")
    if not (offer.valid_from <= today <= offer.valid_to) or offer.stock <= 0:
        raise PointsError("offer_unavailable")
    redemption = Redemption(
        id=uuid.uuid4(),
        user_id=user_id,
        merchant_id=offer.merchant_id,
        offer_id=offer.id,
        points=offer.points_cost,
        qr_token=f"RD-{secrets.token_urlsafe(12)}",
        redeemed_at=datetime.now(UTC),
    )
    session.add(redemption)
    session.flush()
    ledger = append(
        session, user_id, -offer.points_cost, "redeem", "redemption", str(redemption.id)
    )
    offer.stock -= 1
    return {
        "redemption_id": str(redemption.id),
        "qr_token": redemption.qr_token,
        "offer": offer.title,
        "points": offer.points_cost,
        "balance": ledger.balance_after,
    }
