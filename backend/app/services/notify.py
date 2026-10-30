"""通知：寫入 App 收件匣，並經推播 Adapter 送出（Web Push／LINE；無金鑰則 Mock，標 [MOCK]）。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.adapters.base import mock_notice
from app.db.models_idn import Notification

log = logging.getLogger(__name__)


class PushChannel(Protocol):
    def send(self, user_id: uuid.UUID, title: str, body: str) -> bool: ...


class MockPush:
    """TODO(二期)：Web Push（VAPID 金鑰）與 LINE（feature flag，SPEC §16-2）。"""

    def send(self, user_id: uuid.UUID, title: str, body: str) -> bool:
        mock_notice("Push", "未設定 VAPID／LINE 金鑰，通知只進 App 收件匣")
        log.info("[MOCK] push → %s：%s", user_id, title)
        return True


_channel: PushChannel = MockPush()


def set_channel(channel: PushChannel) -> None:
    global _channel
    _channel = channel


def notify(
    session: Session,
    *,
    user_id: uuid.UUID,
    channel: str,
    kind: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> Notification:
    # 先送出再一次寫入最終狀態：A1 的連線只有 INSERT 與 read_at 的 UPDATE 權限
    delivered = True if channel == "inbox" else _channel.send(user_id, title, body)
    note = Notification(
        user_id=user_id,
        channel=channel,
        kind=kind,
        title=title,
        body=body,
        payload=payload,
        status="sent" if delivered else "failed",
        sent_at=datetime.now(UTC) if delivered else None,
    )
    session.add(note)
    session.flush()
    return note
