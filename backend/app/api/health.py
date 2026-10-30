from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DB, Bus
from app.config import get_settings

router = APIRouter(tags=["health 健康檢查"])


@router.get("/health", summary="服務健康狀態")
def health(db: DB, bus: Bus) -> dict[str, Any]:
    db_ok = db.execute(text("SELECT 1")).scalar_one() == 1
    settings = get_settings()
    return {
        "status": "ok" if db_ok and bus.ping() else "degraded",
        "milestone": "M1",
        "database": {"ok": db_ok, "name": settings.pg_db},
        "event_bus": {"ok": bus.ping(), "backend": settings.event_bus, "stream": bus.stream},
    }
