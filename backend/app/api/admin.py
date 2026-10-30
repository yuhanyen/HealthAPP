"""系統管理（SPEC §9 系統）：稽核查詢、可調設定，以及 M1 驗收用的事件流與刪除任務檢視。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import DB, Bus
from app.auth.deps import CurrentAdmin
from app.config import get_settings
from app.db.models_idn import DeletionJob, PointsReview, User
from app.db.models_sys import AuditLog, Config
from app.masking import mask_name
from app.services import points
from app.services.reconcile import reconcile
from app.sysconfig import get_value, set_value

router = APIRouter(prefix="/admin", tags=["admin 系統管理"])

# 可由管理端調整的設定與驗證規則
CONFIG_RULES: dict[str, tuple[type, Any]] = {
    "k": (int, range(2, 51)),
    "step_goal_default": (int, range(3000, 10001)),
    "event_push_weekly_cap": (int, range(0, 8)),
}


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor_type: str
    actor_id: str
    action: str
    tool: str | None
    consent_scope: dict[str, Any] | None
    input_digest: str | None
    output_digest: str | None
    decision: str
    reason: str | None


class ConfigIn(BaseModel):
    key: str
    value: Any


class DeletionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    status: str
    requested_at: datetime
    due_at: datetime
    executed_at: datetime | None
    deleted_counts: dict[str, Any] | None


class EventOut(BaseModel):
    id: str
    event: dict[str, Any] = Field(description="SPEC §6 訊息本體")


@router.get("/audit", response_model=list[AuditOut], summary="稽核日誌查詢")
def audit(
    _: CurrentAdmin,
    db: DB,
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    limit: int = Query(100, le=1000),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if actor:
        stmt = stmt.where(AuditLog.actor_id == actor)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if since:
        stmt = stmt.where(AuditLog.at >= since)
    return list(db.scalars(stmt))


@router.get("/config", summary="可調設定")
def get_config(_: CurrentAdmin, db: DB) -> dict[str, Any]:
    return {row.key: row.value for row in db.scalars(select(Config).order_by(Config.key))}


@router.put("/config", summary="變更設定（寫稽核）")
def put_config(body: ConfigIn, admin: CurrentAdmin, db: DB) -> dict[str, Any]:
    rule = CONFIG_RULES.get(body.key)
    if rule is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"不可調整的設定：{body.key}")
    kind, allowed = rule
    if not isinstance(body.value, kind) or body.value not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{body.key} 的值不在允許範圍")
    set_value(db, body.key, body.value, actor_type="admin", actor_id=str(admin.account_id))
    return {body.key: body.value}


@router.get("/events/recent", response_model=list[EventOut], summary="事件匯流最近訊息")
def recent_events(_: CurrentAdmin, bus: Bus, count: int = Query(30, le=200)) -> list[EventOut]:
    return [EventOut(id=d.id, event=d.event) for d in bus.recent(count)]


@router.get("/constraints", summary="八條約束測試最近一次結果（由 make test 產生）")
def constraints_report(_: CurrentAdmin) -> dict[str, Any]:
    path = get_settings().reports_dir / "constraints.json"
    if not path.exists():
        return {"generated_at": None, "constraints": {}}
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report


@router.get("/deletion-jobs", response_model=list[DeletionJobOut], summary="撤回後刪除任務")
def deletion_jobs(_: CurrentAdmin, db: DB, limit: int = Query(50, le=500)) -> list[DeletionJob]:
    return list(
        db.scalars(select(DeletionJob).order_by(DeletionJob.requested_at.desc()).limit(limit))
    )


# --- 點數：對帳與防作弊審核（SPEC §14 M6） ---


@router.get("/points/reconcile", summary="點數對帳（帳本累加、兌換券扣點、全體平衡；只有筆數）")
def points_reconcile(_: CurrentAdmin, db: DB) -> dict[str, Any]:
    return reconcile(db)


class ReviewOut(BaseModel):
    id: int
    display_name: str
    day: date
    reason: str
    detail: dict[str, Any] | None
    status: str
    created_at: datetime


@router.get("/points/reviews", response_model=list[ReviewOut], summary="防作弊標記待審（姓名遮罩）")
def points_reviews(
    _: CurrentAdmin, db: DB, status_filter: str = Query("pending", alias="status")
) -> list[ReviewOut]:
    rows = db.execute(
        select(PointsReview, User.name)
        .join(User, User.id == PointsReview.user_id)
        .where(PointsReview.status == status_filter)
        .order_by(PointsReview.created_at.desc())
        .limit(100)
    ).all()
    return [
        ReviewOut(
            id=r.id,
            display_name=mask_name(name),
            day=r.day,
            reason=r.reason,
            detail=r.detail,
            status=r.status,
            created_at=r.created_at,
        )
        for r, name in rows
    ]


class ReviewDecision(BaseModel):
    decision: Literal["cleared", "rejected"]


@router.post("/points/reviews/{review_id}", summary="審核防作弊標記：放行（達標則補發點）或駁回")
def resolve_points_review(
    review_id: int, body: ReviewDecision, admin: CurrentAdmin, db: DB
) -> dict[str, Any]:
    review, granted = points.resolve_review(
        db,
        review_id,
        cleared=body.decision == "cleared",
        actor_id=str(admin.account_id),
        goal=int(get_value(db, "step_goal_default", 6000)),
    )
    return {"id": review.id, "status": review.status, "granted": granted}


# --- 活動公告與商家評分（SPEC §17.2；排程每 6 小時，這裡可立即執行） ---


@router.post("/events/sync", summary="立即匯入公開活動公告（寫成待審，機關審核後才推給民眾）")
def sync_event_notices(_: CurrentAdmin) -> dict[str, Any]:
    from app.adapters.events_land import sync_events

    return asdict(sync_events())


@router.post("/merchants/ratings/sync", summary="立即更新商家公開評分")
def sync_ratings(_: CurrentAdmin) -> dict[str, Any]:
    from app.adapters.events_land import sync_merchant_ratings

    return asdict(sync_merchant_ratings())
