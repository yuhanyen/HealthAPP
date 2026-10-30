"""共用（非個資）schema `pub`：SPEC §4.3。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# SPEC §17.1：活動類別與來源、狀態
EVENT_CATEGORIES = ("measure", "walk", "exercise", "education", "nutrition", "vaccine", "social")
EVENT_SOURCES = ("site", "agency", "crawl")
EVENT_STATUSES = ("pending_review", "published", "rejected", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Village(Base):
    __tablename__ = "villages"
    __table_args__ = {"schema": "pub"}

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    district: Mapped[str] = mapped_column(Text)
    population: Mapped[int] = mapped_column(Integer)
    # 一期不裝 PostGIS：以 GeoJSON（JSONB）存放，見 docs/DECISIONS.md
    geom: Mapped[dict[str, Any] | None]


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = {"schema": "pub"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    village_code: Mapped[str] = mapped_column(ForeignKey("pub.villages.code"))
    lat: Mapped[float] = mapped_column(Double)
    lng: Mapped[float] = mapped_column(Double)
    hours: Mapped[dict[str, Any]]
    equipment: Mapped[list[Any]]
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = {"schema": "pub"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    village_code: Mapped[str] = mapped_column(ForeignKey("pub.villages.code"))
    lat: Mapped[float] = mapped_column(Double)
    lng: Mapped[float] = mapped_column(Double)
    hours: Mapped[dict[str, Any]]
    api_key_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # SPEC §17.1：公開評分（Google Places 或 Mock）與健康標籤（少鹽、少糖、素食…）
    rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))
    rating_count: Mapped[int | None] = mapped_column(Integer)
    rating_source: Mapped[str | None] = mapped_column(Text)
    rating_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = {"schema": "pub"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("pub.merchants.id"))
    title: Mapped[str] = mapped_column(Text)
    points_cost: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)


class EnvHourly(Base):
    __tablename__ = "env_hourly"
    __table_args__ = {"schema": "pub"}

    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    district: Mapped[str] = mapped_column(Text, primary_key=True)
    temp: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    heat_index: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    aqi: Mapped[int] = mapped_column(Integer)
    rain: Mapped[bool] = mapped_column(Boolean)


class SourceWhitelist(Base):
    __tablename__ = "source_whitelist"
    __table_args__ = {"schema": "pub"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_class: Mapped[str] = mapped_column(Text)
    url_pattern: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str] = mapped_column(Text)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Event(Base):
    """近日社區活動（SPEC §17.1）。公開資訊；誰報名、誰報到在 idn.event_registrations。"""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(_in("category", EVENT_CATEGORIES), name="category"),
        CheckConstraint(_in("source", EVENT_SOURCES), name="source"),
        CheckConstraint(_in("status", EVENT_STATUSES), name="status"),
        CheckConstraint("end_at > start_at", name="window"),
        CheckConstraint("points_reward >= 0", name="points_reward"),
        Index(None, "status", "start_at"),
        {"schema": "pub"},
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    organizer: Mapped[str] = mapped_column(Text)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("pub.sites.id"))
    village_code: Mapped[str | None] = mapped_column(ForeignKey("pub.villages.code"))
    venue: Mapped[str] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Double)
    lng: Mapped[float | None] = mapped_column(Double)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int | None] = mapped_column(Integer)
    points_reward: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    tags: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    source: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    extracted_by: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending_review'"))
    created_by: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
