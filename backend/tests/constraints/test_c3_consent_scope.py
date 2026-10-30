"""C-3：民眾側 Agent（A1）每次工具呼叫附 consent_scope，工具拒絕範圍外請求。

防線：
1. 工具層 registry：沒附 scope、scope 不含所需項目、scope 超出本人實際授權（偽造）、
   存取他人資料 → 一律拒絕且不執行工具。
2. DB 層：A1 的 idn_user_scoped 連線受 RLS 限制，只看得到本人的列。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.consent import service as consent_service
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_idn import Consent, FhirResource, User
from app.tools.registry import (
    CitizenToolArgs,
    ConsentScopeError,
    ToolContext,
    ToolRegistry,
    registry,
)
from tests.conftest import Factory

pytestmark = pytest.mark.constraint


class ProbeArgs(CitizenToolArgs):
    pass


@pytest.fixture
def probe() -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    calls: list[str] = []

    @reg.tool(
        name="probe_steps",
        schema=ProbeArgs,
        side="citizen",
        required_scope=lambda args: {"wearable": ["steps"]},
    )
    def _probe(ctx: ToolContext, args: ProbeArgs) -> dict[str, Any]:
        calls.append(str(args.user_id))
        return {"steps_today": 4200}

    return reg, calls


def _call(
    reg: ToolRegistry, user_id: uuid.UUID, args: dict[str, Any], *, as_user: uuid.UUID | None = None
) -> Any:
    principal = as_user or user_id
    with user_scoped_session(principal) as s:
        return reg.call(
            "probe_steps", args, ToolContext(agent="A1", db=s, principal_user_id=principal)
        )


def test_in_scope_call_succeeds(factory: Factory, probe: tuple[ToolRegistry, list[str]]) -> None:
    reg, calls = probe
    user = factory.user()
    factory.consent(user.id, "wearable", ["steps", "heart_rate"])

    result = _call(
        reg, user.id, {"user_id": str(user.id), "consent_scope": {"wearable": ["steps"]}}
    )

    assert result.status == "ok"
    assert result.data == {"steps_today": 4200}
    assert calls == [str(user.id)]


def test_missing_consent_scope_is_rejected(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, calls = probe
    user = factory.user()
    factory.consent(user.id, "wearable", ["steps"])

    with pytest.raises(ConsentScopeError) as exc:
        _call(reg, user.id, {"user_id": str(user.id)})
    assert exc.value.reason_key == "scope_missing"
    assert calls == []


def test_scope_without_required_item_is_rejected(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, calls = probe
    user = factory.user()
    factory.consent(user.id, "wearable", ["steps"])
    factory.consent(user.id, "site", ["bp"])

    with pytest.raises(ConsentScopeError) as exc:
        _call(reg, user.id, {"user_id": str(user.id), "consent_scope": {"site": ["bp"]}})
    assert exc.value.reason_key == "scope_rejected"
    assert calls == []


def test_forged_scope_beyond_real_consent_is_rejected(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, calls = probe
    user = factory.user()
    factory.consent(user.id, "site", ["bp"])  # 沒有授權 wearable

    with pytest.raises(ConsentScopeError) as exc:
        _call(reg, user.id, {"user_id": str(user.id), "consent_scope": {"wearable": ["steps"]}})
    assert exc.value.reason_key == "scope_forged"
    assert calls == []


def test_revoked_consent_immediately_falls_out_of_scope(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, calls = probe
    user = factory.user()
    consent = factory.consent(user.id, "wearable", ["steps"])
    with session_scope(DbRole.IDN_APP) as s:
        consent_service.revoke(
            s, user_id=user.id, consent_id=consent.id, actor_type="user", actor_id=str(user.id)
        )

    with pytest.raises(ConsentScopeError):
        _call(reg, user.id, {"user_id": str(user.id), "consent_scope": {"wearable": ["steps"]}})
    assert calls == []


def test_access_to_another_users_data_is_rejected(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, calls = probe
    owner, intruder = factory.user(), factory.user()
    factory.consent(owner.id, "wearable", ["steps"])
    factory.consent(intruder.id, "wearable", ["steps"])

    with pytest.raises(ConsentScopeError) as exc:
        _call(
            reg,
            owner.id,
            {"user_id": str(owner.id), "consent_scope": {"wearable": ["steps"]}},
            as_user=intruder.id,
        )
    assert exc.value.reason_key == "wrong_user"
    assert calls == []


def test_builtin_consent_mgmt_tool_also_requires_scope(factory: Factory) -> None:
    user = factory.user()
    with user_scoped_session(user.id) as s, pytest.raises(ConsentScopeError):
        registry.call(
            "consent_mgmt",
            {"user_id": str(user.id), "action": "get"},
            ToolContext(agent="A1", db=s, principal_user_id=user.id),
        )


def test_rls_user_scoped_connection_only_sees_own_rows(factory: Factory) -> None:
    me, other = factory.user(), factory.user()
    for uid in (me.id, other.id):
        factory.fhir(uid, "wearable")
        factory.consent(uid, "wearable", ["steps"])

    with user_scoped_session(me.id) as s:
        assert set(s.scalars(select(User.id))) == {me.id}
        assert set(s.scalars(select(FhirResource.user_id))) == {me.id}
        assert set(s.scalars(select(Consent.user_id))) == {me.id}


def test_rls_without_user_binding_sees_nothing(factory: Factory) -> None:
    factory.user()
    with session_scope(DbRole.IDN_USER_SCOPED) as s:
        assert list(s.scalars(select(User.id))) == []


def test_a1_runtime_injects_identity_and_live_scope(factory: Factory) -> None:
    """A1 runtime：模型給的 user_id／consent_scope 一律被覆寫成本人與目前實際授權。"""
    from app.agents import a1, runtime

    me, other = factory.user(), factory.user()
    factory.consent(me.id, "wearable", ["steps"])
    factory.consent(other.id, "wearable", ["steps", "heart_rate"])
    seen: list[dict[str, Any]] = []

    class Forger:
        name = "forger"

        def step(self, **kwargs: Any) -> Any:
            if not seen:
                seen.append({})
                forged = {"user_id": str(other.id), "consent_scope": {"nhi": ["visits"]}}
                yield runtime.StepResult(
                    [], [runtime.ToolCall("t1", "consent_mgmt", {"action": "get", **forged})], ""
                )
            else:
                seen.append(kwargs["messages"][-1]["content"][0])
                yield runtime.StepResult([], [], "end_turn")

    rc = runtime.RunContext(
        agent=runtime.AGENTS["A1"],
        tool_context=lambda: a1.tool_context(me.id),
        context_text="",
        actor_id=f"A1:{me.id}",
        inject={"user_id": str(me.id), "consent_scope": a1.current_scope(me.id)},
    )
    list(runtime.run(rc, history=[], user_message="我的授權？", planner=Forger()))
    assert '"wearable": ["steps"]' in seen[1]["content"]  # 回來的是本人的授權
    assert "heart_rate" not in seen[1]["content"]
    # 工具定義裡根本沒有這兩個欄位可填
    for schema in runtime.tool_schemas(runtime.AGENTS["A1"], set(rc.inject)):
        assert not {"user_id", "consent_scope"} & set(schema["input_schema"]["properties"])
