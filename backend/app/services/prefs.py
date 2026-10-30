"""推薦偏好（SPEC §17.1 idn.user_prefs）。沒有設定過的人用系統預設。"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models_idn import UserPrefs
from app.sysconfig import cached_value

DEFAULT_MIN_RATING = 4.0
DEFAULT_EVENT_DISTANCE_KM = 3.0
DEFAULT_MERCHANT_DISTANCE_KM = 2.0
DIETARY = ("low_salt", "low_sugar", "vegetarian", "soft_food")


@dataclass
class Prefs:
    event_categories: list[str] = field(default_factory=list)
    merchant_categories: list[str] = field(default_factory=list)
    dietary: list[str] = field(default_factory=list)
    min_rating: float | None = None
    max_distance_km: float | None = None
    event_push: bool = True
    saved: bool = False

    @property
    def rating_threshold(self) -> float:
        if self.min_rating is not None:
            return self.min_rating
        return float(cached_value("merchant_min_rating", DEFAULT_MIN_RATING))

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "rating_threshold": self.rating_threshold}


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def get_prefs(session: Session, user_id: uuid.UUID) -> Prefs:
    row = session.get(UserPrefs, user_id)
    if row is None:
        return Prefs()
    return Prefs(
        event_categories=list(row.event_categories),
        merchant_categories=list(row.merchant_categories),
        dietary=list(row.dietary),
        min_rating=_float(row.min_rating),
        max_distance_km=_float(row.max_distance_km),
        event_push=row.event_push,
        saved=True,
    )


def save_prefs(session: Session, user_id: uuid.UUID, prefs: Prefs) -> Prefs:
    values = {
        "event_categories": sorted(set(prefs.event_categories)),
        "merchant_categories": sorted(set(prefs.merchant_categories)),
        "dietary": sorted(set(prefs.dietary)),
        "min_rating": Decimal(str(prefs.min_rating)) if prefs.min_rating is not None else None,
        "max_distance_km": (
            Decimal(str(prefs.max_distance_km)) if prefs.max_distance_km is not None else None
        ),
        "event_push": prefs.event_push,
        "updated_at": datetime.now(UTC),
    }
    stmt = insert(UserPrefs).values(user_id=user_id, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=["user_id"], set_=values))
    session.flush()
    session.expire_all()
    return get_prefs(session, user_id)
