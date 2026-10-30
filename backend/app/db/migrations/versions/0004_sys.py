"""sys：稽核日誌、可調設定、登入帳號（SPEC §4.3）

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column("consent_scope", postgresql.JSONB(), nullable=True),
        sa.Column("input_digest", sa.Text(), nullable=True),
        sa.Column("output_digest", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('user', 'staff', 'agent', 'admin', 'system')",
            name=op.f("ck_audit_log_actor_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        schema="sys",
    )
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], schema="sys")
    op.create_index(op.f("ix_audit_log_actor_id_at"), "audit_log", ["actor_id", "at"], schema="sys")

    op.create_table(
        "config",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_config")),
        schema="sys",
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("site_id", sa.Text(), nullable=True),
        sa.Column("merchant_id", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('citizen', 'site_staff', 'volunteer', 'merchant', 'agency', 'admin')",
            name=op.f("ck_accounts_role"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["pub.merchants.id"], name=op.f("fk_accounts_merchant_id_merchants")
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["pub.sites.id"], name=op.f("fk_accounts_site_id_sites")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_accounts_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("username", name=op.f("uq_accounts_username")),
        schema="sys",
    )

    op.create_foreign_key(
        op.f("fk_decode_audit_decoded_by_accounts"),
        "decode_audit",
        "accounts",
        ["decoded_by"],
        ["id"],
        source_schema="idn",
        referent_schema="sys",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_decode_audit_decoded_by_accounts"),
        "decode_audit",
        schema="idn",
        type_="foreignkey",
    )
    for table in ("accounts", "config", "audit_log"):
        op.drop_table(table, schema="sys")
