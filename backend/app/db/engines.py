"""每個 DB 角色一個 engine。角色與權限在 migration 0001／0006 建立。

| 角色             | 用途                              | 可存取                       |
|------------------|-----------------------------------|------------------------------|
| migrator         | migrations、seed（超級使用者）    | 全部                         |
| idn_app          | API 的民眾／據點／商家／管理端點  | idn、pub、sys（不含 anon）   |
| idn_user_scoped  | A1（RLS：只看得到 app.user_id）   | idn（列層級）、pub           |
| idn_site_scoped  | A5（RLS：只看得到 app.site_id）   | idn.care_tasks 等、pub       |
| anon_reader      | A2/A3/A4、公衛儀表板              | anon、pub（唯讀）            |
| anon_writer      | deidentify 批次、A4 輿情落地      | anon（讀寫）、pub（唯讀）    |
| audit_writer     | Agent runtime／工具層寫稽核       | sys.audit_log（只能 INSERT） |
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any

from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.config import get_settings
from app.db.guard import install_guard


class DbRole(StrEnum):
    MIGRATOR = "migrator"
    IDN_APP = "idn_app"
    IDN_USER_SCOPED = "idn_user_scoped"
    IDN_SITE_SCOPED = "idn_site_scoped"
    ANON_READER = "anon_reader"
    ANON_WRITER = "anon_writer"
    AUDIT_WRITER = "audit_writer"


APP_ROLES: tuple[DbRole, ...] = tuple(r for r in DbRole if r is not DbRole.MIGRATOR)


def credentials(role: DbRole) -> tuple[str, str]:
    s = get_settings()
    if role is DbRole.MIGRATOR:
        return s.pg_superuser, s.pg_superpass
    password: str = getattr(s, f"{role.value}_password")
    return role.value, password


def url_for(role: DbRole, database: str | None = None) -> URL:
    s = get_settings()
    username, password = credentials(role)
    return URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=s.pg_host,
        port=s.pg_port,
        database=database or s.pg_db,
    )


_engines: dict[DbRole, Engine] = {}


def get_engine(role: DbRole) -> Engine:
    engine = _engines.get(role)
    if engine is None:
        engine = create_engine(
            url_for(role),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"application_name": f"healthapp:{role.value}"},
        )
        if role is not DbRole.MIGRATOR:
            install_guard(engine)
        _engines[role] = engine
    return engine


def dispose_engines() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


def session_factory(role: DbRole) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(role), expire_on_commit=False)


@contextmanager
def session_scope(role: DbRole) -> Iterator[Session]:
    """一個交易：成功 commit、例外 rollback。"""
    with session_factory(role)() as session, session.begin():
        yield session


def _bind_setting(session: Session, name: str, value: str) -> None:
    def _after_begin(sess: Session, trans: SessionTransaction, conn: Connection) -> None:
        conn.execute(text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value})

    event.listen(session, "after_begin", _after_begin)


@contextmanager
def user_scoped_session(user_id: uuid.UUID) -> Iterator[Session]:
    """A1 專用：每個交易開頭設定 app.user_id，RLS 只放行本人的列。"""
    session = session_factory(DbRole.IDN_USER_SCOPED)()
    _bind_setting(session, "app.user_id", str(user_id))
    try:
        with session.begin():
            yield session
    finally:
        session.close()


@contextmanager
def site_scoped_session(site_id: str) -> Iterator[Session]:
    """A5 專用：RLS 只放行本據點的關懷任務。"""
    session = session_factory(DbRole.IDN_SITE_SCOPED)()
    _bind_setting(session, "app.site_id", site_id)
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def current_db_user(conn: Connection | Session) -> Any:
    return conn.execute(text("SELECT current_user")).scalar_one()
