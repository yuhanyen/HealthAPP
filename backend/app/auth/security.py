"""自建簡版身分驗證（SPEC §16-1 預設）：bcrypt 密碼 + HS256 JWT。OIDC 介面見 oidc.py。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings
from app.db.models_sys import Account

ALGORITHM = "HS256"


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    account_id: uuid.UUID
    role: str
    display_name: str
    user_id: uuid.UUID | None = None
    site_id: str | None = None
    merchant_id: str | None = None


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def _secret() -> str:
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET 未設定或太短（至少 32 字元）")
    return secret


def create_access_token(account: Account) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(account.id),
        "role": account.role,
        "name": account.display_name,
        "uid": str(account.user_id) if account.user_id else None,
        "site": account.site_id,
        "merchant": account.merchant_id,
        "iat": now,
        "exp": now + timedelta(minutes=get_settings().jwt_ttl_minutes),
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> Principal:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    return Principal(
        account_id=uuid.UUID(claims["sub"]),
        role=claims["role"],
        display_name=claims.get("name", ""),
        user_id=uuid.UUID(claims["uid"]) if claims.get("uid") else None,
        site_id=claims.get("site"),
        merchant_id=claims.get("merchant"),
    )
