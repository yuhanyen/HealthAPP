"""M1 驗收台（/console）：開發環境專用的單頁，文案全部來自 i18n/zh-TW.json。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.config import get_settings
from app.i18n import catalog

PAGE = Path(__file__).with_name("index.html")

router = APIRouter()


@router.get("/console", include_in_schema=False)
def console() -> FileResponse:
    if get_settings().app_env == "prod":
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return FileResponse(PAGE, media_type="text/html; charset=utf-8")


@router.get("/api/v1/i18n", tags=["health 健康檢查"], summary="介面文案（zh-TW）")
def i18n() -> dict[str, Any]:
    return catalog()
