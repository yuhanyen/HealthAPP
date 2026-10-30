"""測試共用小工具（conftest 只放 fixture）。"""

from __future__ import annotations

from typing import Any

import psycopg
from sqlalchemy import func, select

from app.config import get_settings
from app.db.engines import DbRole, credentials, session_scope
from app.db.models_sys import AuditLog

TEST_VILLAGE = "T01"
TEST_SITE = "S-T01"
OTHER_SITE = "S-OTHER"


def raw_connect(role: DbRole) -> psycopg.Connection[tuple[Any, ...]]:
    """不經 SQLAlchemy（也就不經 guard）直接連線，用來驗證 DB 層防線本身。"""
    s = get_settings()
    user, password = credentials(role)
    return psycopg.connect(
        host=s.pg_host, port=s.pg_port, dbname=s.pg_db, user=user, password=password
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def audit_rows(**filters: str) -> list[AuditLog]:
    with session_scope(DbRole.IDN_APP) as s:
        stmt = select(AuditLog).order_by(AuditLog.id)
        for column, value in filters.items():
            stmt = stmt.where(getattr(AuditLog, column) == value)
        rows = list(s.scalars(stmt))
        for row in rows:
            s.expunge(row)
        return rows


def audit_count(**filters: str) -> int:
    with session_scope(DbRole.IDN_APP) as s:
        stmt = select(func.count()).select_from(AuditLog)
        for column, value in filters.items():
            stmt = stmt.where(getattr(AuditLog, column) == value)
        return int(s.execute(stmt).scalar_one())
