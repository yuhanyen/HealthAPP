"""據點預約：依據點開放時段切 30 分鐘、每格 4 人。

時段占用量是跨使用者的計數（不含任何個人資料），以 idn_app 另開連線計算；
A1 的連線受 RLS 限制只看得到本人預約，無法自行算出占用量。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.engines import DbRole, session_scope
from app.db.models_idn import Booking
from app.db.models_pub import Site
from app.i18n import t

SLOT_MINUTES = 30
CAPACITY = 4
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class BookingError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(t(f"booking.error.{key}"))


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def candidate_slots(site: Site, start: date, days: int) -> list[datetime]:
    out: list[datetime] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        window = site.hours.get(WEEKDAYS[day.weekday()])
        if not window:
            continue
        for part in str(window).split(","):
            begin, end = (time.fromisoformat(x.strip()) for x in part.split("-"))
            slot = datetime.combine(day, begin, _tz())
            stop = datetime.combine(day, end, _tz())
            while slot + timedelta(minutes=SLOT_MINUTES) <= stop:
                out.append(slot)
                slot += timedelta(minutes=SLOT_MINUTES)
    return out


def occupancy(site_id: str, start: datetime, end: datetime) -> dict[datetime, int]:
    with session_scope(DbRole.IDN_APP) as s:
        rows = s.execute(
            select(Booking.slot_at, func.count())
            .where(
                Booking.site_id == site_id,
                Booking.status == "booked",
                Booking.slot_at >= start,
                Booking.slot_at < end,
            )
            .group_by(Booking.slot_at)
        )
        return {slot: int(n) for slot, n in rows}


def open_slots(
    session: Session, site_id: str, *, now: datetime, days: int = 3, limit: int = 6
) -> list[datetime]:
    site = session.get(Site, site_id)
    if site is None or not site.active:
        raise BookingError("site_not_found")
    first = (now + timedelta(hours=2)).astimezone(_tz())
    slots = [s for s in candidate_slots(site, first.date(), days + 1) if s >= first]
    if not slots:
        return []
    taken = occupancy(site_id, slots[0], slots[-1] + timedelta(minutes=SLOT_MINUTES))
    return [s for s in slots if taken.get(s, 0) < CAPACITY][:limit]


def book(
    session: Session,
    *,
    user_id: uuid.UUID,
    site_id: str,
    slot_at: datetime,
    purpose: str,
    created_by: str,
) -> Booking:
    site = session.get(Site, site_id)
    if site is None:
        raise BookingError("site_not_found")
    if slot_at not in candidate_slots(site, slot_at.astimezone(_tz()).date(), 1):
        raise BookingError("not_a_slot")
    taken = session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.site_id == site_id, Booking.slot_at == slot_at, Booking.status == "booked")
    )
    if (taken or 0) >= CAPACITY:
        raise BookingError("slot_full")
    booking = Booking(
        user_id=user_id, site_id=site_id, slot_at=slot_at, purpose=purpose, created_by=created_by
    )
    session.add(booking)
    session.flush()
    return booking
