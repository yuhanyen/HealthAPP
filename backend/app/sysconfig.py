"""sys.config：k 值、黑名單、閾值等可調參數。變更一律寫稽核（SPEC §13-3）。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.writer import ActorType, write_audit
from app.config import get_settings
from app.db.models_sys import Config


def get_value(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Config, key)
    return default if row is None else row.value


def get_k(session: Session) -> int:
    return int(get_value(session, "k", get_settings().k_default))


_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 60.0


def cached_value(key: str, default: Any) -> Any:
    """讀設定但不需呼叫端有 sys 權限（A1、機關側連線都讀不到 sys）。

    以 idn_app 另開連線，快取 60 秒。
    """
    from app.db.engines import DbRole, get_engine

    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL:
        return hit[1]
    with get_engine(DbRole.IDN_APP).connect() as conn:
        value = conn.execute(select(Config.value).where(Config.key == key)).scalar()
    resolved = default if value is None else value
    _cache[key] = (now, resolved)
    return resolved


def clear_cache() -> None:
    _cache.clear()


def set_value(
    session: Session, key: str, value: Any, *, actor_type: ActorType, actor_id: str
) -> None:
    before = get_value(session, key)
    _cache.pop(key, None)
    stmt = insert(Config).values(
        key=key, value=value, updated_at=datetime.now(UTC), updated_by=actor_id
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Config.key],
            set_={
                "value": stmt.excluded.value,
                "updated_at": stmt.excluded.updated_at,
                "updated_by": stmt.excluded.updated_by,
            },
        )
    )
    write_audit(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="config.update",
        decision="executed",
        input={"key": key, "value": value},
        output={"before": before},
        reason=f"設定 {key} 變更",
    )
