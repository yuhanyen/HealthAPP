"""民眾健康儀表板（SPEC §17.6）：經 health_summary 工具（授權檢查、稽核同 A1）。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from app.api.deps import DB
from app.api.me import _api_ctx, _uid
from app.auth.deps import CurrentCitizen
from app.consent.service import effective_scope
from app.tools.registry import registry

router = APIRouter(prefix="/me", tags=["me 民眾"])


@router.get("/health", summary="健康儀表板：近 7／30／90 天各指標時序與統計（只含已授權項目）")
def my_health(
    principal: CurrentCitizen, db: DB, period: Literal["week", "month", "quarter"] = "month"
) -> dict[str, Any]:
    uid = _uid(principal)
    scope = effective_scope(db, uid)
    if not scope:
        return {"period": period, "metrics": {}, "not_authorized": [], "lines": [], "empty": True}
    result = registry.call(
        "health_summary",
        {"user_id": uid, "consent_scope": scope, "period": period},
        _api_ctx(db, uid),
    )
    return result.data or {}
