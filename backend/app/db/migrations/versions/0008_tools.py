"""M3：通知收件匣、點數防作弊待審、分析查詢紀錄；A1 對本人資料的寫入權

- idn.notifications：07:00 推播與即時提醒（App 內收件匣，Web Push／LINE 為通道）。
- idn.points_reviews：防作弊標記（單小時 >3000 步或單日 >40000 步）待人工審核，不發點。
- sys.analysis_queries：A3 每次 sandbox_sql 的查詢編號（Q-xxxx）與 SQL 原文，供 A2 引用與重現。
  SQL 只針對 anon，不含個資；由 audit_writer 寫入。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ME = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def _ts(name: str, *, now: bool = False, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if now else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        _ts("created_at", now=True),
        _ts("sent_at", nullable=True),
        _ts("read_at", nullable=True),
        sa.CheckConstraint(
            "channel IN ('push', 'line', 'inbox')", name=op.f("ck_notifications_channel")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed')", name=op.f("ck_notifications_status")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        schema="idn",
    )
    op.create_index(
        op.f("ix_notifications_user_id_created_at"),
        "notifications",
        ["user_id", "created_at"],
        schema="idn",
    )

    op.create_table(
        "points_reviews",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        _ts("created_at", now=True),
        _ts("resolved_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'cleared', 'rejected')", name=op.f("ck_points_reviews_status")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_points_reviews_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points_reviews")),
        sa.UniqueConstraint("user_id", "day", name=op.f("uq_points_reviews_user_id_day")),
        schema="idn",
    )

    op.create_table(
        "analysis_queries",
        sa.Column("query_id", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("masked_rows", sa.Integer(), nullable=False),
        _ts("created_at", now=True),
        sa.PrimaryKeyConstraint("query_id", name=op.f("pk_analysis_queries")),
        schema="sys",
    )
    op.execute("CREATE SEQUENCE IF NOT EXISTS sys.analysis_query_seq")

    # --- 權限 ---
    op.execute("GRANT SELECT, INSERT, UPDATE ON idn.notifications, idn.points_reviews TO idn_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA idn TO idn_app")
    op.execute("GRANT SELECT, INSERT ON idn.notifications, idn.points_reviews TO idn_user_scoped")
    op.execute("GRANT UPDATE (read_at) ON idn.notifications TO idn_user_scoped")
    op.execute("GRANT INSERT ON idn.points_ledger TO idn_user_scoped")
    op.execute("GRANT SELECT, INSERT ON sys.analysis_queries TO audit_writer, idn_app")
    op.execute("GRANT USAGE, UPDATE ON SEQUENCE sys.analysis_query_seq TO audit_writer, idn_app")
    for table in ("notifications", "points_reviews"):
        op.execute(f"ALTER TABLE idn.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY p_{table}_idn_app ON idn.{table} TO idn_app "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY p_{table}_user_scoped ON idn.{table} TO idn_user_scoped "
            f"USING (user_id = {_ME}) WITH CHECK (user_id = {_ME})"
        )


def downgrade() -> None:
    for table in ("notifications", "points_reviews"):
        op.execute(f"DROP POLICY IF EXISTS p_{table}_idn_app ON idn.{table}")
        op.execute(f"DROP POLICY IF EXISTS p_{table}_user_scoped ON idn.{table}")
    op.execute("REVOKE INSERT ON idn.points_ledger FROM idn_user_scoped")
    op.execute("DROP SEQUENCE IF EXISTS sys.analysis_query_seq")
    op.drop_table("analysis_queries", schema="sys")
    op.drop_table("points_reviews", schema="idn")
    op.drop_index(op.f("ix_notifications_user_id_created_at"), "notifications", schema="idn")
    op.drop_table("notifications", schema="idn")
