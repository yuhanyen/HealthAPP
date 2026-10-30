from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.engines import DbRole, session_scope
from app.events.bus import EventBus, get_bus


def get_db() -> Iterator[Session]:
    """API 的民眾／據點／商家／管理端點用 idn_app；整個請求一個交易。"""
    with session_scope(DbRole.IDN_APP) as session:
        yield session


DB = Annotated[Session, Depends(get_db)]
Bus = Annotated[EventBus, Depends(get_bus)]
