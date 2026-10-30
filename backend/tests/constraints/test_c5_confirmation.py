"""C-5：高風險動作（預約、分享、兌換、撤回授權；M8 起加上活動報名）需本人明確確認後才執行。

確認狀態機：pending →（本人確認）confirmed → executed；逾 24h → expired；可 cancelled。
Agent 自己帶 confirmed=true 不算數——只有本人透過 /me/actions/{id}/confirm 才會執行。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.actions.state_machine import (
    HIGH_RISK_ACTIONS,
    ActionExpired,
    InvalidActionState,
    NotActionOwner,
    cancel,
    confirm,
)
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_idn import Consent, PendingAction
from app.tools.registry import CitizenToolArgs, ToolContext, ToolRegistry, registry
from tests.conftest import Factory
from tests.helpers import auth

pytestmark = pytest.mark.constraint


class ProbeArgs(CitizenToolArgs):
    detail: str = "明早 9:00 測試據點"


@pytest.fixture
def probe() -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    executed: list[str] = []

    for action_type in sorted(HIGH_RISK_ACTIONS):

        def _make(kind: str) -> None:
            @reg.tool(
                name=f"probe_{kind}",
                schema=ProbeArgs,
                side="citizen",
                high_risk=lambda args: True,
                action_type=kind,
            )
            def _tool(ctx: ToolContext, args: ProbeArgs) -> dict[str, Any]:
                assert args.confirmed is True, "高風險工具只在確認後被呼叫"
                executed.append(kind)
                return {"done": kind}

        _make(action_type)
    return reg, executed


def _request(reg: ToolRegistry, factory: Factory, kind: str, **extra: Any) -> tuple[Any, Any]:
    user = factory.user()
    factory.consent(user.id, "site", ["bp"])
    with user_scoped_session(user.id) as s:
        result = reg.call(
            f"probe_{kind}",
            {"user_id": str(user.id), "consent_scope": {"site": ["bp"]}, **extra},
            ToolContext(agent="A1", db=s, principal_user_id=user.id),
        )
    return user, result


def test_high_risk_set_matches_spec() -> None:
    # SPEC §2.2 的四類，加上 §17.3 的活動報名（報名會把姓名交給主辦單位，等同分享）
    assert set(HIGH_RISK_ACTIONS) == {
        "booking",
        "share",
        "redeem",
        "consent_revoke",
        "event_register",
    }


@pytest.mark.parametrize("kind", sorted(HIGH_RISK_ACTIONS))
def test_unconfirmed_high_risk_call_only_creates_pending_action(
    factory: Factory, probe: tuple[ToolRegistry, list[str]], kind: str
) -> None:
    reg, executed = probe
    user, result = _request(reg, factory, kind)

    assert result.status == "confirmation_required"
    assert result.action_id is not None
    assert executed == []
    with session_scope(DbRole.IDN_APP) as s:
        action = s.get(PendingAction, result.action_id)
        assert action is not None
        assert (action.status, action.action_type, action.user_id) == ("pending", kind, user.id)
        assert action.expires_at - action.created_at == timedelta(hours=24)


def test_agent_cannot_self_confirm(factory: Factory, probe: tuple[ToolRegistry, list[str]]) -> None:
    reg, executed = probe
    _, first = _request(reg, factory, "booking")
    _, result = _request(reg, factory, "booking", confirmed=True, action_id=str(first.action_id))
    assert result.status == "confirmation_required"
    assert executed == []


@pytest.mark.parametrize("kind", sorted(HIGH_RISK_ACTIONS))
def test_owner_confirmation_executes_exactly_once(
    factory: Factory, probe: tuple[ToolRegistry, list[str]], kind: str
) -> None:
    reg, executed = probe
    user, result = _request(reg, factory, kind)

    with session_scope(DbRole.IDN_APP) as s:
        action = confirm(s, result.action_id, user_id=user.id, registry=reg)
        assert action.status == "executed"
        assert action.result == {"done": kind}
    assert executed == [kind]

    with session_scope(DbRole.IDN_APP) as s, pytest.raises(InvalidActionState):
        confirm(s, result.action_id, user_id=user.id, registry=reg)
    assert executed == [kind]


def test_confirmation_expires_after_24_hours(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, executed = probe
    user, result = _request(reg, factory, "redeem")
    with session_scope(DbRole.IDN_APP) as s:
        created_at = s.get_one(PendingAction, result.action_id).created_at

    with session_scope(DbRole.IDN_APP) as s, pytest.raises(ActionExpired):
        confirm(
            s,
            result.action_id,
            user_id=user.id,
            registry=reg,
            now=created_at + timedelta(hours=24, seconds=1),
        )

    with session_scope(DbRole.IDN_APP) as s:
        assert s.get_one(PendingAction, result.action_id).status == "expired"
    assert executed == []


def test_only_the_owner_can_confirm(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, executed = probe
    _, result = _request(reg, factory, "share")
    stranger = factory.user()
    with session_scope(DbRole.IDN_APP) as s, pytest.raises(NotActionOwner):
        confirm(s, result.action_id, user_id=stranger.id, registry=reg)
    assert executed == []


def test_cancelled_action_cannot_be_confirmed(
    factory: Factory, probe: tuple[ToolRegistry, list[str]]
) -> None:
    reg, executed = probe
    user, result = _request(reg, factory, "booking")
    with session_scope(DbRole.IDN_APP) as s:
        cancel(s, result.action_id, user_id=user.id)
    with session_scope(DbRole.IDN_APP) as s, pytest.raises(InvalidActionState):
        confirm(s, result.action_id, user_id=user.id, registry=reg)
    assert executed == []


def test_consent_revoke_tool_requires_confirmation(factory: Factory) -> None:
    user = factory.user()
    consent = factory.consent(user.id, "nhi", ["visits"])
    with user_scoped_session(user.id) as s:
        result = registry.call(
            "consent_mgmt",
            {
                "user_id": str(user.id),
                "consent_scope": {"nhi": ["visits"]},
                "action": "revoke",
                "source": "nhi",
                "confirmed": True,
            },
            ToolContext(agent="A1", db=s, principal_user_id=user.id),
        )
    assert result.status == "confirmation_required"
    with session_scope(DbRole.IDN_APP) as s:
        assert s.get_one(Consent, consent.id).revoked_at is None


def test_consent_revoke_api_requires_confirmation(client: TestClient, factory: Factory) -> None:
    user, _, token = factory.citizen()
    consent = factory.consent(user.id, "wearable", ["steps"])

    pending = client.delete(f"/api/v1/consents/{consent.id}", headers=auth(token))
    assert pending.status_code == 202
    body = pending.json()
    assert body["status"] == "confirmation_required"
    with session_scope(DbRole.IDN_APP) as s:
        assert s.get_one(Consent, consent.id).revoked_at is None

    done = client.post(f"/api/v1/me/actions/{body['action_id']}/confirm", headers=auth(token))
    assert done.status_code == 200
    assert done.json()["status"] == "executed"
    with session_scope(DbRole.IDN_APP) as s:
        assert s.get_one(Consent, consent.id).revoked_at is not None
        remaining = s.scalars(
            select(PendingAction).where(
                PendingAction.user_id == user.id, PendingAction.status == "pending"
            )
        ).all()
        assert remaining == []


def test_a1_chat_never_executes_high_risk_without_owner(factory: Factory) -> None:
    """A1 對話中模型自稱 confirmed=true 也沒用：只會產生待確認動作與確認卡片。"""
    from app.agents import a1, runtime

    user = factory.user()
    factory.consent(user.id, "wearable", ["steps"])

    class Pusher:
        name = "pusher"

        def __init__(self) -> None:
            self.done = False

        def step(self, **kwargs: Any) -> Any:
            if self.done:
                yield runtime.StepResult([], [], "end_turn")
                return
            self.done = True
            args = {"action": "revoke", "source": "wearable", "confirmed": True, "action_id": "x"}
            yield runtime.StepResult([], [runtime.ToolCall("t1", "consent_mgmt", args)], "")

    rc = runtime.RunContext(
        agent=runtime.AGENTS["A1"],
        tool_context=lambda: a1.tool_context(user.id),
        context_text="",
        actor_id=f"A1:{user.id}",
        inject={"user_id": str(user.id), "consent_scope": a1.current_scope(user.id)},
    )
    events = list(runtime.run(rc, history=[], user_message="撤回", planner=Pusher()))
    [card] = [e for e in events if isinstance(e, runtime.ConfirmationEvent)]
    with session_scope(DbRole.IDN_APP) as s:
        assert s.get_one(PendingAction, card.action_id).status == "pending"
        consent = s.scalars(select(Consent).where(Consent.user_id == user.id)).one()
        assert consent.revoked_at is None
