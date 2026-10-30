"""FastAPI 進入點：uvicorn app.main:app（make api）。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# 別名匯入：不讓 `app` 這個名稱指到套件（檔尾的 app 是 FastAPI 實例）
from app import tools as _register_tools  # noqa: F401  註冊工具
from app.actions.state_machine import (
    ActionError,
    ActionExpired,
    ActionNotFound,
    InvalidActionState,
    NotActionOwner,
)
from app.api import (
    actions,
    admin,
    agency,
    events,
    family,
    health,
    me,
    me_community,
    me_health,
    merchants,
    sites,
)
from app.auth import router as auth_router
from app.care.decode import DecodeRejected
from app.config import PROJECT_ROOT, get_settings
from app.consent import router as consent_router
from app.consent.service import ConsentError, ConsentNotActive, ConsentNotFound
from app.console import router as console_router
from app.db.guard import CrossSchemaQueryError
from app.deidentify.k_anon import KAnonViolation
from app.events import handlers as _register_handlers  # noqa: F401  註冊事件處理
from app.i18n import t
from app.services.booking import BookingError
from app.services.events import EventError
from app.services.family import FamilyError
from app.services.points import PointsError
from app.services.visit import VisitError
from app.tools.registry import ToolRejected

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

_STATUS: list[tuple[type[Exception], int]] = [
    (ConsentNotFound, 404),
    (ConsentNotActive, 409),
    (ConsentError, 422),
    (ActionNotFound, 404),
    (ActionExpired, 410),
    (InvalidActionState, 409),
    (NotActionOwner, 403),
    (ActionError, 422),
    (ToolRejected, 403),
    (DecodeRejected, 403),
    (KAnonViolation, 422),
    (BookingError, 409),
    (EventError, 409),
    (PointsError, 409),
    (FamilyError, 403),
    (VisitError, 422),
]


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    code = next(status for kind, status in _STATUS if isinstance(exc, kind))
    return JSONResponse(status_code=code, content={"detail": str(exc), "error": type(exc).__name__})


async def _cross_schema(request: Request, exc: Exception) -> JSONResponse:
    log.error("C-1 違規查詢被攔截：%s", exc)
    return JSONResponse(status_code=500, content={"detail": t("db.error.cross_schema")})


WEB_APPS = ("citizen", "agency", "site", "merchant")


def mount_web_apps(application: FastAPI) -> None:
    """已 build 的前端（web/apps/*/dist）掛在 /app/<名稱>/，與 API 同源，不需另開伺服器。"""
    for name in WEB_APPS:
        dist = PROJECT_ROOT / "web" / "apps" / name / "dist"
        if (dist / "index.html").exists():
            application.mount(f"/app/{name}", StaticFiles(directory=dist, html=True), name=name)


def create_app() -> FastAPI:
    application = FastAPI(
        title=f"{t('app.name')} API",
        version="0.4.0-M4",
        description=(
            "一期 PoC：身分驗證、同意管理、確認狀態機、事件匯流、稽核、資料進、工具層、"
            "A1 個人數位雙生（每日報告、對話 SSE、守護圈）。\n\n"
            "示範帳號見專案根目錄 README.md；右上角 **Authorize** 輸入帳密即可試打。"
        ),
    )
    for router in (
        health.router,
        auth_router.router,
        consent_router.router,
        me.router,
        me_health.router,
        me_community.router,
        family.router,
        actions.router,
        sites.router,
        merchants.router,
        agency.router,
        events.router,
        admin.router,
    ):
        application.include_router(router, prefix=API_PREFIX)
    application.include_router(console_router.router)
    mount_web_apps(application)

    for kind, _ in _STATUS:
        application.add_exception_handler(kind, _domain_error)
    application.add_exception_handler(CrossSchemaQueryError, _cross_schema)

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        # 正式（雲端示範）環境：首頁直接進民眾 App；開發環境：M1 驗收台
        return RedirectResponse("/app/citizen/" if get_settings().app_env == "prod" else "/console")

    return application


app = create_app()
