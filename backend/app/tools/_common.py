"""工具層共用小工具。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.agents.output_filter import filter_output
from app.config import get_settings
from app.db.models_idn import User


class AgencyToolArgs(BaseModel):
    """機關側工具參數基底（沒有個人，也就沒有 consent_scope）。"""

    model_config = ConfigDict(extra="forbid")


class SiteToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def today() -> date:
    return datetime.now(tz()).date()


def md(moment: datetime | date) -> str:
    if isinstance(moment, datetime):
        moment = moment.astimezone(tz()).date()
    return f"{moment.month}/{moment.day}"


def age_band(age: int) -> str:
    if age < 20:
        return "<20"
    if age >= 80:
        return "80+"
    low = age // 10 * 10
    return f"{low}-{low + 9}"


@dataclass(frozen=True)
class Profile:
    age: int
    age_band: str
    sex: str
    village_code: str
    elder_mode: bool


def profile(session: Session, user_id: uuid.UUID, on: date | None = None) -> Profile:
    user = session.get_one(User, user_id)
    on = on or today()
    age = (
        on.year
        - user.birth_date.year
        - ((on.month, on.day) < (user.birth_date.month, user.birth_date.day))
    )
    return Profile(age, age_band(age), user.sex, user.village_code, user.elder_mode)


def site_name(session: Session, site_id: str | None) -> str:
    from app.db.models_pub import Site

    if not site_id:
        return "據點"
    site = session.get(Site, site_id)
    return site.name if site else site_id


def safe(text: str) -> str:
    """A1 對民眾的文字一律經 C-7 輸出過濾。"""
    return filter_output(text).text


def _safe_any(data: Any) -> Any:
    if isinstance(data, str):
        return safe(data)
    if isinstance(data, list):
        return [_safe_any(v) for v in data]
    if isinstance(data, dict):
        return {k: _safe_any(v) for k, v in data.items()}
    return data


def safe_all(data: dict[str, Any]) -> dict[str, Any]:
    """整包工具輸出的所有字串都過 C-7 過濾（結構不變）。"""
    out: dict[str, Any] = _safe_any(data)
    return out
