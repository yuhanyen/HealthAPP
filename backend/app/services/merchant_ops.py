"""商家端（SPEC §9 商家、§15 情境 7）：掃碼核銷、月成效報告（只有聚合、沒有任何個人）。

商家看不到兌換者的姓名或健康資料：核銷只回傳優惠品項與點數；成效報告的年齡層分佈經 k-匿名遮蔽。
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.writer import write_audit
from app.db.engines import DbRole, get_engine
from app.db.models_idn import Redemption
from app.db.models_pub import Merchant, Offer
from app.deidentify.k_anon import mask_small_cells
from app.i18n import t

COUPON_VALID_DAYS = 30


class CouponError(Exception):
    def __init__(self, key: str, status: int) -> None:
        self.key = key
        self.status = status
        super().__init__(t(f"merchant.error.{key}"))


def verify(session: Session, *, merchant_id: str, qr_token: str, actor: str) -> dict[str, Any]:
    """核銷兌換券：只能核銷自己商家的券、只能一次、30 天內有效。"""
    coupon = session.scalars(
        select(Redemption).where(Redemption.qr_token == qr_token.strip()).with_for_update()
    ).first()
    reason = None
    if coupon is None:
        reason = CouponError("coupon_not_found", 404)
    elif coupon.merchant_id != merchant_id:
        reason = CouponError("not_yours", 403)
    elif coupon.status == "verified":
        reason = CouponError("coupon_used", 409)
    elif coupon.status != "issued" or coupon.redeemed_at < datetime.now(UTC) - timedelta(
        days=COUPON_VALID_DAYS
    ):
        reason = CouponError("coupon_expired", 410)
    if reason is not None:
        write_audit(
            None,
            actor_type="user",
            actor_id=actor,
            action="merchant.verify",
            decision="rejected",
            input={"merchant_id": merchant_id},
            reason=str(reason),
        )
        raise reason
    assert coupon is not None
    coupon.status = "verified"
    coupon.verified_at = datetime.now(UTC)
    offer = session.get_one(Offer, coupon.offer_id)
    write_audit(
        session,
        actor_type="user",
        actor_id=actor,
        action="merchant.verify",
        decision="executed",
        input={"merchant_id": merchant_id, "redemption_id": coupon.id},
        output={"offer_id": offer.id, "points": coupon.points},
    )
    return {
        "offer": offer.title,
        "offer_id": offer.id,
        "points": coupon.points,
        "redeemed_at": coupon.redeemed_at.isoformat(),
        "verified_at": coupon.verified_at.isoformat(),
    }


AGE_SQL = """
SELECT CASE WHEN date_part('year', age(:ref, u.birth_date)) >= 80 THEN '80+'
            WHEN date_part('year', age(:ref, u.birth_date)) < 20 THEN '<20'
            ELSE (floor(date_part('year', age(:ref, u.birth_date)) / 10) * 10)::int || '-'
                 || ((floor(date_part('year', age(:ref, u.birth_date)) / 10) * 10)::int + 9) END
       AS age_band,
       count(DISTINCT r.user_id) AS n
FROM idn.redemptions r JOIN idn.users u ON u.id = r.user_id
WHERE r.merchant_id = :merchant AND r.redeemed_at >= :start AND r.redeemed_at < :end
GROUP BY 1 ORDER BY 1
"""

WEEKLY_SQL = """
SELECT m.merchant_id, sum(m.redemptions) AS redemptions, sum(m.points) AS points
FROM anon.merchant_weekly m
WHERE m.week >= :start AND m.week < :end
GROUP BY m.merchant_id
"""


def month_range(month: str | None) -> tuple[date, date]:
    first = date.fromisoformat(f"{month}-01") if month else datetime.now(UTC).date().replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt


def monthly_report(
    session: Session, *, merchant_id: str, month: str | None, k: int
) -> dict[str, Any]:
    """月成效：自家核銷筆數與品項（自己的交易紀錄）、與同類商家中位數比較（anon 週聚合）、
    兌換者年齡層分佈（人數 < k 的年齡層遮蔽）。沒有任何個人欄位。"""
    start, end = month_range(month)
    merchant = session.get_one(Merchant, merchant_id)
    tz_start = datetime.combine(start, datetime.min.time(), UTC)
    tz_end = datetime.combine(end, datetime.min.time(), UTC)
    in_month = (
        Redemption.merchant_id == merchant_id,
        Redemption.redeemed_at >= tz_start,
        Redemption.redeemed_at < tz_end,
    )
    by_offer = session.execute(
        select(Offer.title, func.count(), func.sum(Redemption.points))
        .join(Offer, Offer.id == Redemption.offer_id)
        .where(*in_month)
        .group_by(Offer.title)
        .order_by(func.count().desc())
    ).all()
    status_counts = dict(
        session.execute(
            select(Redemption.status, func.count()).where(*in_month).group_by(Redemption.status)
        ).all()
    )
    ages = session.execute(
        text(AGE_SQL), {"merchant": merchant_id, "start": tz_start, "end": tz_end, "ref": end}
    )
    age_bands = mask_small_cells(["age_band", "n"], [tuple(r) for r in ages], k=k)
    peers = list(session.scalars(select(Merchant.id).where(Merchant.category == merchant.category)))
    # anon.merchant_weekly 只有「兌換人數 ≥ k」的週才有列（deidentify 寫入時已遮蔽），
    # 加總不會揭露個人
    with get_engine(DbRole.ANON_READER).connect() as conn:
        weekly = conn.execute(text(WEEKLY_SQL), {"start": start, "end": end}).mappings().all()
    peer_values = [float(r["redemptions"]) for r in weekly if r["merchant_id"] in peers]
    mine = next((r for r in weekly if r["merchant_id"] == merchant_id), None)
    return {
        "merchant_id": merchant_id,
        "merchant_name": merchant.name,
        "category": merchant.category,
        "month": start.strftime("%Y-%m"),
        "k": k,
        "redemptions": sum(n for _, n, _ in by_offer),
        "points": int(sum(p or 0 for _, _, p in by_offer)),
        "verified": status_counts.get("verified", 0),
        "outstanding": status_counts.get("issued", 0),
        "offers": [{"title": title, "count": n, "points": int(p or 0)} for title, n, p in by_offer],
        "age_bands": age_bands.rows,
        "age_masked": age_bands.masked_rows,
        "weekly_published": None if mine is None else int(mine["redemptions"]),
        "category_median": statistics.median(peer_values) if peer_values else None,
        "category_merchants": len(peer_values),
        "note": t("merchant.report_note", k=k),
    }
