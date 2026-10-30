"""schemas 與 DB 角色

四個 schema：pub（共用非個資）、sys（稽核／設定／帳號）、idn（可識別區）、anon（去識別化區）。
角色為叢集層級、跨資料庫共用，故 downgrade 不刪角色。

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("pub", "sys", "idn", "anon")
ROLES = {
    "idn_app": "idn_app_password",
    "idn_user_scoped": "idn_user_scoped_password",
    "idn_site_scoped": "idn_site_scoped_password",
    "anon_reader": "anon_reader_password",
    "anon_writer": "anon_writer_password",
    "audit_writer": "audit_writer_password",
}


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    settings = get_settings()
    for role, attr in ROLES.items():
        password = getattr(settings, attr)
        if not password:
            raise RuntimeError(f"缺少環境變數 {attr.upper()}，無法建立角色 {role}")
        options = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
        op.execute(
            f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} {options} PASSWORD {_quote(password)};
              ELSE
                ALTER ROLE {role} WITH {options} PASSWORD {_quote(password)};
              END IF;
            END
            $$;
            """
        )

    # public schema 不給任何應用角色使用
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
