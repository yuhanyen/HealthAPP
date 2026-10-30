"""點數對帳（SPEC §14 M6）：帳本 append-only，這裡只檢查、不修改。

檢查項目：
1. 每人帳本逐筆累加 = 每筆 balance_after（不可有斷層或負餘額）。
2. 每張兌換券（issued／verified）恰有一筆扣點，金額相符；每筆扣點都對得到兌換券。
3. 全體：發點 − 扣點 = 所有人目前餘額。
結果只有筆數與問題類型，不列出任何人（問題以內部序號標示，供管理者到稽核端追查）。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_idn import PointsLedger, Redemption


def reconcile(session: Session, user_ids: Sequence[uuid.UUID] | None = None) -> dict[str, Any]:
    ledger_q = select(PointsLedger).order_by(PointsLedger.user_id, PointsLedger.id)
    coupon_q = select(Redemption).where(Redemption.status.in_(("issued", "verified")))
    if user_ids is not None:
        ledger_q = ledger_q.where(PointsLedger.user_id.in_(user_ids))
        coupon_q = coupon_q.where(Redemption.user_id.in_(user_ids))
    rows = list(session.scalars(ledger_q))
    coupons = list(session.scalars(coupon_q))

    problems: list[dict[str, Any]] = []
    running: dict[uuid.UUID, int] = defaultdict(int)
    for row in rows:
        running[row.user_id] += row.delta
        if running[row.user_id] != row.balance_after:
            problems.append({"type": "balance_mismatch", "ledger_id": row.id})
        if row.balance_after < 0:
            problems.append({"type": "negative_balance", "ledger_id": row.id})

    debits = {row.ref_id: row for row in rows if row.ref_type == "redemption" and row.delta < 0}
    counted: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.ref_type == "redemption" and row.delta < 0 and row.ref_id:
            counted[row.ref_id] += 1
    merchants: dict[str, dict[str, int]] = defaultdict(
        lambda: {"issued": 0, "verified": 0, "points": 0}
    )
    for coupon in coupons:
        debit = debits.get(str(coupon.id))
        if debit is None:
            problems.append({"type": "coupon_without_debit", "redemption": str(coupon.id)[:8]})
        elif -debit.delta != coupon.points or counted[str(coupon.id)] != 1:
            problems.append({"type": "debit_mismatch", "redemption": str(coupon.id)[:8]})
        stats = merchants[coupon.merchant_id]
        stats[coupon.status] += 1
        stats["points"] += coupon.points
    coupon_ids = {str(c.id) for c in coupons}
    problems += [
        {"type": "debit_without_coupon", "ledger_id": row.id}
        for ref, row in debits.items()
        if ref not in coupon_ids
    ]

    granted = sum(r.delta for r in rows if r.delta > 0)
    spent = -sum(r.delta for r in rows if r.delta < 0)
    outstanding = sum(running.values())
    if granted - spent != outstanding:
        problems.append({"type": "totals_mismatch"})
    return {
        "ok": not problems,
        "accounts": len(running),
        "ledger_rows": len(rows),
        "granted": granted,
        "spent": spent,
        "outstanding": outstanding,
        "coupons": len(coupons),
        "merchants": [{"merchant_id": m, **v} for m, v in sorted(merchants.items())],
        "problems": problems[:50],
        "problem_count": len(problems),
    }
