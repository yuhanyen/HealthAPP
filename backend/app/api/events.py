"""活動管理 API（SPEC §17.6）：據點人員與機關人員上架、審核公開公告、取消、報名名單、報到。

- 活動本身是公開資訊（pub.events），以 API 帳號（idn_app）寫入，與商家上架優惠相同。
- 機關端看得到各場報名數，但 < k 時只顯示「少於 k」；報名名單（姓名遮罩）只有該據點人員看得到。
- 每次上架、審核、取消、查看名單、報到都寫稽核。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB
from app.audit.writer import write_audit
from app.auth.deps import require_roles
from app.auth.security import Principal
from app.auth.user_qr import InvalidUserQr, read_user_qr
from app.db.models_idn import EventRegistration, User
from app.db.models_pub import EVENT_CATEGORIES, Event, Site
from app.i18n import t
from app.masking import mask_name
from app.services import events as ev
from app.sysconfig import get_k

router = APIRouter(prefix="/events", tags=["events 活動"])

Staff = Annotated[Principal, Depends(require_roles("agency", "site_staff", "admin"))]
Reviewer = Annotated[Principal, Depends(require_roles("agency", "admin"))]
TAGS = ("bp", "glucose", "weight", "steps", "indoor", "outdoor", "gentle", "free")


def _actor(principal: Principal) -> str:
    return str(principal.account_id)


def _load(db: Session, event_id: str) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("events.error.not_found"))
    return event


def _can_manage(principal: Principal, event: Event) -> None:
    if principal.role == "site_staff" and event.site_id != principal.site_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("site.error.not_your_site"))


def _audit(db: Session, principal: Principal, action: str, **data: Any) -> None:
    write_audit(
        db,
        actor_type="staff",
        actor_id=_actor(principal),
        action=action,
        input=data,
        decision="executed",
    )


def _with_counts(db: Session, principal: Principal, events: list[Event]) -> list[dict[str, Any]]:
    k = get_k(db)
    counts = ev.counts(db, [e.id for e in events])
    out = []
    for event in events:
        c = counts[event.id]
        total = c["registered"] + c["attended"]
        own = principal.role == "admin" or (
            principal.role == "site_staff" and principal.site_id == event.site_id
        )
        masked = not own and 0 < total < k
        out.append(
            {
                **ev.event_out(event),
                "registered": None if masked else total,
                "attended": None if masked else c["attended"],
                "count_masked": masked,
                "count_text": t("events.count_masked", k=k) if masked else None,
                "reviewed_by": event.reviewed_by,
                "created_at": event.created_at.isoformat(),
            }
        )
    return out


@router.get("", summary="活動清單（據點人員只看本據點；機關看全部，報名數 < k 遮蔽）")
def list_events(
    principal: Staff,
    db: DB,
    state: Annotated[
        Literal["pending_review", "published", "rejected", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
    upcoming: bool = False,
) -> list[dict[str, Any]]:
    stmt = select(Event).order_by(Event.start_at)
    if state:
        stmt = stmt.where(Event.status == state)
    if upcoming:
        stmt = stmt.where(Event.end_at > datetime.now(UTC))
    if principal.role == "site_staff":
        stmt = stmt.where(Event.site_id == principal.site_id)
    return _with_counts(db, principal, list(db.scalars(stmt.limit(200))))


@router.get("/sites", summary="可指定的據點（上架活動用；pub 非個資）")
def sites(principal: Staff, db: DB) -> list[dict[str, str]]:
    stmt = select(Site.id, Site.name).where(Site.active).order_by(Site.id)
    if principal.role == "site_staff":
        stmt = stmt.where(Site.id == principal.site_id)
    return [{"id": sid, "name": name} for sid, name in db.execute(stmt)]


class EventIn(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    category: Literal["measure", "walk", "exercise", "education", "nutrition", "vaccine", "social"]
    description: str = Field(default="", max_length=400)
    site_id: str | None = None
    venue: str | None = Field(default=None, max_length=80)
    start_at: datetime
    end_at: datetime
    capacity: int | None = Field(default=None, ge=1, le=2000)
    points_reward: int = Field(default=0, ge=0, le=200)
    tags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _window(self) -> EventIn:
        if self.end_at <= self.start_at:
            raise ValueError("結束時間要晚於開始時間")
        return self


assert set(EVENT_CATEGORIES) == {
    "measure",
    "walk",
    "exercise",
    "education",
    "nutrition",
    "vaccine",
    "social",
}


@router.post("", summary="上架活動（據點人員：本據點；機關：任一據點或不指定）")
def create_event(body: EventIn, principal: Staff, db: DB) -> dict[str, Any]:
    site_id = principal.site_id if principal.role == "site_staff" else body.site_id
    site = db.get(Site, site_id) if site_id else None
    if site_id and site is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, t("events.error.bad_site"))
    event = Event(
        id=f"EV-{uuid.uuid4().hex[:8].upper()}",
        title=body.title,
        category=body.category,
        description=body.description,
        organizer=site.name if site else t("events.organizer_agency"),
        site_id=site_id,
        village_code=site.village_code if site else None,
        venue=body.venue or (site.name if site else "—"),
        start_at=body.start_at,
        end_at=body.end_at,
        capacity=body.capacity,
        points_reward=body.points_reward,
        tags=[tag for tag in body.tags if tag in TAGS],
        source="site" if principal.role == "site_staff" else "agency",
        status="published",
        created_by=_actor(principal),
    )
    db.add(event)
    db.flush()
    _audit(db, principal, "event.create", event_id=event.id, site_id=site_id)
    return _with_counts(db, principal, [event])[0]


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    title: str | None = Field(default=None, min_length=2, max_length=80)
    category: (
        Literal["measure", "walk", "exercise", "education", "nutrition", "vaccine", "social"] | None
    ) = None
    site_id: str | None = None
    venue: str | None = Field(default=None, max_length=80)
    start_at: datetime | None = None
    end_at: datetime | None = None
    points_reward: int | None = Field(default=None, ge=0, le=200)
    capacity: int | None = Field(default=None, ge=1, le=2000)
    description: str | None = Field(default=None, max_length=400)
    tags: list[str] | None = Field(default=None, max_length=8)


@router.post(
    "/{event_id}/review",
    summary="審核公開公告匯入的活動（上架前可修正擷取欄位；只有機關人員）",
)
def review_event(event_id: str, body: ReviewIn, principal: Reviewer, db: DB) -> dict[str, Any]:
    event = _load(db, event_id)
    if event.status != "pending_review":
        raise HTTPException(status.HTTP_409_CONFLICT, t("events.error.not_pending"))
    edits = body.model_dump(exclude_none=True, exclude={"decision"})
    if "site_id" in edits:
        site = db.get(Site, edits["site_id"])
        if site is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, t("events.error.bad_site"))
        edits["village_code"] = site.village_code
    if "tags" in edits:
        edits["tags"] = [tag for tag in edits["tags"] if tag in TAGS]
    for field, value in edits.items():
        setattr(event, field, value)
    if event.end_at <= event.start_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, t("events.error.window"))
    event.status = "published" if body.decision == "approve" else "rejected"
    event.reviewed_by = _actor(principal)
    event.reviewed_at = datetime.now(UTC)
    db.flush()
    _audit(db, principal, f"event.review.{body.decision}", event_id=event.id, edited=sorted(edits))
    return _with_counts(db, principal, [event])[0]


@router.post("/{event_id}/cancel", summary="取消活動（通知已報名者；推播只有活動名稱與時間）")
def cancel_event(event_id: str, principal: Staff, db: DB) -> dict[str, Any]:
    event = _load(db, event_id)
    _can_manage(principal, event)
    if event.status not in ("published", "pending_review"):
        raise HTTPException(status.HTTP_409_CONFLICT, t("events.error.not_active"))
    notified = ev.cancel_event(db, event, actor=_actor(principal))
    _audit(db, principal, "event.cancel", event_id=event.id, notified=notified)
    return {"event_id": event.id, "status": event.status, "notified": notified}


@router.get(
    "/{event_id}/registrations",
    summary="報名名單（只有本據點人員；姓名遮罩；每次查看留稽核）",
)
def registrations(event_id: str, principal: Staff, db: DB) -> dict[str, Any]:
    event = _load(db, event_id)
    if principal.role == "agency":
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("events.error.list_forbidden"))
    _can_manage(principal, event)
    rows = db.execute(
        select(EventRegistration, User.name)
        .join(User, User.id == EventRegistration.user_id)
        .where(EventRegistration.event_id == event.id, EventRegistration.status != "cancelled")
        .order_by(EventRegistration.registered_at)
    ).all()
    _audit(db, principal, "event.registrations.view", event_id=event.id, rows=len(rows))
    return {
        "event": ev.event_out(event),
        "rows": [
            {
                "person": mask_name(name),
                "status": reg.status,
                "registered_at": reg.registered_at.isoformat(),
                "checked_in_at": reg.checked_in_at.isoformat() if reg.checked_in_at else None,
                "points_granted": reg.points_granted,
            }
            for reg, name in rows
        ],
    }


class CheckinIn(BaseModel):
    user_qr: str = Field(min_length=10, max_length=400)


@router.post(
    "/{event_id}/checkin", summary="報到（掃民眾 QR；有點數的活動報到後發點，同一場只發一次）"
)
def checkin(event_id: str, body: CheckinIn, principal: Staff, db: DB) -> dict[str, Any]:
    event = _load(db, event_id)
    if principal.role == "agency":
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("events.error.list_forbidden"))
    _can_manage(principal, event)
    try:
        user_id = read_user_qr(body.user_qr)
    except InvalidUserQr:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("site.error.bad_qr")) from None
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("site.error.bad_qr"))
    result = ev.check_in(db, event=event, user_id=user_id, staff_account_id=principal.account_id)
    _audit(
        db,
        principal,
        "event.checkin",
        event_id=event.id,
        points=result.points,
        walk_in=result.walk_in,
    )
    return {
        "person": mask_name(user.name),
        "status": result.registration.status,
        "points": result.points,
        "walk_in": result.walk_in,
        "message": t(
            "events.checked_in",
            person=mask_name(user.name),
            points=t("events.checked_in_points", points=result.points) if result.points else "",
        ),
    }
