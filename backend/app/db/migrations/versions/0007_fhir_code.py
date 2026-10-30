"""M2：fhir_resources 加 code（主要編碼，LOINC／ICD-10…），唯一鍵納入 code

SPEC §4.1 的唯一鍵 (user, type, source, effective_at) 會讓同一小時的步數與心率、
同一次據點量測的血壓與體重互撞；加上 code 才能區分。見 docs/DECISIONS.md。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fhir_resources", sa.Column("code", sa.Text(), nullable=True), schema="idn")
    op.execute(
        """
        UPDATE idn.fhir_resources SET code = COALESCE(
          resource->'code'->'coding'->0->>'code',
          resource->'vaccineCode'->'coding'->0->>'code',
          resource->'medicationCodeableConcept'->'coding'->0->>'code',
          resource->'class'->>'code',
          lower(resource_type))
        """
    )
    op.alter_column("fhir_resources", "code", nullable=False, schema="idn")
    op.drop_constraint(
        "uq_fhir_resources_user_id_resource_type_source_effective_at",
        "fhir_resources",
        schema="idn",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_fhir_resources_natural_key",
        "fhir_resources",
        ["user_id", "resource_type", "source", "code", "effective_at"],
        schema="idn",
    )
    op.create_index(
        op.f("ix_fhir_resources_user_id_code_effective_at"),
        "fhir_resources",
        ["user_id", "code", "effective_at"],
        schema="idn",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_fhir_resources_user_id_code_effective_at"), "fhir_resources", schema="idn"
    )
    op.drop_constraint(
        "uq_fhir_resources_natural_key",
        "fhir_resources",
        schema="idn",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_fhir_resources_user_id_resource_type_source_effective_at",
        "fhir_resources",
        ["user_id", "resource_type", "source", "effective_at"],
        schema="idn",
    )
    op.drop_column("fhir_resources", "code", schema="idn")
