"""C-1：可識別區（idn）與去識別化區（anon）之間無任何 JOIN；資料只能經 deidentify 單向流動。

驗證三道防線：靜態掃描、執行期 guard、DB 權限。另驗證 anon 表沒有可反查欄位（SPEC §4.4）。
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from sqlalchemy import select, text

from app.db.engines import APP_ROLES, DbRole, get_engine, session_scope
from app.db.guard import CrossSchemaQueryError, is_cross_schema
from app.db.models_anon import CohortWeekly
from app.db.models_idn import User
from scripts.check_schema import scan_paths
from tests.helpers import raw_connect

pytestmark = pytest.mark.constraint

BACKEND = Path(__file__).resolve().parents[2]
CROSS_SQL = (
    "SELECT u.id, c.value FROM idn.users u "
    "JOIN anon.cohort_weekly c ON c.village_code = u.village_code"
)


class TestStaticScan:
    def test_codebase_has_no_cross_schema_sql(self) -> None:
        violations = scan_paths([BACKEND / "app", BACKEND / "scripts"])
        assert violations == [], "\n".join(str(v) for v in violations)

    def test_planted_join_in_python_is_caught(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text(f'QUERY = """{CROSS_SQL}"""\n', encoding="utf-8")
        violations = scan_paths([tmp_path])
        assert [v.kind for v in violations] == ["cross_schema_sql"]
        assert violations[0].line == 1

    def test_planted_join_in_sql_file_is_caught(self, tmp_path: Path) -> None:
        (tmp_path / "bad.sql").write_text(f"-- 報表\n{CROSS_SQL};\n", encoding="utf-8")
        assert [v.kind for v in scan_paths([tmp_path])] == ["cross_schema_sql"]

    def test_concatenated_strings_are_caught(self, tmp_path: Path) -> None:
        code = (
            'Q = ("SELECT * FROM idn.users u " "JOIN anon.cohort_weekly c USING (village_code)")\n'
        )
        (tmp_path / "bad.py").write_text(code, encoding="utf-8")
        assert [v.kind for v in scan_paths([tmp_path])] == ["cross_schema_sql"]

    def test_search_path_trick_is_caught(self, tmp_path: Path) -> None:
        code = (
            'Q = "SET search_path TO idn, anon; SELECT * FROM users JOIN cohort_weekly USING (x)"\n'
        )
        (tmp_path / "bad.py").write_text(code, encoding="utf-8")
        assert [v.kind for v in scan_paths([tmp_path])] == ["cross_schema_sql"]

    def test_module_importing_both_zones_is_caught(self, tmp_path: Path) -> None:
        code = "from app.db.models_idn import User\nfrom app.db.models_anon import CohortWeekly\n"
        (tmp_path / "service.py").write_text(code, encoding="utf-8")
        assert [v.kind for v in scan_paths([tmp_path])] == ["cross_zone_import"]

    def test_single_zone_queries_pass(self, tmp_path: Path) -> None:
        code = (
            'A = "SELECT * FROM idn.users"\n'
            'B = "SELECT * FROM anon.cohort_weekly"\n'
            '"""docstring：資料從 idn.users 經 deidentify 寫到 anon.cohort_weekly。"""\n'
        )
        (tmp_path / "ok.py").write_text(code, encoding="utf-8")
        assert scan_paths([tmp_path]) == []


class TestRuntimeGuard:
    @pytest.mark.parametrize(
        "statement",
        [
            CROSS_SQL,
            'SELECT 1 FROM "idn"."users" JOIN "anon"."cohort_weekly" ON true',
            "select 1 from IDN.users, ANON.cohort_weekly",
            "SELECT 1 FROM idn . users WHERE village_code IN "
            "(SELECT village_code FROM anon.cohort_weekly)",
            "SELECT set_config('search_path', 'idn, anon', false)",
        ],
    )
    def test_detects_cross_schema_variants(self, statement: str) -> None:
        assert is_cross_schema(statement)

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM idn.users",
            "SELECT * FROM anon.cohort_weekly -- 註解提到 idn.users 不算",
            "SELECT * FROM anon.sentiment_items WHERE summary = 'idn.users 字串常值不算'",
        ],
    )
    def test_single_schema_statements_pass(self, statement: str) -> None:
        assert not is_cross_schema(statement)

    @pytest.mark.parametrize("role", APP_ROLES, ids=lambda r: r.value)
    def test_every_app_engine_blocks_cross_schema_sql(self, role: DbRole) -> None:
        with get_engine(role).connect() as conn, pytest.raises(CrossSchemaQueryError):
            conn.execute(text(CROSS_SQL))

    def test_orm_generated_join_is_blocked(self) -> None:
        stmt = select(User.id).join(CohortWeekly, CohortWeekly.village_code == User.village_code)
        with session_scope(DbRole.IDN_APP) as s, pytest.raises(CrossSchemaQueryError):
            s.execute(stmt)


class TestDatabasePrivileges:
    def test_no_login_role_has_usage_on_both_zones(self) -> None:
        with session_scope(DbRole.MIGRATOR) as s:
            offenders = (
                s.execute(
                    text(
                        "SELECT rolname FROM pg_roles "
                        "WHERE NOT rolsuper AND rolname !~ '^pg_' "
                        "AND has_schema_privilege(rolname, 'idn', 'USAGE') "
                        "AND has_schema_privilege(rolname, 'anon', 'USAGE')"
                    )
                )
                .scalars()
                .all()
            )
        assert offenders == []

    @pytest.mark.parametrize("role", [DbRole.IDN_APP, DbRole.ANON_READER, DbRole.ANON_WRITER])
    def test_db_rejects_join_even_without_app_guard(self, role: DbRole) -> None:
        with raw_connect(role) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(CROSS_SQL)

    def test_anon_tables_have_no_identifying_columns(self) -> None:
        forbidden = {
            "user_id",
            "national_id_hash",
            "name",
            "address",
            "phone",
            "email",
            "birth_date",
            "author",
            "author_name",
            "author_url",
            "avatar",
            "avatar_url",
            "profile_photo_url",
        }
        with session_scope(DbRole.MIGRATOR) as s:
            columns = (
                s.execute(
                    text(
                        "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        "WHERE table_schema = 'anon' AND column_name = ANY(:cols)"
                    ),
                    {"cols": sorted(forbidden)},
                )
                .scalars()
                .all()
            )
        assert columns == []

    def test_anon_has_no_foreign_keys_into_idn(self) -> None:
        with session_scope(DbRole.MIGRATOR) as s:
            fks = (
                s.execute(
                    text(
                        "SELECT conname FROM pg_constraint c "
                        "JOIN pg_namespace src ON src.oid = c.connamespace "
                        "JOIN pg_class ref ON ref.oid = c.confrelid "
                        "JOIN pg_namespace dst ON dst.oid = ref.relnamespace "
                        "WHERE c.contype = 'f' AND ("
                        "(src.nspname = 'anon' AND dst.nspname = 'idn') OR "
                        "(src.nspname = 'idn' AND dst.nspname = 'anon'))"
                    )
                )
                .scalars()
                .all()
            )
        assert fks == []
