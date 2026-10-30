"""近日社區活動（SPEC §17）：查詢、名額、報名、取消、報到發點、活動取消通知。

報名與報到是個資（idn）；名額只用 idn.event_seats_taken() 取「人數」，A1 的 RLS 連線也能算。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models_idn import EventRegistration
from app.db.models_pub import Event, Site
from app.i18n import t, t_list
from app.services import geo, points
from app.services.notify import notify
from app.tools._common import tz

CHECKIN_BEFORE = timedelta(hours=1)
CHECKIN_AFTER = timedelta(hours=2)
ACTIVE = ("registered", "attended")


class EventError(Exception):
    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        super().__init__(t(f"events.error.{key}", **params))


def when_text(start: datetime, end: datetime) -> str:
    s, e = start.astimezone(tz()), end.astimezone(tz())
    weekday = t_list("a1.weekday")[s.weekday()]
    return f"{s.month}/{s.day}（{weekday}）{s:%H:%M}–{e:%H:%M}"


def location(session: Session, event: Event) -> tuple[float, float] | None:
    if event.lat is not None and event.lng is not None:
        return event.lat, event.lng
    if event.site_id:
        site = session.get(Site, event.site_id)
        if site is not None:
            return site.lat, site.lng
    if event.village_code:
        return geo.village_centroids(session).get(event.village_code)
    return None


def home_point(session: Session, village_code: str) -> tuple[float, float] | None:
    """住家所在里的中心點；里沒有圖資時，退回同里的第一個據點。"""
    centre = geo.village_centroids(session).get(village_code)
    if centre is not None:
        return centre
    row = session.execute(
        select(Site.lat, Site.lng).where(Site.village_code == village_code, Site.active).limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def upcoming(session: Session, *, now: datetime, days: int) -> list[Event]:
    return list(
        session.scalars(
            select(Event)
            .where(
                Event.status == "published",
                Event.end_at > now,
                Event.start_at < now + timedelta(days=days),
            )
            .order_by(Event.start_at)
        )
    )


def seats_taken(session: Session, event_ids: list[str]) -> dict[str, int]:
    if not event_ids:
        return {}
    rows = session.execute(
        text("SELECT event_id, taken FROM idn.event_seats_taken(:ids)"), {"ids": event_ids}
    )
    return {event_id: int(taken) for event_id, taken in rows}


def my_registrations(session: Session, user_id: uuid.UUID) -> dict[str, EventRegistration]:
    rows = session.scalars(select(EventRegistration).where(EventRegistration.user_id == user_id))
    return {r.event_id: r for r in rows}


def register(
    session: Session, *, user_id: uuid.UUID, event_id: str, created_by: str
) -> EventRegistration:
    event = session.get(Event, event_id, with_for_update=True)
    if event is None or event.status != "published":
        raise EventError("not_found")
    if event.end_at <= datetime.now(UTC):
        raise EventError("ended")
    existing = session.scalar(
        select(EventRegistration).where(
            EventRegistration.user_id == user_id, EventRegistration.event_id == event_id
        )
    )
    if existing is not None and existing.status in ACTIVE:
        return existing
    if event.capacity is not None:
        taken = seats_taken(session, [event_id]).get(event_id, 0)
        if taken >= event.capacity:
            raise EventError("full")
    if existing is not None:
        existing.status = "registered"
        existing.registered_at = datetime.now(UTC)
        session.flush()
        return existing
    row = EventRegistration(user_id=user_id, event_id=event_id, created_by=created_by)
    session.add(row)
    session.flush()
    return row


def cancel(session: Session, *, user_id: uuid.UUID, event_id: str) -> EventRegistration:
    row = session.scalar(
        select(EventRegistration).where(
            EventRegistration.user_id == user_id, EventRegistration.event_id == event_id
        )
    )
    if row is None or row.status != "registered":
        raise EventError("not_registered")
    row.status = "cancelled"
    session.flush()
    return row


@dataclass
class CheckinResult:
    registration: EventRegistration
    points: int
    walk_in: bool


def check_in(
    session: Session,
    *,
    event: Event,
    user_id: uuid.UUID,
    staff_account_id: uuid.UUID,
    now: datetime | None = None,
) -> CheckinResult:
    """據點人員掃民眾 QR 報到。沒報名的現場參加者一併登記（walk-in）。點數同一場只發一次。"""
    now = now or datetime.now(UTC)
    if event.status != "published":
        raise EventError("not_found")
    if not (event.start_at - CHECKIN_BEFORE <= now <= event.end_at + CHECKIN_AFTER):
        raise EventError("checkin_window")
    row = session.scalar(
        select(EventRegistration)
        .where(EventRegistration.user_id == user_id, EventRegistration.event_id == event.id)
        .with_for_update()
    )
    walk_in = row is None
    if row is None:
        row = EventRegistration(user_id=user_id, event_id=event.id, created_by="user")
        session.add(row)
        session.flush()
    if row.status == "attended":
        return CheckinResult(row, 0, walk_in=False)
    row.status = "attended"
    row.checked_in_at = now
    row.checked_in_by = staff_account_id
    granted = 0
    if event.points_reward > 0:
        points.append(session, user_id, event.points_reward, "event.attend", "event", event.id)
        granted = event.points_reward
    row.points_granted = granted
    session.flush()
    return CheckinResult(row, granted, walk_in)


def cancel_event(session: Session, event: Event, *, actor: str) -> int:
    """取消活動並通知已報名者（推播只有活動名稱與時間）。回傳通知人數。"""
    event.status = "cancelled"
    event.reviewed_by = actor
    event.reviewed_at = datetime.now(UTC)
    rows = session.scalars(
        select(EventRegistration).where(
            EventRegistration.event_id == event.id, EventRegistration.status == "registered"
        )
    ).all()
    for row in rows:
        row.status = "cancelled"
        notify(
            session,
            user_id=row.user_id,
            channel="push",
            kind="event_cancelled",
            title=t("events.push.cancelled_title"),
            body=t(
                "events.push.cancelled_body",
                title=event.title,
                when=when_text(event.start_at, event.end_at),
            ),
            payload={"event_id": event.id},
        )
    session.flush()
    return len(rows)


def counts(session: Session, event_ids: list[str]) -> dict[str, dict[str, int]]:
    """各場的報名／報到人數（idn_app 連線；給機關端時由呼叫端做 < k 遮蔽）。"""
    out: dict[str, dict[str, int]] = {e: {"registered": 0, "attended": 0} for e in event_ids}
    if not event_ids:
        return out
    rows = session.execute(
        select(EventRegistration.event_id, EventRegistration.status, func.count())
        .where(EventRegistration.event_id.in_(event_ids), EventRegistration.status.in_(ACTIVE))
        .group_by(EventRegistration.event_id, EventRegistration.status)
    )
    for event_id, status, n in rows:
        out[event_id][status] = int(n)
    return out


def event_out(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "title": event.title,
        "category": event.category,
        "category_label": t(f"events.category.{event.category}"),
        "description": event.description,
        "organizer": event.organizer,
        "site_id": event.site_id,
        "village_code": event.village_code,
        "venue": event.venue,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "when": when_text(event.start_at, event.end_at),
        "capacity": event.capacity,
        "points_reward": event.points_reward,
        "tags": list(event.tags),
        "source": event.source,
        "source_url": event.source_url,
        "status": event.status,
        "extracted_by": event.extracted_by,
    }
