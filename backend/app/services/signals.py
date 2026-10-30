"""推薦用的健康訊號（SPEC §17.3 對照表）。每個訊號只在對應項目已授權時才會出現。

訊號只描述資料（「最近幾次血壓量測偏高」），不做疾病判斷；文字一律經 C-7 過濾後才給民眾。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_pub import EnvHourly
from app.fhir import codes
from app.i18n import t
from app.services import health_data as hd
from app.services import risk
from app.sysconfig import cached_value
from app.tools._common import md, profile

GLUCOSE_HIGH = 126  # mg/dL；只用來說「偏高」，不是診斷

# 訊號 → 偏好的活動標籤、商家標籤
EVENT_TAG_OF = {"bp": "bp", "glucose": "glucose", "steps": "steps", "gentle": "gentle"}
MERCHANT_TAG_OF = {"bp": "low_salt", "glucose": "low_sugar", "gentle": "elder_friendly"}


@dataclass(frozen=True)
class Signal:
    key: str  # bp | glucose | steps | gentle | indoor
    reason: str
    basis: str | None = None


def _bp(session: Session, user_id: uuid.UUID, now: datetime) -> Signal | None:
    readings = hd.bp_readings(session, user_id, since=now - timedelta(days=30), limit=3)
    if not readings or not readings[0].high or sum(r.high for r in readings) < 2:
        return None
    dates = "、".join(md(r.at) for r in reversed(readings))
    return Signal("bp", t("rec.signal.bp"), t("rec.basis.bp", dates=dates))


def _glucose(session: Session, user_id: uuid.UUID, now: datetime) -> Signal | None:
    readings = hd.observations(session, user_id, codes.GLUCOSE, since=now - timedelta(days=60))
    if not readings or readings[-1].value < GLUCOSE_HIGH:
        return None
    last = readings[-1]
    return Signal("glucose", t("rec.signal.glucose"), t("rec.basis.glucose", date=md(last.at)))


def _steps(session: Session, user_id: uuid.UUID, day: date) -> Signal | None:
    steps = hd.daily_steps(session, user_id, day - timedelta(days=28), day)
    recent = [v for d, v in steps.items() if d >= day - timedelta(days=14)]
    earlier = [v for d, v in steps.items() if d < day - timedelta(days=14)]
    if len(recent) < 5:
        return None
    avg = sum(recent) / len(recent)
    goal = int(cached_value("step_goal_default", 6000))
    basis = t("rec.basis.steps", avg=f"{round(avg):,}")
    if earlier and sum(earlier) / len(earlier) > 0:
        drop = 1 - avg / (sum(earlier) / len(earlier))
        if drop >= 0.3:
            return Signal("steps", t("rec.signal.steps_drop"), basis)
    if avg < goal * 0.8:
        return Signal("steps", t("rec.signal.steps_low", goal=f"{goal:,}"), basis)
    return None


def _conditions(session: Session, user_id: uuid.UUID) -> list[Signal]:
    found = hd.condition_codes(session, user_id)
    out = []
    if hd.has_condition(found, codes.HYPERTENSION):
        out.append(Signal("bp", t("rec.signal.bp_record"), t("rec.basis.nhi")))
    if hd.has_condition(found, codes.DIABETES):
        out.append(Signal("glucose", t("rec.signal.glucose_record"), t("rec.basis.nhi")))
    return out


def _indoor(session: Session) -> Signal | None:
    row = session.scalars(
        select(EnvHourly)
        .where(EnvHourly.district == get_settings().district)
        .order_by(EnvHourly.hour.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    if row.aqi > 100 or float(row.heat_index) > 32 or row.rain:
        return Signal("indoor", t("rec.signal.indoor"))
    return None


def health_signals(
    session: Session, user_id: uuid.UUID, scope: dict[str, list[str]], day: date
) -> dict[str, Signal]:
    """同一個 key 只留第一個（量測資料優先於就醫紀錄）。"""
    now = datetime.combine(day, datetime.min.time(), hd.tz()) + timedelta(days=1)
    found: list[Signal | None] = []
    if "bp" in scope.get("site", []):
        found.append(_bp(session, user_id, now))
    if "glucose" in scope.get("site", []):
        found.append(_glucose(session, user_id, now))
    if "steps" in scope.get("wearable", []):
        found.append(_steps(session, user_id, day))
    if "conditions" in scope.get("nhi", []):
        found += _conditions(session, user_id)

    me = profile(session, user_id, day)
    level = risk.evaluate(session, user_id, scope, day).level
    if me.age >= 75 or level in ("L2", "L3"):
        found.append(Signal("gentle", t("rec.signal.gentle")))
    found.append(_indoor(session))

    out: dict[str, Signal] = {}
    for signal in found:
        if signal is not None and signal.key not in out:
            out[signal.key] = signal
    return out
