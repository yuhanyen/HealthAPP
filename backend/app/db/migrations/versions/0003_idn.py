"""idn（可識別區）：使用者、授權、FHIR、點數帳本、預約、關懷任務、雙生、報告（SPEC §4.1）

另含規格未列、但約束需要的兩張表：deletion_jobs（撤回後刪除）、pending_actions（C-5 確認狀態機）。
decode_audit → sys.accounts 的外鍵在 0004 建立 accounts 後補上。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "idn"


def _uuid_pk() -> sa.Column[Any]:
    return sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _identity_pk() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False)


def _ts(name: str, *, default_now: bool = False, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default_now else None,
        nullable=nullable,
    )


def _user_fk(table: str, column: str = "user_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["idn.users.id"], name=op.f(f"fk_{table}_{column}_users")
    )


def upgrade() -> None:
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("national_id_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),  # AES-GCM 密文
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("sex", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),  # AES-GCM 密文
        sa.Column("address", sa.Text(), nullable=True),  # AES-GCM 密文
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("elder_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("created_at", default_now=True),
        sa.CheckConstraint("sex IN ('F', 'M', 'X')", name=op.f("ck_users_sex")),
        sa.ForeignKeyConstraint(
            ["village_code"], ["pub.villages.code"], name=op.f("fk_users_village_code_villages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("national_id_hash", name=op.f("uq_users_national_id_hash")),
        schema=S,
    )

    op.create_table(
        "consents",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        _ts("granted_at", default_now=True),
        _ts("revoked_at", nullable=True),
        _ts("superseded_at", nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "source IN ('wearable', 'site', 'nhi', 'env', 'merchant')",
            name=op.f("ck_consents_source"),
        ),
        _user_fk("consents"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
        schema=S,
    )
    op.create_index(op.f("ix_consents_user_id"), "consents", ["user_id"], schema=S)
    op.create_index(
        "uq_consents_active_source",
        "consents",
        ["user_id", "source"],
        unique=True,
        schema=S,
        postgresql_where=sa.text("revoked_at IS NULL AND superseded_at IS NULL"),
    )

    op.create_table(
        "family_links",
        _uuid_pk(),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("viewer_user_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Text(), server_default=sa.text("'summary'"), nullable=False),
        _ts("granted_at", default_now=True),
        _ts("revoked_at", nullable=True),
        sa.CheckConstraint("level IN ('summary')", name=op.f("ck_family_links_level")),
        _user_fk("family_links", "owner_user_id"),
        _user_fk("family_links", "viewer_user_id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_family_links")),
        schema=S,
    )
    op.create_index(
        op.f("ix_family_links_owner_user_id"), "family_links", ["owner_user_id"], schema=S
    )
    op.create_index(
        op.f("ix_family_links_viewer_user_id"), "family_links", ["viewer_user_id"], schema=S
    )

    op.create_table(
        "fhir_resources",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _ts("effective_at"),
        _ts("created_at", default_now=True),
        sa.CheckConstraint(
            "resource_type IN ('Observation', 'Condition', 'MedicationStatement', "
            "'Encounter', 'Immunization', 'AllergyIntolerance')",
            name=op.f("ck_fhir_resources_resource_type"),
        ),
        _user_fk("fhir_resources"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_resources")),
        sa.UniqueConstraint(
            "user_id",
            "resource_type",
            "source",
            "effective_at",
            name=op.f("uq_fhir_resources_user_id_resource_type_source_effective_at"),
        ),
        schema=S,
    )
    op.create_index(
        op.f("ix_fhir_resources_user_id_source"), "fhir_resources", ["user_id", "source"], schema=S
    )

    op.create_table(
        "points_ledger",
        _identity_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ref_type", sa.Text(), nullable=True),
        sa.Column("ref_id", sa.Text(), nullable=True),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        _ts("created_at", default_now=True),
        sa.CheckConstraint(
            "balance_after >= 0", name=op.f("ck_points_ledger_balance_non_negative")
        ),
        _user_fk("points_ledger"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points_ledger")),
        schema=S,
    )
    op.create_index(op.f("ix_points_ledger_user_id"), "points_ledger", ["user_id"], schema=S)

    op.create_table(
        "redemptions",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("offer_id", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("qr_token", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'issued'"), nullable=False),
        _ts("redeemed_at", default_now=True),
        _ts("verified_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('issued', 'verified', 'expired', 'cancelled')",
            name=op.f("ck_redemptions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["pub.merchants.id"], name=op.f("fk_redemptions_merchant_id_merchants")
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["pub.offers.id"], name=op.f("fk_redemptions_offer_id_offers")
        ),
        _user_fk("redemptions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_redemptions")),
        sa.UniqueConstraint("qr_token", name=op.f("uq_redemptions_qr_token")),
        schema=S,
    )
    op.create_index(op.f("ix_redemptions_user_id"), "redemptions", ["user_id"], schema=S)

    op.create_table(
        "bookings",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        _ts("slot_at"),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'booked'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        _ts("created_at", default_now=True),
        sa.CheckConstraint("created_by IN ('user', 'a1')", name=op.f("ck_bookings_created_by")),
        sa.CheckConstraint(
            "status IN ('booked', 'cancelled', 'completed', 'no_show')",
            name=op.f("ck_bookings_status"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["pub.sites.id"], name=op.f("fk_bookings_site_id_sites")
        ),
        _user_fk("bookings"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        schema=S,
    )
    op.create_index(op.f("ix_bookings_user_id"), "bookings", ["user_id"], schema=S)

    op.create_table(
        "care_tasks",
        _uuid_pk(),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        _ts("created_at", default_now=True),
        sa.CheckConstraint(
            "status IN ('open', 'assigned', 'visited', 'closed')",
            name=op.f("ck_care_tasks_status"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["pub.sites.id"], name=op.f("fk_care_tasks_site_id_sites")
        ),
        _user_fk("care_tasks"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_care_tasks")),
        sa.UniqueConstraint("code", name=op.f("uq_care_tasks_code")),
        schema=S,
    )
    op.create_index(op.f("ix_care_tasks_site_id"), "care_tasks", ["site_id"], schema=S)
    op.create_index(op.f("ix_care_tasks_user_id"), "care_tasks", ["user_id"], schema=S)

    op.create_table(
        "decode_audit",
        _identity_pk(),
        sa.Column("care_task_code", sa.Text(), nullable=False),
        sa.Column("decoded_by", sa.UUID(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("task_ref", sa.Text(), nullable=False),
        _ts("at", default_now=True),
        sa.ForeignKeyConstraint(
            ["site_id"], ["pub.sites.id"], name=op.f("fk_decode_audit_site_id_sites")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decode_audit")),
        schema=S,
    )
    op.create_index(
        op.f("ix_decode_audit_care_task_code"), "decode_audit", ["care_task_code"], schema=S
    )

    op.create_table(
        "twin_snapshots",
        _identity_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        _ts("created_at", default_now=True),
        _user_fk("twin_snapshots"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_twin_snapshots")),
        sa.UniqueConstraint("user_id", "as_of", name=op.f("uq_twin_snapshots_user_id_as_of")),
        schema=S,
    )

    op.create_table(
        "daily_reports",
        _identity_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        _ts("delivered_at", nullable=True),
        _ts("opened_at", nullable=True),
        _user_fk("daily_reports"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_reports")),
        sa.UniqueConstraint(
            "user_id", "report_date", name=op.f("uq_daily_reports_user_id_report_date")
        ),
        schema=S,
    )

    op.create_table(
        "deletion_jobs",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("consent_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _ts("requested_at", default_now=True),
        _ts("due_at"),
        _ts("executed_at", nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("deleted_counts", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed')", name=op.f("ck_deletion_jobs_status")
        ),
        sa.ForeignKeyConstraint(
            ["consent_id"], ["idn.consents.id"], name=op.f("fk_deletion_jobs_consent_id_consents")
        ),
        _user_fk("deletion_jobs"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_jobs")),
        schema=S,
    )
    op.create_index(
        op.f("ix_deletion_jobs_status_due_at"), "deletion_jobs", ["status", "due_at"], schema=S
    )
    op.create_index(op.f("ix_deletion_jobs_user_id"), "deletion_jobs", ["user_id"], schema=S)

    op.create_table(
        "pending_actions",
        _uuid_pk(),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        _ts("created_at", default_now=True),
        _ts("expires_at"),
        _ts("confirmed_at", nullable=True),
        _ts("executed_at", nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "action_type IN ('booking', 'share', 'redeem', 'consent_revoke')",
            name=op.f("ck_pending_actions_action_type"),
        ),
        sa.CheckConstraint(
            "created_by IN ('user', 'a1', 'family')", name=op.f("ck_pending_actions_created_by")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'executed', 'expired', 'cancelled', 'failed')",
            name=op.f("ck_pending_actions_status"),
        ),
        _user_fk("pending_actions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_actions")),
        schema=S,
    )
    op.create_index(
        op.f("ix_pending_actions_user_id_status"),
        "pending_actions",
        ["user_id", "status"],
        schema=S,
    )


def downgrade() -> None:
    for table in (
        "pending_actions",
        "deletion_jobs",
        "daily_reports",
        "twin_snapshots",
        "decode_audit",
        "care_tasks",
        "bookings",
        "redemptions",
        "points_ledger",
        "fhir_resources",
        "family_links",
        "consents",
        "users",
    ):
        op.drop_table(table, schema=S)
