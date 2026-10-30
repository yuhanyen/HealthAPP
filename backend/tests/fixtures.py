"""跨測試檔共用的 fixture（由 conftest 的 pytest_plugins 載入）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.db.engines import DbRole, session_scope
from app.fhir import builders
from app.fhir.writer import ingest
from tests.conftest import Factory
from tests.helpers import OTHER_SITE, TEST_SITE

TZ = ZoneInfo("Asia/Taipei")
TODAY = datetime.now(TZ).date()
SCOPE = {
    "wearable": ["steps"],
    "site": ["bp", "weight"],
    "nhi": ["conditions", "visits"],
    "merchant": ["redemptions"],
}
OPEN_HOURS = (
    '{"mon":"08:00-20:00","tue":"08:00-20:00","wed":"08:00-20:00","thu":"08:00-20:00",'
    '"fri":"08:00-20:00","sat":"08:00-20:00","sun":"08:00-20:00"}'
)


@dataclass
class Person:
    user_id: uuid.UUID


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, datetime.min.time(), TZ) + timedelta(hours=hour)


@pytest.fixture
def wang(factory: Factory) -> Person:
    """仿王秀英：步數兩週掉一半、近三次血壓偏高、有高血壓就醫紀錄、60 天前回診。"""
    user = factory.user(name="王測試", birth_date=date(TODAY.year - 73, 1, 1))
    for source, items in SCOPE.items():
        factory.consent(user.id, source, items)
    with session_scope(DbRole.IDN_APP) as s:
        ingest(
            s,
            user_id=user.id,
            source="wearable",
            emit_events=False,
            resources=[
                builders.steps_daily(_at(TODAY - timedelta(days=d), 23), 6400 if d > 14 else 3100)
                for d in range(1, 29)
            ],
        )
        ingest(
            s,
            user_id=user.id,
            source="site",
            emit_events=False,
            resources=[
                builders.blood_pressure(_at(TODAY - timedelta(days=d), 9), s_, d_, site=TEST_SITE)
                for d, s_, d_ in ((6, 142, 91), (4, 145, 93), (2, 139, 90))
            ],
        )
        ingest(
            s,
            user_id=user.id,
            source="nhi",
            emit_events=False,
            resources=[
                builders.condition(date(2024, 3, 1), "I10", "Essential hypertension"),
                builders.encounter(_at(TODAY - timedelta(days=60), 10), "內科"),
            ],
        )
    return Person(user.id)


@pytest.fixture
def open_sites() -> Iterator[None]:
    """兩個測試據點每天 08:00–20:00 開放（預設 hours 為空，沒有可預約時段）。"""
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text("UPDATE pub.sites SET hours = :h WHERE id IN (:a, :b)"),
            {"h": OPEN_HOURS, "a": TEST_SITE, "b": OTHER_SITE},
        )
    yield
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text("UPDATE pub.sites SET hours = '{}' WHERE id IN (:a, :b)"),
            {"a": TEST_SITE, "b": OTHER_SITE},
        )
