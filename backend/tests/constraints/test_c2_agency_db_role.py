"""C-2：機關側 Agent（A2、A3、A4）的資料庫連線帳號只有 anon schema 權限。

SPEC §15-8：「A3 帳號查 idn 被 DB 拒絕」。pub（非個資的里／據點／商家）允許唯讀，
但商家 api_key_hash 欄位不可見。
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.agents.accounts import AGENT_DB_ROLE, agent_engine
from app.db.engines import DbRole, get_engine

pytestmark = pytest.mark.constraint

AGENCY_AGENTS = ("A2", "A3", "A4")


def _denied(agent: str, statement: str) -> None:
    with agent_engine(agent).connect() as conn, pytest.raises(ProgrammingError) as exc:
        conn.execute(text(statement))
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)


@pytest.mark.parametrize("agent", AGENCY_AGENTS)
def test_agency_agents_are_bound_to_anon_reader(agent: str) -> None:
    assert AGENT_DB_ROLE[agent] is DbRole.ANON_READER
    with agent_engine(agent).connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == "anon_reader"


def test_anon_reader_schema_privileges() -> None:
    with get_engine(DbRole.ANON_READER).connect() as conn:

        def usage(schema: str) -> bool:
            return bool(
                conn.execute(
                    text("SELECT has_schema_privilege(current_user, :s, 'USAGE')"), {"s": schema}
                ).scalar_one()
            )

        assert usage("anon")
        assert not usage("idn")
        assert not usage("sys")
        assert not usage("public")


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM idn.users",
        "SELECT * FROM idn.fhir_resources",
        "SELECT * FROM idn.consents",
        "SELECT * FROM idn.care_tasks",
        "SELECT * FROM sys.audit_log",
        "SELECT * FROM sys.accounts",
    ],
)
def test_a3_reading_identifiable_or_system_tables_is_rejected_by_db(statement: str) -> None:
    _denied("A3", statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO anon.cohort_weekly VALUES ('2026-10-26', 'N01', '60-69', 'F', 'x', 1, 9)",
        "UPDATE anon.sentiment_alerts SET status = 'closed'",
        "DELETE FROM anon.cohort_weekly",
    ],
)
def test_agency_role_is_read_only(statement: str) -> None:
    _denied("A2", statement)


def test_agency_role_can_read_anon_and_public_reference_data() -> None:
    with agent_engine("A3").connect() as conn:
        conn.execute(text("SELECT count(*) FROM anon.cohort_weekly")).scalar_one()
        conn.execute(text("SELECT count(*) FROM pub.villages")).scalar_one()
        conn.execute(text("SELECT id, name FROM pub.merchants")).all()


def test_agency_role_cannot_read_merchant_api_key_hash() -> None:
    _denied("A4", "SELECT api_key_hash FROM pub.merchants")


def test_citizen_agent_uses_user_scoped_role_without_anon_access() -> None:
    assert AGENT_DB_ROLE["A1"] is DbRole.IDN_USER_SCOPED
    _denied("A1", "SELECT * FROM anon.cohort_weekly")


def test_site_agent_uses_site_scoped_role_without_anon_access() -> None:
    assert AGENT_DB_ROLE["A5"] is DbRole.IDN_SITE_SCOPED
    _denied("A5", "SELECT * FROM anon.cohort_weekly")


@pytest.mark.parametrize("agent", ["A2", "A3", "A4"])
def test_agency_agent_runtime_connections_are_anon_reader(agent: str) -> None:
    """runtime 實際給 A2／A3／A4 工具用的連線（含委派時）都是 anon_reader。"""
    from app.agents.agency import tool_context

    with tool_context(agent, "constraint-test") as ctx:
        assert ctx.db.execute(text("SELECT current_user")).scalar_one() == "anon_reader"
        with pytest.raises(ProgrammingError):
            ctx.db.execute(text("SELECT count(*) FROM idn.users"))
