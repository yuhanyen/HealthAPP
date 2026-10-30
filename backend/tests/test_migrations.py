"""Migrations：與模型一致、可完整 downgrade／upgrade、每張表都有對應角色權限。"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from psycopg import sql
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.db.all_models import Base
from app.db.base import SCHEMAS
from app.db.engines import DbRole, session_scope, url_for

BACKEND = Path(__file__).resolve().parents[1]


def _alembic(database: str | None = None) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app" / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    if database:
        cfg.attributes["database"] = database
    return cfg


def test_models_match_migrated_schema() -> None:
    engine = create_engine(url_for(DbRole.MIGRATOR))
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "include_name": lambda name, type_, parents: (
                    name in SCHEMAS if type_ == "schema" else True
                ),
                "compare_type": True,
            },
        )
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == []


def test_full_downgrade_and_upgrade_roundtrip() -> None:
    s = get_settings()
    name = f"{s.pg_db}_roundtrip"
    conninfo = (
        f"host={s.pg_host} port={s.pg_port} dbname=postgres "
        f"user={s.pg_superuser} password={s.pg_superpass}"
    )
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        cfg = _alembic(name)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.mark.parametrize(
    ("schema", "role", "privilege"),
    [
        ("idn", "idn_app", "SELECT"),
        ("anon", "anon_reader", "SELECT"),
        ("anon", "anon_writer", "INSERT"),
    ],
)
def test_every_table_is_granted_to_its_zone_role(schema: str, role: str, privilege: str) -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        missing = (
            s.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = :schema "
                    "AND NOT has_table_privilege(:role, schemaname || '.' || tablename, :priv)"
                ),
                {"schema": schema, "role": role, "priv": privilege},
            )
            .scalars()
            .all()
        )
    assert missing == []


def test_rls_enabled_on_user_scoped_tables() -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        no_rls = (
            s.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'idn' AND c.relkind = 'r' AND NOT c.relrowsecurity "
                    "AND has_table_privilege('idn_user_scoped', c.oid, 'SELECT')"
                )
            )
            .scalars()
            .all()
        )
    assert no_rls == []
