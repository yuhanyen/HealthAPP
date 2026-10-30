"""可識別區 schema `idn`：SPEC §4.1。

規則：這裡的資料只能經 `app/deidentify/` 單向流向 `anon`，任何查詢不得同時引用兩區。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from app.db.base import Base
from app.db.crypto import EncryptedText

CONSENT_SOURCES = ("wearable", "site", "nhi", "env", "merchant")
FHIR_RESOURCE_TYPES = (
    "Observation",
    "Condition",
    "MedicationStatement",
    "Encounter",
    "Immunization",
    "AllergyIntolerance",
)
# event_register：報名會把姓名與電話交給主辦單位，等同一次分享（SPEC §17.3）
HIGH_RISK_ACTIONS = ("booking", "share", "redeem", "consent_revoke", "event_register")
REGISTRATION_STATUSES = ("registered", "cancelled", "attended")
ACTION_STATUSES = ("pending", "confirmed", "executed", "expired", "cancelled", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("idn.users.id"), index=True)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("sex IN ('F', 'M', 'X')", name="sex"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    national_id_hash: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(EncryptedText)
    birth_date: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(EncryptedText)
    # 規格未列：去識別化聚合鍵需要「里」、據點解碼需要地址（docs/DECISIONS.md）
    address: Mapped[str | None] = mapped_column(EncryptedText)
    village_code: Mapped[str] = mapped_column(ForeignKey("pub.villages.code"))
    elder_mode: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = _now()


class Consent(Base):
    """授權。更新＝舊版本標 superseded_at、新增 version+1；撤回＝寫 revoked_at。"""

    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(_in("source", CONSENT_SOURCES), name="source"),
        Index(
            "uq_consents_active_source",
            "user_id",
            "source",
            unique=True,
            postgresql_where=text("revoked_at IS NULL AND superseded_at IS NULL"),
        ),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    source: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict[str, Any]]
    granted_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class FamilyLink(Base):
    __tablename__ = "family_links"
    __table_args__ = (
        CheckConstraint("level IN ('summary')", name="level"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"), index=True)
    viewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"), index=True)
    level: Mapped[str] = mapped_column(Text, server_default=text("'summary'"))
    granted_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FhirResource(Base):
    __tablename__ = "fhir_resources"
    __table_args__ = (
        CheckConstraint(_in("resource_type", FHIR_RESOURCE_TYPES), name="resource_type"),
        # 明確命名：慣例名稱會超過 PostgreSQL 的 63 字元上限而被截斷
        UniqueConstraint(
            "user_id",
            "resource_type",
            "source",
            "code",
            "effective_at",
            name=conv("uq_fhir_resources_natural_key"),
        ),
        Index(None, "user_id", "source"),
        Index(None, "user_id", "code", "effective_at"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    resource_type: Mapped[str] = mapped_column(Text)
    resource: Mapped[dict[str, Any]]
    source: Mapped[str] = mapped_column(Text)
    # 主要編碼（LOINC／ICD-10／疫苗碼…），由 fhir/writer 填入；查詢與唯一鍵都靠它
    code: Mapped[str] = mapped_column(Text)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class PointsLedger(Base):
    """點數帳本（append-only，DB trigger 禁止 UPDATE/DELETE）。"""

    __tablename__ = "points_ledger"
    __table_args__ = (
        CheckConstraint("balance_after >= 0", name="balance_non_negative"),
        {"schema": "idn"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    ref_type: Mapped[str | None] = mapped_column(Text)
    ref_id: Mapped[str | None] = mapped_column(Text)
    balance_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()


class Redemption(Base):
    __tablename__ = "redemptions"
    __table_args__ = (
        CheckConstraint("status IN ('issued', 'verified', 'expired', 'cancelled')", name="status"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    merchant_id: Mapped[str] = mapped_column(ForeignKey("pub.merchants.id"))
    offer_id: Mapped[str] = mapped_column(ForeignKey("pub.offers.id"))
    points: Mapped[int] = mapped_column(Integer)
    qr_token: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'issued'"))
    redeemed_at: Mapped[datetime] = _now()
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("status IN ('booked', 'cancelled', 'completed', 'no_show')", name="status"),
        CheckConstraint("created_by IN ('user', 'a1')", name="created_by"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    site_id: Mapped[str] = mapped_column(ForeignKey("pub.sites.id"))
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'booked'"))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class CareTask(Base):
    __tablename__ = "care_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'assigned', 'visited', 'closed')", name="status"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    site_id: Mapped[str] = mapped_column(ForeignKey("pub.sites.id"), index=True)
    task_type: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    created_at: Mapped[datetime] = _now()


VISIT_REASONS = ("distance", "equipment", "willingness", "health", "other")


class VisitReport(Base):
    """訪視回報（SKILL A5「收回訪視結果」）。去姓名後由 deidentify 聚合進 anon.visit_outcomes。"""

    __tablename__ = "visit_reports"
    __table_args__ = (
        CheckConstraint(_in("reason_class", VISIT_REASONS), name="reason_class"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    care_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.care_tasks.id"), index=True)
    reported_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("sys.accounts.id"))
    task_ref: Mapped[str] = mapped_column(Text)
    reason_class: Mapped[str] = mapped_column(Text)
    needs: Mapped[list[str]] = mapped_column(JSONB)
    referral: Mapped[bool] = mapped_column(Boolean, server_default=false())
    next_followup: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(EncryptedText)
    created_at: Mapped[datetime] = _now()


class DecodeAudit(Base):
    """代號解碼留痕（append-only）。"""

    __tablename__ = "decode_audit"
    __table_args__ = {"schema": "idn"}

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    care_task_code: Mapped[str] = mapped_column(Text, index=True)
    decoded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("sys.accounts.id"))
    site_id: Mapped[str] = mapped_column(ForeignKey("pub.sites.id"))
    task_ref: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = _now()


class TwinSnapshot(Base):
    __tablename__ = "twin_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "as_of"), {"schema": "idn"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    as_of: Mapped[date] = mapped_column(Date)
    state: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _now()


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("user_id", "report_date"), {"schema": "idn"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    report_date: Mapped[date] = mapped_column(Date)
    content: Mapped[dict[str, Any]]
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletionJob(Base):
    """撤回授權後的資料刪除任務（SPEC §5.1：24h 內執行，稽核保留）。規格未列，見 DECISIONS。"""

    __tablename__ = "deletion_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
        Index(None, "status", "due_at"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    consent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.consents.id"))
    source: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = _now()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    deleted_counts: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)


class PendingAction(Base):
    """高風險動作確認狀態機（C-5）。pending → confirmed → executed；逾 24h → expired。"""

    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint(_in("action_type", HIGH_RISK_ACTIONS), name="action_type"),
        CheckConstraint(_in("status", ACTION_STATUSES), name="status"),
        CheckConstraint("created_by IN ('user', 'a1', 'family')", name="created_by"),
        Index(None, "user_id", "status"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    action_type: Mapped[str] = mapped_column(Text)
    tool: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None]


class Notification(Base):
    """通知收件匣（07:00 每日報告推播、即時提醒）。channel：push／line／inbox。"""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('push', 'line', 'inbox')", name="channel"),
        CheckConstraint("status IN ('queued', 'sent', 'failed')", name="status"),
        Index(None, "user_id", "created_at"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    channel: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    created_at: Mapped[datetime] = _now()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PointsReview(Base):
    """點數防作弊待審（SPEC §7.1 points_engine）：被標記的那天不發點，等人工審核。"""

    __tablename__ = "points_reviews"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'cleared', 'rejected')", name="status"),
        UniqueConstraint("user_id", "day"),
        {"schema": "idn"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    day: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """民眾與 A1 的對話（只存文字回合；內容加密）。meta 只放待決定方案（時段、優惠編號）。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        Index(None, "user_id", "id"),
        {"schema": "idn"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(EncryptedText)
    meta: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = _now()


class UserPrefs(Base):
    """推薦偏好（SPEC §17.1）：活動興趣、商家類別、飲食、評分門檻、距離、活動推播。"""

    __tablename__ = "user_prefs"
    __table_args__ = {"schema": "idn"}

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"), primary_key=True)
    event_categories: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    merchant_categories: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    dietary: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    min_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1))
    max_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    event_push: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = _now()


class EventRegistration(Base):
    """活動報名與報到（SPEC §17.1）。報名本身是個資，只在 idn；參與率經 deidentify 聚合。"""

    __tablename__ = "event_registrations"
    __table_args__ = (
        CheckConstraint(_in("status", REGISTRATION_STATUSES), name="status"),
        CheckConstraint("created_by IN ('user', 'a1')", name="created_by"),
        UniqueConstraint("user_id", "event_id"),
        Index(None, "event_id", "status"),
        {"schema": "idn"},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("idn.users.id"))
    event_id: Mapped[str] = mapped_column(ForeignKey("pub.events.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'registered'"))
    created_by: Mapped[str] = mapped_column(Text)
    registered_at: Mapped[datetime] = _now()
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sys.accounts.id"))
    points_granted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
