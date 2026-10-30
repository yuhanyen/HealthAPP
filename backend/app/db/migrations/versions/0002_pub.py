"""pub：里、據點、商家、優惠、環境、輿情來源白名單（SPEC §4.3）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "villages",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("district", sa.Text(), nullable=False),
        sa.Column("population", sa.Integer(), nullable=False),
        sa.Column("geom", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_villages")),
        schema="pub",
    )
    op.create_table(
        "sites",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("hours", postgresql.JSONB(), nullable=False),
        sa.Column("equipment", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["village_code"], ["pub.villages.code"], name=op.f("fk_sites_village_code_villages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
        schema="pub",
    )
    op.create_table(
        "merchants",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("village_code", sa.Text(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("hours", postgresql.JSONB(), nullable=False),
        sa.Column("api_key_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["village_code"],
            ["pub.villages.code"],
            name=op.f("fk_merchants_village_code_villages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchants")),
        schema="pub",
    )
    op.create_table(
        "offers",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["pub.merchants.id"], name=op.f("fk_offers_merchant_id_merchants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
        schema="pub",
    )
    op.create_table(
        "env_hourly",
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("district", sa.Text(), nullable=False),
        sa.Column("temp", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("heat_index", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("aqi", sa.Integer(), nullable=False),
        sa.Column("rain", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("hour", "district", name=op.f("pk_env_hourly")),
        schema="pub",
    )
    op.create_table(
        "source_whitelist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_class", sa.Text(), nullable=False),
        sa.Column("url_pattern", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_whitelist")),
        schema="pub",
    )


def downgrade() -> None:
    for table in ("source_whitelist", "env_hourly", "offers", "merchants", "sites", "villages"):
        op.drop_table(table, schema="pub")
