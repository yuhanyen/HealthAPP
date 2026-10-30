"""C-6：每次 Agent 工具呼叫與每次代號解碼寫入稽核日誌（append-only）。

- sys.audit_log 由 DB trigger 禁止 UPDATE／DELETE／TRUNCATE（連超級使用者也擋）。
- 工具層每次呼叫（成功、拒絕、待確認）恰好一筆稽核；只存摘要雜湊，不存原始值。
- 代號解碼必附任務編號；成功寫 idn.decode_audit + sys.audit_log，被拒也留稽核。
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from sqlalchemy import select, text

from app.audit.writer import write_audit
from app.care.decode import DecodeRejected, decode_care_task
from app.db.engines import APP_ROLES, DbRole, session_scope, user_scoped_session
from app.db.models_idn import DecodeAudit
from app.tools.registry import CitizenToolArgs, ConsentScopeError, ToolContext, ToolRegistry
from tests.conftest import Factory
from tests.helpers import TEST_SITE, audit_count, audit_rows, raw_connect

pytestmark = pytest.mark.constraint

APPEND_ONLY = ("sys.audit_log", "idn.points_ledger", "idn.decode_audit")


def _seed_audit_row() -> int:
    return write_audit(
        None, actor_type="system", actor_id="test", action="test.seed", decision="executed"
    )


class TestAppendOnly:
    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE sys.audit_log SET decision = 'tampered' WHERE id = %s",
            "DELETE FROM sys.audit_log WHERE id = %s",
        ],
    )
    def test_even_superuser_cannot_modify_audit_rows(self, statement: str) -> None:
        row_id = _seed_audit_row()
        with (
            raw_connect(DbRole.MIGRATOR) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(statement, (row_id,))
        assert audit_count(action="test.seed", decision="executed") >= 1

    @pytest.mark.parametrize("table", APPEND_ONLY)
    def test_truncate_is_blocked(self, table: str) -> None:
        with (
            raw_connect(DbRole.MIGRATOR) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(f"TRUNCATE {table} CASCADE")

    @pytest.mark.parametrize("table", APPEND_ONLY)
    @pytest.mark.parametrize("role", APP_ROLES, ids=lambda r: r.value)
    def test_no_app_role_holds_update_or_delete(self, table: str, role: DbRole) -> None:
        with session_scope(DbRole.MIGRATOR) as s:
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                granted = s.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": role.value, "t": table, "p": privilege},
                ).scalar_one()
                assert granted is False, f"{role.value} 對 {table} 有 {privilege}"

    def test_points_ledger_rows_are_immutable(self, factory: Factory) -> None:
        user = factory.user()
        with raw_connect(DbRole.MIGRATOR) as conn:
            row_id = conn.execute(
                "INSERT INTO idn.points_ledger (user_id, delta, reason, balance_after) "
                "VALUES (%s, 10, 'test', 10) RETURNING id",
                (user.id,),
            ).fetchone()
            conn.commit()
            assert row_id is not None
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE idn.points_ledger SET delta = 9999 WHERE id = %s", row_id)


class ProbeArgs(CitizenToolArgs):
    note: str = ""


@pytest.fixture
def probe() -> tuple[ToolRegistry, str, str]:
    reg = ToolRegistry()
    plain, risky = f"probe_plain_{uuid.uuid4().hex[:6]}", f"probe_risky_{uuid.uuid4().hex[:6]}"

    @reg.tool(
        name=plain, schema=ProbeArgs, side="citizen", required_scope=lambda a: {"site": ["bp"]}
    )
    def _plain(ctx: ToolContext, args: ProbeArgs) -> dict[str, Any]:
        return {"bp_last": [142, 91]}

    @reg.tool(
        name=risky,
        schema=ProbeArgs,
        side="citizen",
        high_risk=lambda a: True,
        action_type="booking",
    )
    def _risky(ctx: ToolContext, args: ProbeArgs) -> dict[str, Any]:
        return {"booking_id": "B-1"}

    return reg, plain, risky


class TestToolCallAuditing:
    def test_every_tool_call_writes_exactly_one_row(
        self, factory: Factory, probe: tuple[ToolRegistry, str, str]
    ) -> None:
        reg, plain, risky = probe
        user = factory.user()
        factory.consent(user.id, "site", ["bp"])
        scope = {"site": ["bp"]}

        with user_scoped_session(user.id) as s:
            ctx = ToolContext(agent="A1", db=s, principal_user_id=user.id)
            reg.call(plain, {"user_id": str(user.id), "consent_scope": scope}, ctx)
            with pytest.raises(ConsentScopeError):
                reg.call(plain, {"user_id": str(user.id)}, ctx)
            reg.call(risky, {"user_id": str(user.id), "consent_scope": scope}, ctx)

        plain_rows = audit_rows(tool=plain)
        assert [r.decision for r in plain_rows] == ["executed", "rejected"]
        assert [r.decision for r in audit_rows(tool=risky)] == ["pending_confirmation"]

        executed = plain_rows[0]
        assert (executed.actor_type, executed.actor_id) == ("agent", f"A1:{user.id}")
        assert executed.consent_scope == scope
        assert executed.input_digest is not None and executed.input_digest.startswith("sha256:")
        assert executed.output_digest is not None and executed.output_digest.startswith("sha256:")
        assert plain_rows[1].reason

    def test_audit_keeps_digests_not_raw_values(
        self, factory: Factory, probe: tuple[ToolRegistry, str, str]
    ) -> None:
        reg, plain, _ = probe
        user = factory.user()
        factory.consent(user.id, "site", ["bp"])
        secret = "請打 0912-345-678 找王小明"
        with user_scoped_session(user.id) as s:
            reg.call(
                plain,
                {"user_id": str(user.id), "consent_scope": {"site": ["bp"]}, "note": secret},
                ToolContext(agent="A1", db=s, principal_user_id=user.id),
            )
        with session_scope(DbRole.IDN_APP) as s:
            dumped = s.execute(
                text("SELECT row_to_json(a)::text FROM sys.audit_log a WHERE tool = :t"),
                {"t": plain},
            ).scalar_one()
        assert "0912-345-678" not in dumped
        assert "王小明" not in dumped


class TestDecodeAuditing:
    def test_decode_writes_decode_audit_and_audit_log(self, factory: Factory) -> None:
        user = factory.user(name="王測試")
        task = factory.care_task(user.id)
        staff = factory.account("site_staff", site_id=TEST_SITE)

        with session_scope(DbRole.IDN_APP) as s:
            decoded = decode_care_task(
                s,
                code=task.code,
                task_ref="T-1030-01",
                staff_account_id=staff.id,
                site_id=TEST_SITE,
            )
        assert decoded.name == "王測試"

        with session_scope(DbRole.IDN_APP) as s:
            trail = s.scalars(
                select(DecodeAudit).where(DecodeAudit.care_task_code == task.code)
            ).all()
        assert [(t.task_ref, t.decoded_by, t.site_id) for t in trail] == [
            ("T-1030-01", staff.id, TEST_SITE)
        ]
        rows = audit_rows(actor_id=str(staff.id), action="care_task.decode")
        assert [(r.actor_type, r.decision) for r in rows] == [("staff", "executed")]
        assert rows[0].reason is not None and "T-1030-01" in rows[0].reason

    @pytest.mark.parametrize(
        ("task_ref", "site", "reason_key"),
        [("", TEST_SITE, "task_ref_required"), ("T-1", "S-OTHER", "out_of_site")],
    )
    def test_rejected_decode_is_still_audited(
        self, factory: Factory, task_ref: str, site: str, reason_key: str
    ) -> None:
        user = factory.user()
        task = factory.care_task(user.id)
        staff = factory.account("site_staff", site_id=TEST_SITE)

        with pytest.raises(DecodeRejected) as exc, session_scope(DbRole.IDN_APP) as s:
            decode_care_task(
                s, code=task.code, task_ref=task_ref, staff_account_id=staff.id, site_id=site
            )
        assert exc.value.reason_key == reason_key

        rows = audit_rows(actor_id=str(staff.id), action="care_task.decode")
        assert [r.decision for r in rows] == ["rejected"]
        with session_scope(DbRole.IDN_APP) as s:
            assert (
                s.scalars(select(DecodeAudit).where(DecodeAudit.care_task_code == task.code)).all()
                == []
            )
