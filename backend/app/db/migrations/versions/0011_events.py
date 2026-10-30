"""M8：近日社區活動、報名與報到、推薦偏好、商家評分（SPEC §17）

- pub.events：公開活動（據點／機關上架，或公開公告匯入後待審）。公開資訊，各角色可讀；
  公告匯入只用 anon_writer 寫入（不接觸 idn）。
- pub.merchants 增加評分與健康標籤；評分同步同樣只用 anon_writer（只能更新評分欄位）。
- idn.user_prefs：本人的推薦偏好；idn.event_registrations：報名與報到（個資）。
  A1（idn_user_scoped）可讀本人的偏好與報名，可取消本人的報名（只能改 status）。
- pending_actions 增加高風險動作 event_register（報名會把姓名交給主辦單位）。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ME = "NULLIF(current_setting('app.user_id', true), '')::uuid"
CATEGORIES = "('measure', 'walk', 'exercise', 'education', 'nutrition', 'vaccine', 'social')"
OLD_ACTIONS = "('booking', 'share', 'redeem', 'consent_revoke')"
NEW_ACTIONS = "('booking', 'share', 'redeem', 'consent_revoke', 'event_register')"
RATING_COLUMNS = "rating, rating_count, rating_source, rating_updated_at"
READERS = "idn_user_scoped, idn_site_scoped, anon_reader, anon_writer"


def _ts(name: str, *, now: bool = False, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if now else None,
        nullable=nullable,
    )


def _jsonb_list(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("organizer", sa.Text(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=True),
        sa.Column("village_code", sa.Text(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=True),
        sa.Column("lng", sa.Double(), nullable=True),
        _ts("start_at"),
        _ts("end_at"),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("points_reward", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _jsonb_list("tags"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("extracted_by", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending_review'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        _ts("reviewed_at", nullable=True),
        _ts("created_at", now=True),
        sa.CheckConstraint(f"category IN {CATEGORIES}", name=op.f("ck_events_category")),
        sa.CheckConstraint("source IN ('site', 'agency', 'crawl')", name=op.f("ck_events_source")),
        sa.CheckConstraint(
            "status IN ('pending_review', 'published', 'rejected', 'cancelled')",
            name=op.f("ck_events_status"),
        ),
        sa.CheckConstraint("end_at > start_at", name=op.f("ck_events_window")),
        sa.CheckConstraint("points_reward >= 0", name=op.f("ck_events_points_reward")),
        sa.ForeignKeyConstraint(
            ["site_id"], ["pub.sites.id"], name=op.f("fk_events_site_id_sites")
        ),
        sa.ForeignKeyConstraint(
            ["village_code"], ["pub.villages.code"], name=op.f("fk_events_village_code_villages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_events_dedupe_key")),
        schema="pub",
    )
    op.create_index(
        op.f("ix_events_status_start_at"), "events", ["status", "start_at"], schema="pub"
    )

    op.add_column("merchants", sa.Column("rating", sa.Numeric(2, 1), nullable=True), schema="pub")
    op.add_column("merchants", sa.Column("rating_count", sa.Integer(), nullable=True), schema="pub")
    op.add_column("merchants", sa.Column("rating_source", sa.Text(), nullable=True), schema="pub")
    op.add_column("merchants", _ts("rating_updated_at", nullable=True), schema="pub")
    op.add_column("merchants", _jsonb_list("tags"), schema="pub")

    op.create_table(
        "user_prefs",
        sa.Column("user_id", sa.UUID(), nullable=False),
        _jsonb_list("event_categories"),
        _jsonb_list("merchant_categories"),
        _jsonb_list("dietary"),
        sa.Column("min_rating", sa.Numeric(2, 1), nullable=True),
        sa.Column("max_distance_km", sa.Numeric(4, 1), nullable=True),
        sa.Column("event_push", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("updated_at", now=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_user_prefs_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_prefs")),
        schema="idn",
    )
    op.create_table(
        "event_registrations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'registered'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        _ts("registered_at", now=True),
        _ts("checked_in_at", nullable=True),
        sa.Column("checked_in_by", sa.UUID(), nullable=True),
        sa.Column("points_granted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "status IN ('registered', 'cancelled', 'attended')",
            name=op.f("ck_event_registrations_status"),
        ),
        sa.CheckConstraint(
            "created_by IN ('user', 'a1')", name=op.f("ck_event_registrations_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["idn.users.id"], name=op.f("fk_event_registrations_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["pub.events.id"], name=op.f("fk_event_registrations_event_id_events")
        ),
        sa.ForeignKeyConstraint(
            ["checked_in_by"],
            ["sys.accounts.id"],
            name=op.f("fk_event_registrations_checked_in_by_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_registrations")),
        sa.UniqueConstraint(
            "user_id", "event_id", name=op.f("uq_event_registrations_user_id_event_id")
        ),
        schema="idn",
    )
    op.create_index(
        op.f("ix_event_registrations_event_id_status"),
        "event_registrations",
        ["event_id", "status"],
        schema="idn",
    )

    op.drop_constraint("ck_pending_actions_action_type", "pending_actions", schema="idn")
    op.create_check_constraint(
        op.f("ck_pending_actions_action_type"),
        "pending_actions",
        f"action_type IN {NEW_ACTIONS}",
        schema="idn",
    )

    # 名額：只回人數。A1 的 RLS 連線看不到別人的報名，靠這個函式（SECURITY DEFINER）取得已占名額
    op.execute(
        """
        CREATE FUNCTION idn.event_seats_taken(ids text[])
        RETURNS TABLE (event_id text, taken int)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
          SELECT r.event_id, count(*)::int FROM idn.event_registrations r
          WHERE r.event_id = ANY(ids) AND r.status IN ('registered', 'attended')
          GROUP BY r.event_id
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION idn.event_seats_taken(text[]) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION idn.event_seats_taken(text[]) TO idn_app, idn_user_scoped"
    )

    # --- 權限 ---
    op.execute("GRANT SELECT, INSERT, UPDATE ON pub.events TO idn_app")
    op.execute(f"GRANT SELECT ON pub.events TO {READERS}")
    op.execute("GRANT INSERT, UPDATE ON pub.events TO anon_writer")
    op.execute(f"GRANT SELECT ({RATING_COLUMNS}, tags) ON pub.merchants TO {READERS}")
    op.execute(f"GRANT UPDATE ({RATING_COLUMNS}) ON pub.merchants TO anon_writer")

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON idn.user_prefs TO idn_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON idn.event_registrations TO idn_app")
    op.execute("GRANT SELECT ON idn.user_prefs, idn.event_registrations TO idn_user_scoped")
    op.execute("GRANT UPDATE (status) ON idn.event_registrations TO idn_user_scoped")
    for table in ("user_prefs", "event_registrations"):
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
    for table in ("user_prefs", "event_registrations"):
        op.execute(f"DROP POLICY IF EXISTS p_{table}_idn_app ON idn.{table}")
        op.execute(f"DROP POLICY IF EXISTS p_{table}_user_scoped ON idn.{table}")
    op.execute("DROP FUNCTION IF EXISTS idn.event_seats_taken(text[])")
    op.execute("DELETE FROM idn.pending_actions WHERE action_type = 'event_register'")
    op.drop_constraint("ck_pending_actions_action_type", "pending_actions", schema="idn")
    op.create_check_constraint(
        op.f("ck_pending_actions_action_type"),
        "pending_actions",
        f"action_type IN {OLD_ACTIONS}",
        schema="idn",
    )
    op.drop_index(
        op.f("ix_event_registrations_event_id_status"), "event_registrations", schema="idn"
    )
    op.drop_table("event_registrations", schema="idn")
    op.drop_table("user_prefs", schema="idn")
    op.execute(f"REVOKE SELECT ({RATING_COLUMNS}, tags) ON pub.merchants FROM {READERS}")
    op.execute(f"REVOKE UPDATE ({RATING_COLUMNS}) ON pub.merchants FROM anon_writer")
    for column in ("tags", "rating_updated_at", "rating_source", "rating_count", "rating"):
        op.drop_column("merchants", column, schema="pub")
    op.drop_index(op.f("ix_events_status_start_at"), "events", schema="pub")
    op.drop_table("events", schema="pub")
