"""交易內排入事件，commit 成功後才發布；rollback 則丟棄。

發布失敗只記錄、不影響已 commit 的資料：需要「一定做到」的後續動作（例如撤回後刪除資料）
另有 DB 任務表與排程補做，不依賴事件一定送達。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.events.bus import build_event, get_bus

log = logging.getLogger(__name__)

_KEY = "healthapp.outbox"


def enqueue(session: Session, event_type: str, **fields: Any) -> None:
    build_event(event_type, fields)  # 先驗證，錯誤在交易內就拋出
    session.info.setdefault(_KEY, []).append((event_type, fields))


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    for event_type, fields in session.info.pop(_KEY, []):
        try:
            get_bus().publish(event_type, **fields)
        except Exception:
            log.exception("事件發布失敗：%s（由排程補做）", event_type)


@event.listens_for(Session, "after_rollback")
def _discard_after_rollback(session: Session) -> None:
    session.info.pop(_KEY, None)
