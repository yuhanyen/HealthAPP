"""讀取「本人」的健康資料（給工具層用）。傳入的 session 可以是 A1 的 RLS 連線，只看得到本人。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_idn import FhirResource
from app.fhir import codes


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


@dataclass(frozen=True)
class BpReading:
    at: datetime
    systolic: int
    diastolic: int
    site: str | None

    @property
    def high(self) -> bool:
        return self.systolic >= 140 or self.diastolic >= 90


def _component(resource: dict[str, Any], code: str) -> float | None:
    for comp in resource.get("component", []):
        if comp["code"]["coding"][0]["code"] == code:
            return float(comp["valueQuantity"]["value"])
    return None


def bp_readings(
    session: Session, user_id: uuid.UUID, *, since: datetime | None = None, limit: int = 20
) -> list[BpReading]:
    """最近的據點血壓（新到舊）。"""
    stmt = (
        select(FhirResource.effective_at, FhirResource.resource)
        .where(
            FhirResource.user_id == user_id,
            FhirResource.source == "site",
            FhirResource.code == codes.BP_PANEL,
        )
        .order_by(FhirResource.effective_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(FhirResource.effective_at >= since)
    out = []
    for at, res in session.execute(stmt):
        s, d = _component(res, codes.BP_SYSTOLIC), _component(res, codes.BP_DIASTOLIC)
        if s is None or d is None:
            continue
        performer = (res.get("performer") or [{}])[0].get("identifier", {}).get("value")
        out.append(BpReading(at, int(s), int(d), performer))
    return out


def daily_steps(session: Session, user_id: uuid.UUID, start: date, end: date) -> dict[date, int]:
    """[start, end) 每日步數：有每日總數用總數，否則加總每小時。"""
    t = tz()
    rows = session.execute(
        select(FhirResource.code, FhirResource.effective_at, FhirResource.resource).where(
            FhirResource.user_id == user_id,
            FhirResource.source == "wearable",
            FhirResource.code.in_([codes.STEPS_DAILY, codes.STEPS_HOURLY]),
            FhirResource.effective_at >= datetime.combine(start, datetime.min.time(), t),
            FhirResource.effective_at < datetime.combine(end, datetime.min.time(), t),
        )
    )
    summary: dict[date, int] = {}
    hourly: dict[date, int] = {}
    for code, at, res in rows:
        day = at.astimezone(t).date()
        value = int(res["valueQuantity"]["value"])
        if code == codes.STEPS_DAILY:
            summary[day] = value
        else:
            hourly[day] = hourly.get(day, 0) + value
    return {**hourly, **summary}


def hourly_steps(session: Session, user_id: uuid.UUID, day: date) -> dict[int, int]:
    t = tz()
    start = datetime.combine(day, datetime.min.time(), t)
    rows = session.execute(
        select(FhirResource.effective_at, FhirResource.resource).where(
            FhirResource.user_id == user_id,
            FhirResource.code == codes.STEPS_HOURLY,
            FhirResource.effective_at >= start,
            FhirResource.effective_at < start + timedelta(days=1),
        )
    )
    return {at.astimezone(t).hour: int(res["valueQuantity"]["value"]) for at, res in rows}


def condition_codes(session: Session, user_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(FhirResource.code).where(
                FhirResource.user_id == user_id, FhirResource.resource_type == "Condition"
            )
        )
    )


def medications(session: Session, user_id: uuid.UUID) -> list[str]:
    """目前用藥的 ATC 碼（不回藥名，雙生只需要碼）。"""
    return sorted(
        set(
            session.scalars(
                select(FhirResource.code).where(
                    FhirResource.user_id == user_id,
                    FhirResource.resource_type == "MedicationStatement",
                )
            )
        )
    )


def has_condition(codes_found: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(code[:3] in prefixes for code in codes_found)


def last_encounter(session: Session, user_id: uuid.UUID) -> datetime | None:
    found: datetime | None = session.scalar(
        select(FhirResource.effective_at)
        .where(FhirResource.user_id == user_id, FhirResource.resource_type == "Encounter")
        .order_by(FhirResource.effective_at.desc())
        .limit(1)
    )
    return found


@dataclass(frozen=True)
class Reading:
    at: datetime
    value: float
    site: str | None


def observations(
    session: Session,
    user_id: uuid.UUID,
    code: str,
    *,
    since: datetime,
    until: datetime | None = None,
    limit: int = 500,
) -> list[Reading]:
    """單一數值的量測（體重、血糖…），舊到新。"""
    stmt = (
        select(FhirResource.effective_at, FhirResource.resource)
        .where(
            FhirResource.user_id == user_id,
            FhirResource.code == code,
            FhirResource.effective_at >= since,
        )
        .order_by(FhirResource.effective_at)
        .limit(limit)
    )
    if until is not None:
        stmt = stmt.where(FhirResource.effective_at < until)
    out = []
    for at, res in session.execute(stmt):
        value = (res.get("valueQuantity") or {}).get("value")
        if value is None:
            continue
        performer = (res.get("performer") or [{}])[0].get("identifier", {}).get("value")
        out.append(Reading(at, float(value), performer))
    return out


def daily_heart_rate(
    session: Session, user_id: uuid.UUID, start: date, end: date
) -> dict[date, int]:
    """[start, end) 每日平均心率（每小時量測的平均）。"""
    t = tz()
    sums: dict[date, list[float]] = {}
    for reading in observations(
        session,
        user_id,
        codes.HEART_RATE,
        since=datetime.combine(start, datetime.min.time(), t),
        until=datetime.combine(end, datetime.min.time(), t),
        limit=24 * 400,
    ):
        sums.setdefault(reading.at.astimezone(t).date(), []).append(reading.value)
    return {d: round(sum(v) / len(v)) for d, v in sums.items()}


def latest_observation(
    session: Session, user_id: uuid.UUID, code: str
) -> tuple[float, datetime] | None:
    row = session.execute(
        select(FhirResource.resource, FhirResource.effective_at)
        .where(FhirResource.user_id == user_id, FhirResource.code == code)
        .order_by(FhirResource.effective_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return float(row[0]["valueQuantity"]["value"]), row[1]
