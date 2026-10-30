"""事件匯流（SPEC §6）：訊息格式、事件目錄驗證、消費者群組、交易後才發布。"""

from __future__ import annotations

import uuid

import pytest
import redis
from sqlalchemy import text

from app.config import get_settings
from app.db.engines import DbRole, session_scope
from app.events.bus import EVENT_CATALOG, EventBus, EventValidationError, make_client
from app.events.consumer import consume_once
from app.events.outbox import enqueue


def test_catalog_matches_spec_section_6() -> None:
    assert set(EVENT_CATALOG) == {
        "measurement.created",
        "nhi.updated",
        "daily.batch",
        "goal.achieved",
        "consent.revoked",
        "sentiment.alert",
        "care.list.ready",
        "redeem.verified",
    }


def test_publish_uses_spec_message_format(bus: EventBus) -> None:
    msg_id = bus.publish(
        "measurement.created",
        user_id="u-1",
        source="site",
        resource_id="r-1",
        at="2026-10-29T05:00:00+08:00",
    )
    [delivery] = bus.recent(1)
    assert delivery.id == msg_id
    assert delivery.event == {
        "type": "measurement.created",
        "user_id": "u-1",
        "source": "site",
        "resource_id": "r-1",
        "at": "2026-10-29T05:00:00+08:00",
    }


def test_at_is_filled_in_when_missing(bus: EventBus) -> None:
    bus.publish("daily.batch", date="2026-10-30")
    assert bus.recent(1)[0].event["at"]


@pytest.mark.parametrize(
    ("event_type", "fields"),
    [
        ("no.such.event", {}),
        ("consent.revoked", {"user_id": "u-1", "source": "nhi"}),  # 缺 consent_id
        (
            "care.list.ready",
            {"date": "2026-10-30", "site_id": "S01", "codes": ["N-1"], "user_id": "u-1"},
        ),
        (
            "sentiment.alert",
            {"alert_id": 1, "village_code": "N01", "topic": "排隊", "name": "陳小明"},
        ),
    ],
)
def test_invalid_events_are_rejected(
    bus: EventBus, event_type: str, fields: dict[str, object]
) -> None:
    with pytest.raises(EventValidationError):
        bus.publish(event_type, **fields)
    assert bus.recent(5) == []


def test_each_group_gets_its_own_copy_and_ack_is_final(bus: EventBus) -> None:
    bus.publish("daily.batch", date="2026-10-30")
    assert consume_once(bus, "adapters") == 1
    assert consume_once(bus, "a1") == 1
    assert consume_once(bus, "adapters") == 0


def test_outbox_publishes_only_after_commit(bus: EventBus) -> None:
    with session_scope(DbRole.IDN_APP) as s:
        enqueue(s, "daily.batch", date="2026-10-31")
        s.execute(text("SELECT 1"))
        assert bus.recent(5) == []
    assert [d.event["date"] for d in bus.recent(5)] == ["2026-10-31"]


def test_outbox_discards_on_rollback(bus: EventBus) -> None:
    with pytest.raises(RuntimeError), session_scope(DbRole.IDN_APP) as s:
        enqueue(s, "daily.batch", date="2026-11-01")
        raise RuntimeError("中途失敗")
    assert bus.recent(5) == []


def test_real_redis_streams_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "event_bus", "redis")
    client = make_client()
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("本機 Redis 未啟動（make infra）")
    stream = f"events_test_{uuid.uuid4().hex[:8]}"
    real = EventBus(client, stream=stream)
    try:
        real.publish("daily.batch", date="2026-10-30")
        assert consume_once(real, "a1") == 1
        assert consume_once(real, "a1") == 0
    finally:
        client.delete(stream)
