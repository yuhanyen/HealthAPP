"""民眾出示給據點人員掃描的 QR：短效（10 分鐘）簽章 token，內容不含姓名或任何健康資料。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.auth.security import ALGORITHM, _secret

TTL = timedelta(minutes=10)


class InvalidUserQr(Exception):
    pass


def issue_user_qr(user_id: uuid.UUID, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires = now + TTL
    token = jwt.encode(
        {"typ": "user_qr", "uid": str(user_id), "iat": now, "exp": expires},
        _secret(),
        algorithm=ALGORITHM,
    )
    return token, expires


def read_user_qr(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidUserQr(str(exc)) from exc
    if claims.get("typ") != "user_qr":
        raise InvalidUserQr("不是民眾 QR")
    return uuid.UUID(claims["uid"])
