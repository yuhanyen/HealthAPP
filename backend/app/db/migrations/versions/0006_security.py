"""權限、列層級安全（RLS）、append-only trigger

- C-1／C-2：沒有任何應用角色同時擁有 idn 與 anon 的權限；機關側只有 anon_reader。
- C-3（DB 層防線）：idn_user_scoped 以 RLS 只看得到 app.user_id 本人的列。
- C-6：sys.audit_log（以及 idn.points_ledger、idn.decode_audit）禁止 UPDATE/DELETE/TRUNCATE。

之後新增資料表的 migration，要記得在同一個 migration 補上對應 GRANT（測試會抓漏）。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLES = (
    "idn_app",
    "idn_user_scoped",
    "idn_site_scoped",
    "anon_reader",
    "anon_writer",
    "audit_writer",
)
PUB_READABLE = ("pub.villages", "pub.sites", "pub.offers", "pub.env_hourly", "pub.source_whitelist")
# 商家 api_key_hash 不給 idn_app 以外的角色看
MERCHANT_PUBLIC_COLUMNS = "id, name, category, village_code, lat, lng, hours, active"

USER_SCOPED_READ = (
    "users",
    "consents",
    "family_links",
    "fhir_resources",
    "points_ledger",
    "redemptions",
    "bookings",
    "twin_snapshots",
    "daily_reports",
    "pending_actions",
)
APPEND_ONLY = ("sys.audit_log", "idn.points_ledger", "idn.decode_audit")

_ME = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_SITE = "NULLIF(current_setting('app.site_id', true), '')"


def _grant_pub_read(role: str) -> None:
    op.execute(f"GRANT SELECT ON {', '.join(PUB_READABLE)} TO {role}")
    op.execute(f"GRANT SELECT ({MERCHANT_PUBLIC_COLUMNS}) ON pub.merchants TO {role}")


def _policy(table: str, name: str, role: str, using: str, check: str | None = None) -> None:
    op.execute(f"DROP POLICY IF EXISTS {name} ON idn.{table}")
    with_check = f" WITH CHECK ({check})" if check else ""
    op.execute(f"CREATE POLICY {name} ON idn.{table} TO {role} USING ({using}){with_check}")


def upgrade() -> None:
    roles = ", ".join(APP_ROLES)
    # 先全部收回，確保可重複套用
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA pub, sys, idn, anon FROM {roles}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA pub, sys, idn, anon FROM {roles}")
    op.execute(f"REVOKE ALL ON SCHEMA pub, sys, idn, anon FROM {roles}")

    # --- idn_app：API（民眾／據點／商家／管理端），不碰 anon ---
    op.execute("GRANT USAGE ON SCHEMA idn, pub, sys TO idn_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA idn TO idn_app")
    op.execute("REVOKE UPDATE, DELETE ON idn.points_ledger, idn.decode_audit FROM idn_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA pub TO idn_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA pub TO idn_app")
    op.execute("GRANT SELECT, INSERT ON sys.audit_log TO idn_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON sys.config, sys.accounts TO idn_app")

    # --- idn_user_scoped：A1，只讀本人（RLS） ---
    op.execute("GRANT USAGE ON SCHEMA idn, pub TO idn_user_scoped")
    op.execute(
        f"GRANT SELECT ON {', '.join('idn.' + t for t in USER_SCOPED_READ)} TO idn_user_scoped"
    )
    op.execute(
        "GRANT INSERT ON idn.twin_snapshots, idn.daily_reports, idn.pending_actions "
        "TO idn_user_scoped"
    )
    op.execute("GRANT UPDATE ON idn.daily_reports TO idn_user_scoped")
    _grant_pub_read("idn_user_scoped")

    # --- idn_site_scoped：A5，只看本據點的關懷任務，解碼只拿必要欄位 ---
    op.execute("GRANT USAGE ON SCHEMA idn, pub TO idn_site_scoped")
    op.execute("GRANT SELECT ON idn.care_tasks TO idn_site_scoped")
    op.execute("GRANT SELECT (id, name, phone, address) ON idn.users TO idn_site_scoped")
    op.execute("GRANT INSERT ON idn.decode_audit TO idn_site_scoped")
    _grant_pub_read("idn_site_scoped")

    # --- anon_reader：A2／A3／A4 與公衛儀表板，只讀 anon ---
    op.execute("GRANT USAGE ON SCHEMA anon, pub TO anon_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA anon TO anon_reader")
    _grant_pub_read("anon_reader")

    # --- anon_writer：deidentify 批次與 A4 輿情落地 ---
    op.execute("GRANT USAGE ON SCHEMA anon, pub TO anon_writer")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA anon TO anon_writer")
    _grant_pub_read("anon_writer")

    # --- audit_writer：只能新增稽核（RETURNING id 需要 id 欄的 SELECT） ---
    op.execute("GRANT USAGE ON SCHEMA sys TO audit_writer")
    op.execute("GRANT INSERT ON sys.audit_log TO audit_writer")
    op.execute("GRANT SELECT (id) ON sys.audit_log TO audit_writer")

    # --- RLS ---
    rls_tables = (*USER_SCOPED_READ, "care_tasks", "decode_audit")
    for table in rls_tables:
        op.execute(f"ALTER TABLE idn.{table} ENABLE ROW LEVEL SECURITY")
        _policy(table, f"p_{table}_idn_app", "idn_app", "true", "true")

    for table in USER_SCOPED_READ:
        column = {"users": "id", "family_links": "owner_user_id"}.get(table, "user_id")
        _policy(
            table,
            f"p_{table}_user_scoped",
            "idn_user_scoped",
            f"{column} = {_ME}",
            f"{column} = {_ME}",
        )

    _policy("care_tasks", "p_care_tasks_site_scoped", "idn_site_scoped", f"site_id = {_SITE}")
    _policy(
        "users",
        "p_users_site_scoped",
        "idn_site_scoped",
        "EXISTS (SELECT 1 FROM idn.care_tasks c "
        f"WHERE c.user_id = users.id AND c.site_id = {_SITE})",
    )
    _policy(
        "decode_audit",
        "p_decode_audit_site_scoped",
        "idn_site_scoped",
        f"site_id = {_SITE}",
        f"site_id = {_SITE}",
    )

    # --- append-only ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sys.forbid_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'append-only: % on %.% is not allowed',
            TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    for qualified in APPEND_ONLY:
        name = qualified.replace(".", "_")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{name}_no_update_delete ON {qualified}")
        op.execute(
            f"CREATE TRIGGER trg_{name}_no_update_delete BEFORE UPDATE OR DELETE ON {qualified} "
            "FOR EACH ROW EXECUTE FUNCTION sys.forbid_mutation()"
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_{name}_no_truncate ON {qualified}")
        op.execute(
            f"CREATE TRIGGER trg_{name}_no_truncate BEFORE TRUNCATE ON {qualified} "
            "FOR EACH STATEMENT EXECUTE FUNCTION sys.forbid_mutation()"
        )


def downgrade() -> None:
    for qualified in APPEND_ONLY:
        name = qualified.replace(".", "_")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{name}_no_update_delete ON {qualified}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{name}_no_truncate ON {qualified}")
    op.execute("DROP FUNCTION IF EXISTS sys.forbid_mutation()")
    # 政策會互相依賴（users 的據點政策引用 care_tasks），全部移除才能往下 downgrade
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT policyname, tablename FROM pg_policies WHERE schemaname = 'idn' LOOP
            EXECUTE format('DROP POLICY %I ON idn.%I', r.policyname, r.tablename);
          END LOOP;
        END
        $$;
        """
    )
    for table in (*USER_SCOPED_READ, "care_tasks", "decode_audit"):
        op.execute(f"ALTER TABLE idn.{table} DISABLE ROW LEVEL SECURITY")
    roles = ", ".join(APP_ROLES)
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA pub, sys, idn, anon FROM {roles}")
    op.execute(f"REVOKE ALL ON SCHEMA pub, sys, idn, anon FROM {roles}")
