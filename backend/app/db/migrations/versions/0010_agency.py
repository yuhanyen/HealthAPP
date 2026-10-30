"""M5：訪視回報、輿情警訊的 A2 分析、關懷清單每日統計

- idn.visit_reports：志工／據點人員的訪視回報
  （原因分類、需要協助、是否需轉介由人判斷、下次追蹤日）。
  備註可能含姓名，應用層加密。只有 idn_app（據點 API）讀寫。
- anon.visit_outcomes 由 deidentify 從 visit_reports 聚合（去姓名、k-匿名），見 aggregate.py。
- anon.sentiment_alerts.analysis：A2 對警訊的對照分析（委派 A3／A4 的結果與結論）。
- anon.care_list_daily：A2 每日關懷清單的數量（日期 × 據點 × 任務類型），供趨勢追蹤；不含代號。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = "('distance', 'equipment', 'willingness', 'health', 'other')"


def upgrade() -> None:
    op.create_table(
        "visit_reports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("care_task_id", sa.UUID(), nullable=False),
        sa.Column("reported_by", sa.UUID(), nullable=False),
        sa.Column("task_ref", sa.Text(), nullable=False),
        sa.Column("reason_class", sa.Text(), nullable=False),
        sa.Column("needs", postgresql.JSONB(), nullable=False),
        sa.Column("referral", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("next_followup", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"reason_class IN {REASONS}", name=op.f("ck_visit_reports_reason_class")
        ),
        sa.ForeignKeyConstraint(
            ["care_task_id"],
            ["idn.care_tasks.id"],
            name=op.f("fk_visit_reports_care_task_id_care_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["reported_by"], ["sys.accounts.id"], name=op.f("fk_visit_reports_reported_by_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_reports")),
        schema="idn",
    )
    op.create_index(
        op.f("ix_visit_reports_care_task_id"), "visit_reports", ["care_task_id"], schema="idn"
    )
    op.add_column(
        "sentiment_alerts",
        sa.Column("analysis", postgresql.JSONB(), nullable=True),
        schema="anon",
    )
    op.create_table(
        "care_list_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", "site_id", "task_type", name=op.f("pk_care_list_daily")),
        schema="anon",
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON idn.visit_reports TO idn_app")
    op.execute("ALTER TABLE idn.visit_reports ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY p_visit_reports_idn_app ON idn.visit_reports TO idn_app "
        "USING (true) WITH CHECK (true)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON anon.care_list_daily TO anon_writer")
    op.execute("GRANT SELECT ON anon.care_list_daily TO anon_reader")


def downgrade() -> None:
    op.drop_table("care_list_daily", schema="anon")
    op.drop_column("sentiment_alerts", "analysis", schema="anon")
    op.execute("DROP POLICY IF EXISTS p_visit_reports_idn_app ON idn.visit_reports")
    op.drop_index(op.f("ix_visit_reports_care_task_id"), "visit_reports", schema="idn")
    op.drop_table("visit_reports", schema="idn")
