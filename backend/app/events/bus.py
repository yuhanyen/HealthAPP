"""事件匯流（SPEC §6）：Redis Streams，stream 名稱 `events`。

每則訊息存兩個欄位：type（方便篩選）與 json（SPEC 規定的扁平 JSON 訊息本體）。
發布前依事件目錄驗證必要欄位；去識別化側（anon）產生的事件禁止夾帶可識別欄位。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

import fakeredis
import redis

from app.config import get_settings

log = logging.getLogger(__name__)

Zone = Literal["idn", "anon", "system"]


@dataclass(frozen=True)
class EventSpec:
    required: frozenset[str]
    zone: Zone
    producer: str


EVENT_CATALOG: dict[str, EventSpec] = {
    "measurement.created": EventSpec(
        frozenset({"user_id", "source", "resource_id"}), "idn", "Adapter"
    ),
    "nhi.updated": EventSpec(frozenset({"user_id"}), "idn", "NhiAdapter"),
    "daily.batch": EventSpec(frozenset({"date"}), "system", "排程 05:00"),
    "goal.achieved": EventSpec(frozenset({"user_id", "goal"}), "idn", "points_engine"),
    "consent.revoked": EventSpec(frozenset({"user_id", "source", "consent_id"}), "idn", "同意管理"),
    "sentiment.alert": EventSpec(frozenset({"alert_id", "village_code", "topic"}), "anon", "A4"),
    "care.list.ready": EventSpec(frozenset({"date", "site_id", "codes"}), "anon", "A2"),
    "redeem.verified": EventSpec(frozenset({"redemption_id", "merchant_id"}), "idn", "商家 API"),
}

IDENTIFYING_FIELDS = frozenset({"user_id", "name", "phone", "address", "national_id_hash"})


class EventValidationError(ValueError):
    pass


def build_event(event_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    spec = EVENT_CATALOG.get(event_type)
    if spec is None:
        raise EventValidationError(f"未定義的事件類型：{event_type}")
    missing = spec.required - fields.keys()
    if missing:
        raise EventValidationError(f"{event_type} 缺少欄位：{sorted(missing)}")
    if spec.zone == "anon":
        leaked = IDENTIFYING_FIELDS & fields.keys()
        if leaked:
            raise EventValidationError(f"{event_type} 屬去識別化側，不得含 {sorted(leaked)}")
    event = {"type": event_type, **{k: _plain(v) for k, v in fields.items()}}
    event.setdefault("at", datetime.now(UTC).isoformat())
    return event


def _plain(value: Any) -> Any:
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class Delivery:
    id: str
    event: dict[str, Any]


class EventBus:
    def __init__(self, client: redis.Redis, stream: str = "events") -> None:
        self.client = client
        self.stream = stream

    def publish(self, event_type: str, **fields: Any) -> str:
        event = build_event(event_type, fields)
        msg_id = self.client.xadd(
            self.stream,
            {"type": event_type, "json": json.dumps(event, ensure_ascii=False)},
            maxlen=get_settings().event_stream_maxlen,
            approximate=True,
        )
        log.info("event published %s %s", event_type, msg_id)
        return str(msg_id)

    def ensure_group(self, group: str) -> None:
        try:
            self.client.xgroup_create(self.stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read(
        self,
        group: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int | None = None,
        pending: bool = False,
    ) -> list[Delivery]:
        start = "0" if pending else ">"
        response = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            self.client.xreadgroup(
                group, consumer, {self.stream: start}, count=count, block=block_ms
            ),
        )
        deliveries: list[Delivery] = []
        for _stream, messages in response or []:
            for msg_id, data in messages:
                if not data:  # 已被 XDEL／裁切的待處理訊息
                    continue
                deliveries.append(Delivery(msg_id, json.loads(data["json"])))
        return deliveries

    def ack(self, group: str, msg_id: str) -> None:
        self.client.xack(self.stream, group, msg_id)

    def recent(self, count: int = 50) -> list[Delivery]:
        messages = cast(
            list[tuple[str, dict[str, str]]], self.client.xrevrange(self.stream, count=count)
        )
        return [Delivery(msg_id, json.loads(data["json"])) for msg_id, data in messages]

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False


_FAKE_SERVER = fakeredis.FakeServer()
_bus: EventBus | None = None


def make_client() -> redis.Redis:
    s = get_settings()
    if s.event_bus == "memory":
        log.warning("[MOCK] 事件匯流使用進程內 fakeredis（EVENT_BUS=memory）")
        return fakeredis.FakeRedis(server=_FAKE_SERVER, decode_responses=True)
    # protocol=2：相容 Windows 版 Redis 5（不支援 RESP3 的 HELLO）
    return redis.Redis.from_url(s.redis_url, decode_responses=True, protocol=2)


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(make_client(), stream=get_settings().event_stream)
    return _bus


def set_bus(bus: EventBus | None) -> EventBus | None:
    """測試用：替換全域事件匯流，回傳原本的。"""
    global _bus
    previous, _bus = _bus, bus
    return previous
