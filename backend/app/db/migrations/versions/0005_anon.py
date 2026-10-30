"""anon（去識別化區）：群體倉儲與輿情語料（SPEC §4.2）

此區任何表都不含 user_id／national_id_hash／姓名／地址（SPEC §4.4），也不對 idn 建外鍵。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "anon"


def upgrade() -> None:
    op.create_table(
        "cohort_weekly",
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("age_band", sa.Text(), nullable=False),
        sa.Column("sex", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "week", "village_code", "age_band", "sex", "metric", name=op.f("pk_cohort_weekly")
        ),
        schema=S,
    )
    op.create_index(
        op.f("ix_cohort_weekly_metric_week"), "cohort_weekly", ["metric", "week"], schema=S
    )

    op.create_table(
        "site_ops_weekly",
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("visits", sa.Integer(), nullable=False),
        sa.Column("bookings", sa.Integer(), nullable=False),
        sa.Column("no_show", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("week", "site_id", name=op.f("pk_site_ops_weekly")),
        schema=S,
    )

    op.create_table(
        "merchant_weekly",
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("redemptions", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("week", "merchant_id", name=op.f("pk_merchant_weekly")),
        schema=S,
    )

    op.create_table(
        "visit_outcomes",
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("reason_class", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "week", "village_code", "task_type", "reason_class", name=op.f("pk_visit_outcomes")
        ),
        schema=S,
    )

    op.create_table(
        "sentiment_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_class", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("sentiment", sa.Text(), nullable=False),
        sa.Column("intensity", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentiment_items")),
        schema=S,
    )
    op.create_index(
        op.f("ix_sentiment_items_village_code_topic_collected_at"),
        "sentiment_items",
        ["village_code", "topic", "collected_at"],
        schema=S,
    )

    op.create_table(
        "sentiment_alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("baseline", sa.Numeric(), nullable=False),
        sa.Column("ratio", sa.Numeric(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentiment_alerts")),
        schema=S,
    )

    op.create_table(
        "app_metrics_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("dau", sa.Integer(), nullable=False),
        sa.Column("mau", sa.Integer(), nullable=False),
        sa.Column("retention_7", sa.Numeric(), nullable=False),
        sa.Column("retention_30", sa.Numeric(), nullable=False),
        sa.Column("push_open_rate", sa.Numeric(), nullable=False),
        sa.Column("elder_mode_rate", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_app_metrics_daily")),
        schema=S,
    )


def downgrade() -> None:
    for table in (
        "app_metrics_daily",
        "sentiment_alerts",
        "sentiment_items",
        "visit_outcomes",
        "merchant_weekly",
        "site_ops_weekly",
        "cohort_weekly",
    ):
        op.drop_table(table, schema=S)
