"""民眾端：推薦偏好、近日社區活動（推薦、報名、取消）、推薦店家（SPEC §17.6）。

推薦與報名都經工具層（C-3 授權範圍、C-5 確認、C-6 稽核），與 A1 代理時走同一條路。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.actions.state_machine import confirm
from app.api.deps import DB
from app.api.me import ActionResultOut, _api_ctx, _uid
from app.auth.deps import CurrentCitizen
from app.consent.service import effective_scope
from app.db.models_idn import EventRegistration
from app.db.models_pub import EVENT_CATEGORIES, Event
from app.i18n import t
from app.services import events as ev
from app.services import prefs
from app.services.events import EventError
from app.tools.merchant_recommend import DINING, TAGS
from app.tools.registry import registry

router = APIRouter(prefix="/me", tags=["me 民眾"])


class PrefsOut(BaseModel):
    event_categories: list[str]
    merchant_categories: list[str]
    dietary: list[str]
    min_rating: float | None
    max_distance_km: float | None
    event_push: bool
    rating_threshold: float
    saved: bool
    options: dict[str, list[dict[str, str]]]


class PrefsIn(BaseModel):
    event_categories: list[str] = Field(default_factory=list, max_length=10)
    merchant_categories: list[str] = Field(default_factory=list, max_length=20)
    dietary: list[str] = Field(default_factory=list, max_length=10)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    max_distance_km: float | None = Field(default=None, gt=0, le=20)
    event_push: bool = True


def _options() -> dict[str, list[dict[str, str]]]:
    return {
        "event_categories": [
            {"value": c, "label": t(f"events.category.{c}")} for c in EVENT_CATEGORIES
        ],
        "merchant_categories": [{"value": c, "label": c} for c in DINING],
        "dietary": [{"value": d, "label": t(f"rec.tag.{d}")} for d in prefs.DIETARY],
    }


def _prefs_out(p: prefs.Prefs) -> PrefsOut:
    return PrefsOut(**p.as_dict(), options=_options())


@router.get(
    "/prefs/recommend", response_model=PrefsOut, summary="推薦偏好（活動興趣、店家、飲食、門檻）"
)
def get_recommend_prefs(principal: CurrentCitizen, db: DB) -> PrefsOut:
    return _prefs_out(prefs.get_prefs(db, _uid(principal)))


@router.put("/prefs/recommend", response_model=PrefsOut, summary="更新推薦偏好")
def put_recommend_prefs(body: PrefsIn, principal: CurrentCitizen, db: DB) -> PrefsOut:
    allowed_tags = set(TAGS)
    saved = prefs.save_prefs(
        db,
        _uid(principal),
        prefs.Prefs(
            event_categories=[c for c in body.event_categories if c in EVENT_CATEGORIES],
            merchant_categories=body.merchant_categories,
            dietary=[d for d in body.dietary if d in allowed_tags],
            min_rating=body.min_rating,
            max_distance_km=body.max_distance_km,
            event_push=body.event_push,
        ),
    )
    return _prefs_out(saved)


@router.get("/events", summary="推薦的近日社區活動，以及我的報名")
def my_events(principal: CurrentCitizen, db: DB, days: int = 14) -> dict[str, Any]:
    uid = _uid(principal)
    args = {
        "user_id": uid,
        "consent_scope": effective_scope(db, uid),
        "days": min(max(days, 1), 60),
    }
    recommended = registry.call("event_recommend", {**args, "limit": 10}, _api_ctx(db, uid)).data
    rows = db.execute(
        select(EventRegistration, Event)
        .join(Event, Event.id == EventRegistration.event_id)
        .where(EventRegistration.user_id == uid, EventRegistration.status != "cancelled")
        .order_by(Event.start_at.desc())
        .limit(30)
    ).all()
    mine = [
        {
            **ev.event_out(event),
            "registration": reg.status,
            "points_granted": reg.points_granted,
            "checked_in_at": reg.checked_in_at.isoformat() if reg.checked_in_at else None,
        }
        for reg, event in rows
    ]
    return {**(recommended or {"events": []}), "mine": mine}


class RegisterIn(BaseModel):
    confirmed: bool = False


@router.post(
    "/events/{event_id}/register",
    response_model=ActionResultOut,
    responses={202: {"description": "需本人確認（回傳 action_id）"}},
    summary="報名活動（高風險：報名會把姓名與電話交給主辦單位，需本人確認）",
)
def register(
    event_id: str, body: RegisterIn, principal: CurrentCitizen, db: DB, response: Response
) -> Any:
    uid = _uid(principal)
    if db.get(Event, event_id) is None:
        raise EventError("not_found")
    pending = registry.call(
        "event_register",
        {"user_id": uid, "consent_scope": effective_scope(db, uid), "event_id": event_id},
        _api_ctx(db, uid),
    )
    assert pending.action_id is not None and pending.data is not None
    if not body.confirmed:
        response.status_code = status.HTTP_202_ACCEPTED
        return ActionResultOut(
            status="confirmation_required",
            action_id=pending.action_id,
            summary=pending.data["summary"],
        )
    action = confirm(db, pending.action_id, user_id=uid)
    return ActionResultOut(
        status="executed" if action.status == "executed" else "failed",
        action_id=action.id,
        summary=action.summary,
        result=action.result,
    )


@router.post("/events/{event_id}/cancel", summary="取消報名")
def cancel(event_id: str, principal: CurrentCitizen, db: DB) -> dict[str, Any]:
    uid = _uid(principal)
    result = registry.call(
        "event_register",
        {
            "user_id": uid,
            "consent_scope": effective_scope(db, uid),
            "event_id": event_id,
            "action": "cancel",
        },
        _api_ctx(db, uid),
    )
    return result.data or {}


@router.get("/merchants/recommend", summary="推薦店家（依偏好與評分門檻；附略過家數）")
def recommend_merchants(
    principal: CurrentCitizen, db: DB, category: str | None = None
) -> dict[str, Any]:
    uid = _uid(principal)
    args: dict[str, Any] = {"user_id": uid, "consent_scope": effective_scope(db, uid)}
    if category:
        args["category"] = category
    return registry.call("merchant_recommend", args, _api_ctx(db, uid)).data or {"merchants": []}
