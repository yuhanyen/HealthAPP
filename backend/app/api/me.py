"""民眾 API（SPEC §9 民眾）。

讀取本人資料的端點一律用 idn_user_scoped（RLS 只放行本人），多一道防線；
需要跨人或寫入設定的端點（兌換確認、偏好）才用 idn_app。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.actions.state_machine import confirm
from app.agents import a1
from app.api import ratelimit
from app.api.deps import DB
from app.api.sse import stream_agent
from app.auth.deps import CurrentCitizen
from app.auth.user_qr import issue_user_qr
from app.config import get_settings
from app.consent.service import effective_scope
from app.db.engines import user_scoped_session
from app.db.models_idn import Booking, DailyReport, Notification, PointsLedger, Redemption, User
from app.db.models_pub import Offer, Site, Village
from app.i18n import t
from app.services import points
from app.tools._common import age_band, profile, today
from app.tools.registry import ToolContext, registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/me", tags=["me 民眾"])


def _uid(principal: CurrentCitizen) -> uuid.UUID:
    assert principal.user_id is not None
    return principal.user_id


class QrOut(BaseModel):
    token: str
    expires_at: datetime


@router.get("/qr", response_model=QrOut, summary="我的量測 QR（10 分鐘有效）")
def my_qr(principal: CurrentCitizen) -> QrOut:
    token, expires = issue_user_qr(_uid(principal))
    return QrOut(token=token, expires_at=expires)


# --- 個人資料與偏好 ---


class ProfileOut(BaseModel):
    name: str
    age: int
    age_band: str
    village_code: str
    village_name: str | None
    elder_mode: bool


def _profile_out(session: Session, uid: uuid.UUID) -> ProfileOut:
    user = session.get_one(User, uid)
    me = profile(session, uid)
    village = session.get(Village, user.village_code)
    return ProfileOut(
        name=user.name,
        age=me.age,
        age_band=age_band(me.age),
        village_code=user.village_code,
        village_name=village.name if village else None,
        elder_mode=user.elder_mode,
    )


@router.get("/profile", response_model=ProfileOut, summary="我的基本資料與偏好")
def my_profile(principal: CurrentCitizen) -> ProfileOut:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        return _profile_out(s, uid)


class PrefsIn(BaseModel):
    elder_mode: bool


@router.put("/prefs", response_model=ProfileOut, summary="切換長者模式（大字、高對比、語音播報）")
def update_prefs(body: PrefsIn, principal: CurrentCitizen, db: DB) -> ProfileOut:
    uid = _uid(principal)
    user = db.get_one(User, uid)
    user.elder_mode = body.elder_mode
    db.flush()
    return _profile_out(db, uid)


# --- 雙生與報告 ---


class TwinOut(BaseModel):
    as_of: date | None
    state: dict[str, Any] | None


@router.get("/twin", response_model=TwinOut, summary="我的數位雙生快照（最新）")
def my_twin(principal: CurrentCitizen) -> TwinOut:
    twin = a1.latest_twin(_uid(principal))
    return TwinOut(as_of=twin.as_of if twin else None, state=twin.state if twin else None)


class ReportOut(BaseModel):
    date: date
    content: dict[str, Any]
    delivered_at: datetime | None
    opened_at: datetime | None


@router.get("/reports", response_model=ReportOut, summary="每日報告（預設最新一份）")
def my_report(principal: CurrentCitizen, date: date | None = None) -> ReportOut:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        stmt = select(DailyReport).where(DailyReport.user_id == uid)
        stmt = (
            stmt.where(DailyReport.report_date == date)
            if date
            else stmt.order_by(DailyReport.report_date.desc()).limit(1)
        )
        report = s.scalars(stmt).first()
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("me.error.no_report"))
        if report.opened_at is None:
            report.opened_at = datetime.now(UTC)
        return ReportOut(
            date=report.report_date,
            content=report.content,
            delivered_at=report.delivered_at,
            opened_at=report.opened_at,
        )


class ReportBrief(BaseModel):
    date: date
    status_sentence: str
    risk_level: str | None


@router.get("/reports/history", response_model=list[ReportBrief], summary="近期報告清單")
def my_report_history(principal: CurrentCitizen, limit: int = 14) -> list[ReportBrief]:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        rows = s.scalars(
            select(DailyReport)
            .where(DailyReport.user_id == uid)
            .order_by(DailyReport.report_date.desc())
            .limit(min(limit, 60))
        )
        return [
            ReportBrief(
                date=r.report_date,
                status_sentence=r.content.get("status_sentence", ""),
                risk_level=(r.content.get("risk") or {}).get("level"),
            )
            for r in rows
        ]


# --- 與 A1 對話（SSE） ---


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


@router.post(
    "/chat",
    summary="與 A1 對話（SSE 串流：text／tool／confirmation／done）",
    response_class=StreamingResponse,
)
def chat(body: ChatIn, principal: CurrentCitizen) -> StreamingResponse:
    uid = _uid(principal)
    ratelimit.check(f"chat:{principal.account_id}", limit=get_settings().rate_chat_per_min)
    return stream_agent(lambda: a1.chat(uid, body.message))


class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime


@router.get("/chat", response_model=list[ChatMessageOut], summary="最近的對話")
def chat_history(principal: CurrentCitizen) -> list[ChatMessageOut]:
    return [
        ChatMessageOut(role=m.role, content=m.content, created_at=m.created_at)
        for m in a1.history(_uid(principal), limit=30)
    ]


# --- 點數與兌換 ---


class LedgerOut(BaseModel):
    delta: int
    reason: str
    balance_after: int
    created_at: datetime


class RedemptionOut(BaseModel):
    id: uuid.UUID
    offer: str
    merchant_id: str
    points: int
    qr_token: str | None
    status: str
    redeemed_at: datetime


class PointsOut(BaseModel):
    balance: int
    expiring: list[dict[str, Any]]
    ledger: list[LedgerOut]
    redemptions: list[RedemptionOut]


def _reason_label(reason: str) -> str:
    try:
        return t(f"points.reason.{reason}")
    except (KeyError, TypeError):
        return t("points.reason.other")


@router.get("/points", response_model=PointsOut, summary="點數餘額、即將到期、流水、兌換券")
def my_points(principal: CurrentCitizen) -> PointsOut:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        ledger = s.scalars(
            select(PointsLedger)
            .where(PointsLedger.user_id == uid)
            .order_by(PointsLedger.id.desc())
            .limit(30)
        )
        redemptions = s.execute(
            select(Redemption, Offer.title)
            .join(Offer, Offer.id == Redemption.offer_id)
            .where(Redemption.user_id == uid)
            .order_by(Redemption.redeemed_at.desc())
            .limit(20)
        )
        return PointsOut(
            balance=points.balance(s, uid),
            expiring=points.expiring(s, uid, today()),
            ledger=[
                LedgerOut(
                    delta=row.delta,
                    reason=_reason_label(row.reason),
                    balance_after=row.balance_after,
                    created_at=row.created_at,
                )
                for row in ledger
            ],
            redemptions=[
                RedemptionOut(
                    id=r.id,
                    offer=title,
                    merchant_id=r.merchant_id,
                    points=r.points,
                    # 已核銷的券不再顯示兌換碼
                    qr_token=r.qr_token if r.status == "issued" else None,
                    status=r.status,
                    redeemed_at=r.redeemed_at,
                )
                for r, title in redemptions
            ],
        )


def _api_ctx(db: Session, uid: uuid.UUID) -> ToolContext:
    """民眾直接在 App 操作（非 A1 代理）：同樣經工具層，稽核身分記為本人。"""
    return ToolContext(
        agent="api", db=db, principal_user_id=uid, actor_type="user", actor_id=str(uid)
    )


@router.get("/offers", summary="附近可兌換的商家與優惠（預設以住家所在里為中心）")
def my_offers(
    principal: CurrentCitizen, db: DB, lat: float | None = None, lng: float | None = None
) -> dict[str, Any]:
    uid = _uid(principal)
    args: dict[str, Any] = {"user_id": uid, "consent_scope": effective_scope(db, uid)}
    if lat is not None and lng is not None:
        args |= {"lat": lat, "lng": lng}
    result = registry.call("merchant_lookup", args, _api_ctx(db, uid))
    return result.data or {"merchants": []}


class RedeemIn(BaseModel):
    offer_id: str
    confirmed: bool = False


class ActionResultOut(BaseModel):
    status: Literal["confirmation_required", "executed", "failed"]
    action_id: uuid.UUID
    summary: str
    result: dict[str, Any] | None = None


@router.post(
    "/redeem",
    response_model=ActionResultOut,
    responses={202: {"description": "需本人確認（回傳 action_id）"}},
    summary="用點數兌換優惠（高風險：需本人確認）",
)
def redeem(body: RedeemIn, principal: CurrentCitizen, db: DB, response: Response) -> Any:
    """不帶 confirmed：回 202 與待確認動作。App 已向本人顯示確認卡片時可帶 confirmed=true，
    同樣經確認狀態機執行並留稽核，成功後回傳兌換碼（qr_token）。"""
    uid = _uid(principal)
    pending = registry.call(
        "points_engine",
        {
            "user_id": uid,
            "consent_scope": effective_scope(db, uid),
            "action": "redeem",
            "offer_id": body.offer_id,
        },
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


# --- 通知與預約 ---


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    payload: dict[str, Any] | None
    created_at: datetime
    read_at: datetime | None


@router.get("/notifications", response_model=list[NotificationOut], summary="通知收件匣")
def my_notifications(principal: CurrentCitizen) -> list[NotificationOut]:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        rows = s.scalars(
            select(Notification)
            .where(Notification.user_id == uid)
            .order_by(Notification.created_at.desc())
            .limit(30)
        )
        return [NotificationOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/notifications/{notification_id}/read", status_code=204, summary="標為已讀")
def read_notification(notification_id: uuid.UUID, principal: CurrentCitizen) -> Response:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        s.execute(
            update(Notification)
            .where(Notification.id == notification_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
        )
    return Response(status_code=204)


class BookingOut(BaseModel):
    id: uuid.UUID
    site_id: str
    site_name: str
    slot_at: datetime
    purpose: str
    status: str


class BookIn(BaseModel):
    site_id: str
    slot_at: datetime
    purpose: str = Field(default="血壓複測", max_length=40)
    confirmed: bool = False


@router.post(
    "/bookings",
    response_model=ActionResultOut,
    responses={202: {"description": "需本人確認（回傳 action_id）"}},
    summary="預約據點時段（高風險：需本人確認）",
)
def book(body: BookIn, principal: CurrentCitizen, db: DB, response: Response) -> Any:
    """與 /me/redeem 相同：不帶 confirmed 回 202；App 已顯示確認卡片時可帶 confirmed=true。"""
    uid = _uid(principal)
    pending = registry.call(
        "site_booking",
        {
            "user_id": uid,
            "consent_scope": effective_scope(db, uid),
            "mode": "book",
            "site_id": body.site_id,
            "slot_at": body.slot_at,
            "purpose": body.purpose,
        },
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


@router.get("/bookings", response_model=list[BookingOut], summary="我接下來的預約")
def my_bookings(principal: CurrentCitizen) -> list[BookingOut]:
    uid = _uid(principal)
    with user_scoped_session(uid) as s:
        rows = s.execute(
            select(Booking, Site.name)
            .join(Site, Site.id == Booking.site_id)
            .where(Booking.user_id == uid, Booking.slot_at >= datetime.now(UTC))
            .order_by(Booking.slot_at)
        )
        return [
            BookingOut(
                id=b.id,
                site_id=b.site_id,
                site_name=name,
                slot_at=b.slot_at,
                purpose=b.purpose,
                status=b.status,
            )
            for b, name in rows
        ]
