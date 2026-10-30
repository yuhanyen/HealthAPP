"""fhir/writer：所有 Adapter 寫入 idn 的唯一入口。"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.consent import service as consent_service
from app.db.engines import DbRole, session_scope
from app.db.models_idn import FhirResource
from app.events.bus import EventBus
from app.fhir import builders
from app.fhir.writer import InvalidResource, ingest
from tests.conftest import Factory
from tests.helpers import TEST_SITE

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _count(user_id: object, source: str) -> int:
    with session_scope(DbRole.IDN_APP) as s:
        return int(
            s.scalar(
                select(func.count())
                .select_from(FhirResource)
                .where(FhirResource.user_id == user_id, FhirResource.source == source)
            )
            or 0
        )


def test_no_active_consent_writes_nothing(factory: Factory, bus: EventBus) -> None:
    user = factory.user()
    with session_scope(DbRole.IDN_APP) as s:
        result = ingest(
            s,
            user_id=user.id,
            source="site",
            resources=[builders.blood_pressure(NOW, 130, 85, site=TEST_SITE)],
        )
    assert result.skipped_reason == "no_active_consent"
    assert _count(user.id, "site") == 0
    assert bus.recent(5) == []


def test_items_outside_scope_are_dropped(factory: Factory, bus: EventBus) -> None:
    user = factory.user()
    factory.consent(user.id, "site", ["bp"])
    with session_scope(DbRole.IDN_APP) as s:
        result = ingest(
            s,
            user_id=user.id,
            source="site",
            resources=[
                builders.blood_pressure(NOW, 130, 85, site=TEST_SITE),
                builders.glucose(NOW, 110, site=TEST_SITE),
            ],
        )
    assert (len(result.inserted), result.out_of_scope) == (1, 1)
    assert _count(user.id, "site") == 1


def test_upsert_is_idempotent_and_events_follow_commit(factory: Factory, bus: EventBus) -> None:
    user = factory.user()
    factory.consent(user.id, "site", ["bp", "weight"])
    resources = [
        builders.blood_pressure(NOW, 130, 85, site=TEST_SITE),
        builders.body_weight(NOW, 61.5, site=TEST_SITE),
    ]
    with session_scope(DbRole.IDN_APP) as s:
        first = ingest(s, user_id=user.id, source="site", resources=resources)
        assert bus.recent(5) == []  # 尚未 commit
    with session_scope(DbRole.IDN_APP) as s:
        second = ingest(s, user_id=user.id, source="site", resources=resources)
    assert (len(first.inserted), first.updated) == (2, 0)
    assert (len(second.inserted), second.updated) == (0, 2)
    assert _count(user.id, "site") == 2  # 血壓與體重同一時間，code 不同不互撞
    events = [d.event for d in bus.recent(10)]
    assert [e["type"] for e in events] == ["measurement.created", "measurement.created"]


def test_wearable_and_nhi_emit_one_event_per_batch(factory: Factory, bus: EventBus) -> None:
    user = factory.user()
    factory.consent(user.id, "wearable", ["steps", "heart_rate"])
    factory.consent(user.id, "nhi", ["visits", "conditions"])
    hours = [NOW + timedelta(hours=h) for h in range(3)]
    with session_scope(DbRole.IDN_APP) as s:
        ingest(
            s,
            user_id=user.id,
            source="wearable",
            resources=[
                r
                for h in hours
                for r in (builders.steps_hourly(h, 400), builders.heart_rate(h, 72))
            ],
        )
        ingest(
            s,
            user_id=user.id,
            source="nhi",
            resources=[
                builders.encounter(NOW, "內科"),
                builders.condition(NOW.date(), "I10", "Essential hypertension"),
            ],
        )
    events = {d.event["type"]: d.event for d in bus.recent(10)}
    assert events["measurement.created"]["count"] == 6
    assert events["nhi.updated"] == events["nhi.updated"] | {"user_id": str(user.id), "count": 2}


def test_invalid_resource_is_rejected(factory: Factory) -> None:
    user = factory.user()
    factory.consent(user.id, "site", ["bp"])
    bad = builders.blood_pressure(NOW, 130, 85)
    del bad["component"]
    with session_scope(DbRole.IDN_APP) as s, pytest.raises(InvalidResource):
        ingest(s, user_id=user.id, source="site", resources=[bad])


def test_revoke_waits_for_inflight_write_then_deletion_covers_it(
    factory: Factory, bus: EventBus
) -> None:
    """寫入交易持有 FOR SHARE 時，撤回會等它 commit；接著的刪除任務會刪掉剛寫入的資料。"""
    user = factory.user()
    consent = factory.consent(user.id, "site", ["bp"])
    revoked = threading.Event()

    def revoke() -> None:
        with session_scope(DbRole.IDN_APP) as s:
            consent_service.revoke(
                s, user_id=user.id, consent_id=consent.id, actor_type="user", actor_id=str(user.id)
            )
        revoked.set()

    with session_scope(DbRole.IDN_APP) as s:
        ingest(
            s,
            user_id=user.id,
            source="site",
            resources=[builders.blood_pressure(NOW, 150, 95, site=TEST_SITE)],
        )
        worker = threading.Thread(target=revoke)
        worker.start()
        time.sleep(0.5)
        assert not revoked.is_set(), "撤回應等待寫入交易結束"
    worker.join(timeout=10)
    assert revoked.is_set()

    from app.scheduler.jobs import sweep_deletion_jobs

    sweep_deletion_jobs()
    assert _count(user.id, "site") == 0
    with session_scope(DbRole.IDN_APP) as s:
        late = ingest(
            s,
            user_id=user.id,
            source="site",
            resources=[builders.blood_pressure(NOW, 151, 96, site=TEST_SITE)],
        )
    assert late.skipped_reason == "no_active_consent"
