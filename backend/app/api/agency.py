"""機關 API（SPEC §9 機關）。

只用 anon_reader 連線讀去識別化區，並一律經 k-匿名中介軟體（C-2、C-4）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents import a2
from app.agents.runtime import DoneEvent
from app.api import ratelimit
from app.api.deps import DB
from app.api.sse import stream_agent
from app.auth.deps import require_roles
from app.auth.security import Principal
from app.config import get_settings
from app.db.engines import DbRole, get_engine, session_scope
from app.db.models_anon import CareListDaily, SentimentAlert
from app.deidentify.aggregate import METRICS
from app.deidentify.k_anon import execute_k_anonymous
from app.i18n import t
from app.services import artifacts, kpi
from app.sysconfig import cached_value, get_k
from app.tools.registry import ToolContext, registry

router = APIRouter(prefix="/agency", tags=["agency 機關"])

Agency = Annotated[Principal, Depends(require_roles("agency", "admin"))]


def _actor(principal: Principal) -> str:
    return f"A2:{principal.account_id}"


class CohortOut(BaseModel):
    metric: str
    k: int
    masked_rows: int
    rows: list[dict[str, Any]]


@router.get("/cohort", response_model=CohortOut, summary="群體統計（去識別化、k-匿名）")
def cohort(
    _: Agency,
    db: DB,
    metric: Annotated[str, Query(pattern="|".join(METRICS))] = "steps_avg",
    weeks: Annotated[int, Query(ge=1, le=52)] = 12,
    village: str = "ALL",
    age_band: str = "ALL",
    sex: str = "ALL",
) -> CohortOut:
    k = get_k(db)  # 設定在 sys（idn_app 讀）；查詢本身只走 anon_reader
    with get_engine(DbRole.ANON_READER).connect() as conn:
        result = execute_k_anonymous(
            conn,
            "SELECT week, village_code, age_band, sex, value, n FROM anon.cohort_weekly "
            "WHERE metric = :metric AND (:village = '*' OR village_code = :village) "
            "AND age_band = :age AND sex = :sex "
            "AND week >= (SELECT max(week) FROM anon.cohort_weekly) - (:weeks - 1) * 7 "
            "ORDER BY week, village_code",
            {"metric": metric, "village": village, "age": age_band, "sex": sex, "weeks": weeks},
            k=k,
        )
    return CohortOut(metric=metric, k=k, masked_rows=result.masked_rows, rows=result.rows)


@router.get("/overview", summary="儀表板總覽：KPI 卡、熱區 GeoJSON、趨勢、警訊、關懷清單數量")
def overview(
    principal: Agency,
    db: DB,
    metric: Annotated[str, Query(pattern="|".join(METRICS))] = "bp_uncontrolled_rate",
    weeks: Annotated[int, Query(ge=4, le=52)] = 12,
) -> dict[str, Any]:
    k = get_k(db)
    with get_engine(DbRole.ANON_READER).connect() as conn:
        cards = kpi.kpis(conn, k=k, period="week")
    with session_scope(DbRole.ANON_READER) as s:
        ctx = ToolContext(agent="api", db=s, actor_type="staff", actor_id=str(principal.account_id))
        cohort_data = registry.call("cohort_analysis", {"metrics": [metric], "weeks": weeks}, ctx)
        alerts = s.scalars(
            select(SentimentAlert).order_by(SentimentAlert.created_at.desc()).limit(10)
        ).all()
        care_day = s.scalar(select(CareListDaily.day).order_by(CareListDaily.day.desc()).limit(1))
        care = (
            s.scalars(select(CareListDaily).where(CareListDaily.day == care_day)).all()
            if care_day
            else []
        )
        return {
            "k": k,
            "metric": metric,
            "metric_label": t(f"metric.{metric}"),
            "kpis": cards["items"],
            "cohort": (cohort_data.data or {}).get("metrics", {}).get(metric, {}),
            "masked_rows": (cohort_data.data or {}).get("masked_rows", 0) + cards["masked_rows"],
            "alerts_open": sum(a.status == "open" for a in alerts),
            "alerts": [_alert_out(a) for a in alerts],
            "care_list": {
                "day": care_day.isoformat() if care_day else None,
                "rows": [{"site_id": c.site_id, "task_type": c.task_type, "n": c.n} for c in care],
            },
        }


def _alert_out(alert: SentimentAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "village_code": alert.village_code,
        "topic": alert.topic,
        "category": alert.category,
        "count": alert.count,
        "baseline": float(alert.baseline),
        "ratio": float(alert.ratio),
        "status": alert.status,
        "created_at": alert.created_at.isoformat(),
        "analysis": alert.analysis,
    }


@router.get("/alerts", summary="輿情警訊（含 A2 對照分析）")
def alerts(_: Agency, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict[str, Any]]:
    with session_scope(DbRole.ANON_READER) as s:
        rows = s.scalars(
            select(SentimentAlert).order_by(SentimentAlert.created_at.desc()).limit(limit)
        ).all()
        return [_alert_out(a) for a in rows]


@router.post("/alerts/{alert_id}/analyze", summary="立即請 A2 對照這則警訊（委派 A3＋A4）")
def analyze(alert_id: int, _: Agency) -> dict[str, Any]:
    with session_scope(DbRole.ANON_READER) as s:
        if s.get(SentimentAlert, alert_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("me.error.not_found"))
    return a2.analyze_alert(alert_id)


def _kpis(db: Any, period: Literal["week", "month"]) -> dict[str, Any]:
    targets = cached_value("kpi_targets", {})
    with get_engine(DbRole.ANON_READER).connect() as conn:
        return kpi.kpis(conn, k=get_k(db), period=period, targets=targets)


@router.get("/kpi", summary="計畫 KPI（本期 vs 上期 vs 目標，含 App 使用指標）")
def kpi_report(_: Agency, db: DB, period: Literal["week", "month"] = "week") -> dict[str, Any]:
    return _kpis(db, period)


def _kpi_value(metric: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric in ("steps_avg", "dau", "mau"):
        return f"{value:,.0f}"
    if metric == "measure_freq":
        return f"{value:.2f}"
    return f"{value * 100:.1f}%"


def _kpi_line(item: dict[str, Any]) -> str:
    metric = item["metric"]
    return t(
        "kpi.line",
        label=item["label"],
        current=_kpi_value(metric, item["current"]),
        previous=_kpi_value(metric, item["previous"]),
        target=_kpi_value(metric, item.get("target")),
        status=t(f"kpi.status.{item['status']}"),
    )


@router.post("/kpi/report", summary="產生 KPI 報表（HTML；本期／上期／目標／達成狀態）")
def kpi_report_file(
    principal: Agency, db: DB, period: Literal["week", "month"] = "month"
) -> dict[str, Any]:
    data = _kpis(db, period)
    with session_scope(DbRole.ANON_READER) as s:
        ctx = ToolContext(agent="api", db=s, actor_type="staff", actor_id=str(principal.account_id))
        built = registry.call(
            "report_build",
            {
                "title": t(f"kpi.report_title.{period}"),
                "sections": [
                    {
                        "heading": t("kpi.section.health"),
                        "body": "\n".join(f"- {_kpi_line(i)}" for i in data["items"]),
                    },
                    {
                        "heading": t("kpi.section.app"),
                        "body": "\n".join(f"- {_kpi_line(i)}" for i in data["app"])
                        or t("kpi.app_empty", k=data["k"]),
                    },
                    {"heading": t("kpi.section.notes"), "body": t("kpi.notes", k=data["k"])},
                ],
                "appendix": [kpi.KPI_SQL.strip(), kpi.APP_SQL.strip()],
            },
            ctx,
        ).data
    assert built is not None
    return {**built, "kpi": data}


@router.get("/care-lists", summary="關懷清單數量（n ≥ k 才公開；不含代號）")
def care_lists(_: Agency, day: date | None = None) -> dict[str, Any]:
    with session_scope(DbRole.ANON_READER) as s:
        target = day or s.scalar(
            select(CareListDaily.day).order_by(CareListDaily.day.desc()).limit(1)
        )
        rows = s.scalars(select(CareListDaily).where(CareListDaily.day == target)).all()
        return {
            "day": target.isoformat() if target else None,
            "rows": [{"site_id": c.site_id, "task_type": c.task_type, "n": c.n} for c in rows],
        }


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=800)


@router.post(
    "/chat",
    summary="與 A2 公衛 Agent 對話（SSE；A2 會委派 A3／A4）",
    response_class=StreamingResponse,
)
def chat(body: ChatIn, principal: Agency) -> StreamingResponse:
    ratelimit.check(f"chat:{principal.account_id}", limit=get_settings().rate_chat_per_min)
    actor = _actor(principal)
    return stream_agent(lambda: a2.chat(actor, body.message))


class BriefingIn(BaseModel):
    occasion: str = Field(default="業務會議", max_length=40)
    topic: str = Field(min_length=2, max_length=200)


@router.post(
    "/briefing", summary="產生談參（A2 委派 A3／A4 後以 briefing_pack 整理；草稿需主管定稿）"
)
def briefing(body: BriefingIn, principal: Agency) -> dict[str, Any]:
    done: DoneEvent | None = None
    tools: list[dict[str, Any]] = []
    for event in a2.chat(
        _actor(principal), t("a2.briefing_prompt", occasion=body.occasion, topic=body.topic)
    ):
        if isinstance(event, DoneEvent):
            done = event
    if done is not None:
        tools = done.tool_calls
    return {
        "occasion": body.occasion,
        "topic": body.topic,
        "markdown": done.text if done else "",
        "meta": done.meta if done else None,
        "tools": tools,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/artifacts/{name}", summary="下載分析產物（圖表、報告、沙箱輸出）")
def artifact(_: Agency, name: str) -> FileResponse:
    path = artifacts.path_of(name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("artifact.error.not_found"))
    # 沙箱輸出可能是任意 HTML／SVG：一律 CSP sandbox，不讓它在本站來源下執行腳本
    return FileResponse(
        path,
        media_type=artifacts.MEDIA[path.suffix.lstrip(".")],
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data:",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )
