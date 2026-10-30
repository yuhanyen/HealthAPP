"""系統 schema `sys`：稽核日誌（append-only）、設定、登入帳號。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ACTOR_TYPES = ("user", "staff", "agent", "admin", "system")
ACCOUNT_ROLES = ("citizen", "site_staff", "volunteer", "merchant", "agency", "admin")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AuditLog(Base):
    """SPEC §4.3 sys.audit_log；UPDATE/DELETE/TRUNCATE 由 DB trigger 禁止（C-6）。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(_in("actor_type", ACTOR_TYPES), name="actor_type"),
        Index(None, "at"),
        Index(None, "actor_id", "at"),
        {"schema": "sys"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    tool: Mapped[str | None] = mapped_column(Text)
    consent_scope: Mapped[dict[str, Any] | None]
    input_digest: Mapped[str | None] = mapped_column(Text)
    output_digest: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)


class Config(Base):
    """k 值、黑名單、風險閾值等可調參數（SPEC §13-3：變更寫稽核）。"""

    __tablename__ = "config"
    __table_args__ = {"schema": "sys"}

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str] = mapped_column(Text)


class Account(Base):
    """登入帳號（自建簡版身分驗證，介面留 OIDC）。民眾帳號連到 idn.users。"""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(_in("role", ACCOUNT_ROLES), name="role"),
        {"schema": "sys"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("idn.users.id"))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("pub.sites.id"))
    merchant_id: Mapped[str | None] = mapped_column(ForeignKey("pub.merchants.id"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisQuery(Base):
    """A3 的分析查詢紀錄（查詢編號 Q-xxxx），A2 的結論以編號引用、可重現。只針對 anon。"""

    __tablename__ = "analysis_queries"
    __table_args__ = {"schema": "sys"}

    query_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    masked_rows: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
