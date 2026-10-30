"""MerchantAdapter（SPEC §5）：商家端 API 即時推送（優惠上架；核銷於 M6）。以商家 API key 驗證。

對口：經發局、商圈協會。商家只碰 pub（優惠）與核銷碼，永遠看不到任何健康資料。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.base import FetchContext, RawRecord
from app.db.models_pub import Merchant, Offer


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key(merchant_id: str) -> str:
    return f"mk_{merchant_id.lower()}_{secrets.token_urlsafe(18)}"


def verify_api_key(session: Session, merchant_id: str, key: str) -> bool:
    merchant = session.get(Merchant, merchant_id)
    if merchant is None or not merchant.active:
        return False
    return hmac.compare_digest(merchant.api_key_hash, hash_api_key(key))


class MerchantAdapter:
    source = "merchant"

    def schedule(self) -> str:
        return "realtime"

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        return []  # 推送式

    def normalize(self, raw: RawRecord) -> list[dict[str, Any]]:
        """payload：{merchant_id, offer_id, title, points_cost, stock, valid_from, valid_to}"""
        p = raw.payload
        return [
            {
                "id": p["offer_id"],
                "merchant_id": p["merchant_id"],
                "title": str(p["title"]).strip(),
                "points_cost": int(p["points_cost"]),
                "stock": int(p["stock"]),
                "valid_from": date.fromisoformat(str(p["valid_from"])),
                "valid_to": date.fromisoformat(str(p["valid_to"])),
            }
        ]

    def upsert_offer(self, session: Session, payload: dict[str, Any]) -> Offer:
        [row] = self.normalize(RawRecord(self.source, payload, datetime.now(UTC)))
        stmt = insert(Offer).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Offer.id],
            set_={
                k: stmt.excluded[k]
                for k in ("title", "points_cost", "stock", "valid_from", "valid_to")
            },
            where=Offer.merchant_id == row["merchant_id"],  # 不能改別家的優惠
        )
        session.execute(stmt)
        return session.get_one(Offer, row["id"], populate_existing=True)
