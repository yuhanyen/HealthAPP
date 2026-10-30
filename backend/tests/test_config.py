"""設定：雲端平台的 DATABASE_URL 覆蓋 PG_HOST／PG_PORT／PG_DB／PG_SUPERUSER／PG_SUPERPASS。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_database_url_overrides_individual_settings() -> None:
    s = Settings(
        database_url="postgresql://healthapp_user:p%40ss%2Fw0rd@dpg-abc123-a/healthapp_xyz",
        pg_host="127.0.0.1",
        pg_port=55432,
    )
    assert (s.pg_host, s.pg_port, s.pg_db) == ("dpg-abc123-a", 5432, "healthapp_xyz")
    assert (s.pg_superuser, s.pg_superpass) == ("healthapp_user", "p@ss/w0rd")


def test_explicit_port_is_kept() -> None:
    s = Settings(database_url="postgres://u:p@db.example.com:6543/app")
    assert (s.pg_host, s.pg_port, s.pg_db) == ("db.example.com", 6543, "app")


def test_without_database_url_individual_settings_are_used() -> None:
    s = Settings(database_url="", pg_host="10.0.0.5", pg_port=5433)
    assert (s.pg_host, s.pg_port) == ("10.0.0.5", 5433)


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="mysql://u:p@h/db")
