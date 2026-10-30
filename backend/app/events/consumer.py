"""事件消費者：每個消費者群組（SPEC §6 的「消費者」欄）各自 XREADGROUP／XACK。

處理失敗不 ACK，訊息留在 pending，下一輪先重送 pending 再讀新訊息。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.events.bus import EventBus

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]
_HANDLERS: dict[str, dict[str, Handler]] = defaultdict(dict)


def handles(group: str, event_type: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[group][event_type] = fn
        return fn

    return decorator


def groups() -> list[str]:
    return sorted(_HANDLERS)


def consume_once(
    bus: EventBus, group: str, consumer: str = "worker-1", *, block_ms: int | None = None
) -> int:
    """處理一輪（先 pending 再新訊息），回傳成功處理的筆數。"""
    bus.ensure_group(group)
    handlers = _HANDLERS.get(group, {})
    processed = 0
    for pending in (True, False):
        for delivery in bus.read(
            group, consumer, pending=pending, block_ms=None if pending else block_ms
        ):
            handler = handlers.get(delivery.event.get("type", ""))
            try:
                if handler is not None:
                    handler(delivery.event)
            except Exception:
                log.exception("[%s] 處理 %s 失敗，留待重送", group, delivery.id)
                continue
            bus.ack(group, delivery.id)
            processed += 1
    return processed


def run_forever(bus: EventBus, stop: threading.Event, consumer: str = "worker-1") -> None:
    log.info("事件消費者啟動：%s", ", ".join(groups()))
    while not stop.is_set():
        for group in groups():
            try:
                consume_once(bus, group, consumer, block_ms=500)
            except Exception:
                log.exception("[%s] 消費迴圈錯誤", group)
                stop.wait(2)
