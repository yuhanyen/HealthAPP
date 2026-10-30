"""去識別化區 schema `anon`：SPEC §4.2。

規則（SPEC §4.4）：任何表不得含 user_id、national_id_hash、姓名、地址或其他可反查欄位；
聚合列 n < k 不寫入。由 tests/constraints/test_c1_* 掃描欄位名驗證。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Identity, Index, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CohortWeekly(Base):
    __tablename__ = "cohort_weekly"
    __table_args__ = (Index(None, "metric", "week"), {"schema": "anon"})

    week: Mapped[date] = mapped_column(Date, primary_key=True)
    village_code: Mapped[str] = mapped_column(Text, primary_key=True)
    age_band: Mapped[str] = mapped_column(Text, primary_key=True)
    sex: Mapped[str] = mapped_column(Text, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric)
    n: Mapped[int] = mapped_column(Integer)


class SiteOpsWeekly(Base):
    __tablename__ = "site_ops_weekly"
    __table_args__ = {"schema": "anon"}

    week: Mapped[date] = mapped_column(Date, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, primary_key=True)
    visits: Mapped[int] = mapped_column(Integer)
    bookings: Mapped[int] = mapped_column(Integer)
    no_show: Mapped[int] = mapped_column(Integer)


class MerchantWeekly(Base):
    __tablename__ = "merchant_weekly"
    __table_args__ = {"schema": "anon"}

    week: Mapped[date] = mapped_column(Date, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    redemptions: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)


class VisitOutcome(Base):
    __tablename__ = "visit_outcomes"
    __table_args__ = {"schema": "anon"}

    week: Mapped[date] = mapped_column(Date, primary_key=True)
    village_code: Mapped[str] = mapped_column(Text, primary_key=True)
    task_type: Mapped[str] = mapped_column(Text, primary_key=True)
    reason_class: Mapped[str] = mapped_column(Text, primary_key=True)
    n: Mapped[int] = mapped_column(Integer)


class SentimentItem(Base):
    __tablename__ = "sentiment_items"
    __table_args__ = (Index(None, "village_code", "topic", "collected_at"), {"schema": "anon"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_class: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    village_code: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str] = mapped_column(Text)
    intensity: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    summary: Mapped[str] = mapped_column(Text)


class SentimentAlert(Base):
    __tablename__ = "sentiment_alerts"
    __table_args__ = {"schema": "anon"}

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    village_code: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer)
    baseline: Mapped[Decimal] = mapped_column(Numeric)
    ratio: Mapped[Decimal] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text)
    analysis: Mapped[dict[str, Any] | None]  # A2 委派 A3／A4 後的對照分析


class CareListDaily(Base):
    """A2 每日關懷清單數量（不含代號）；只寫 n ≥ k 的列，'ALL' 為全區。"""

    __tablename__ = "care_list_daily"
    __table_args__ = {"schema": "anon"}

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_type: Mapped[str] = mapped_column(Text, primary_key=True)
    n: Mapped[int] = mapped_column(Integer)


class AppMetricsDaily(Base):
    __tablename__ = "app_metrics_daily"
    __table_args__ = {"schema": "anon"}

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    dau: Mapped[int] = mapped_column(Integer)
    mau: Mapped[int] = mapped_column(Integer)
    retention_7: Mapped[Decimal] = mapped_column(Numeric)
    retention_30: Mapped[Decimal] = mapped_column(Numeric)
    push_open_rate: Mapped[Decimal] = mapped_column(Numeric)
    elder_mode_rate: Mapped[Decimal] = mapped_column(Numeric)
