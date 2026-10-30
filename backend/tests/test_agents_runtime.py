"""Agent runtime（SPEC §8.1）：工具迴圈、身分注入、確認卡片、逐句過濾、Claude 串流迴圈。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.actions.state_machine import confirm
from app.agents import a1, runtime
from app.agents.runtime import (
    ConfirmationEvent,
    Delta,
    DoneEvent,
    StepResult,
    TextEvent,
    ToolCall,
    ToolEvent,
)
from app.db.engines import DbRole, session_scope
from app.db.models_idn import Booking, ChatMessage, PendingAction
from app.i18n import t
from app.llm import client as llm_client
from tests.conftest import Factory
from tests.fixtures import Person
from tests.helpers import audit_rows


def events_of(user_id: uuid.UUID, message: str) -> list[runtime.AgentEvent]:
    return list(a1.chat(user_id, message))


def text_of(events: list[runtime.AgentEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, TextEvent))


def done_of(events: list[runtime.AgentEvent]) -> DoneEvent:
    last = events[-1]
    assert isinstance(last, DoneEvent)
    return last


class ScriptedPlanner:
    """測試用 planner：依序吐出預先寫好的步驟。"""

    name = "scripted"

    def __init__(self, steps: list[list[Delta | StepResult]]) -> None:
        self.steps = steps
        self.seen: list[dict[str, Any]] = []

    def step(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[Any]
    ) -> Iterator[Delta | StepResult]:
        self.seen.append({"system": system, "messages": list(messages), "tools": tools})
        yield from self.steps.pop(0)


def run_scripted(user_id: uuid.UUID, planner: runtime.Planner) -> list[runtime.AgentEvent]:
    scope = a1.current_scope(user_id)
    rc = runtime.RunContext(
        agent=runtime.AGENTS["A1"],
        tool_context=lambda: a1.tool_context(user_id),
        context_text="測試",
        actor_id=f"A1:{user_id}",
        inject={"user_id": str(user_id), "consent_scope": scope},
    )
    return list(runtime.run(rc, history=[], user_message="測試", planner=planner))


def end(text: str = "") -> StepResult:
    return StepResult([{"type": "text", "text": text}], [], "end_turn")


def say(text: str) -> list[Delta | StepResult]:
    return [Delta(text), end(text)]


# --- mock A1 對話 ---


def test_status_question_answers_with_basis_and_offers_booking(
    wang: Person, open_sites: None
) -> None:
    events = events_of(wang.user_id, "我最近是不是變差了？")
    tools = [e.tool for e in events if isinstance(e, ToolEvent)]
    assert tools == ["daily_report", "risk_score", "site_booking"]
    reply = text_of(events)
    assert "依據：" in reply and "要哪一個" in reply
    done = done_of(events)
    assert done.planner == "mock" and done.meta and done.meta["proposal"]["kind"] == "booking"
    # 對話存檔（含方案 meta），稽核有 agent.run
    turns = a1.history(wang.user_id)
    assert [m.role for m in turns][-2:] == ["user", "assistant"]
    assert turns[-1].meta and turns[-1].meta["proposal"]["slots"]
    assert audit_rows(action="agent.run", actor_id=f"A1:{wang.user_id}")


def test_saying_yes_creates_pending_booking_only(wang: Person, open_sites: None) -> None:
    events_of(wang.user_id, "幫我預約量血壓")
    events = events_of(wang.user_id, "好，第二個")
    [card] = [e for e in events if isinstance(e, ConfirmationEvent)]
    assert card.tool == "site_booking" and "預約" in card.summary
    assert "確認" in text_of(events)
    with session_scope(DbRole.IDN_APP) as s:
        assert not s.scalars(select(Booking).where(Booking.user_id == wang.user_id)).all()
        action = confirm(s, card.action_id, user_id=wang.user_id)
        assert action.status == "executed"
        booking = s.scalars(select(Booking).where(Booking.user_id == wang.user_id)).one()
        assert booking.created_by == "a1"
        proposal = a1.history(wang.user_id)[-3].meta
        assert proposal
        assert booking.slot_at == datetime.fromisoformat(proposal["proposal"]["slots"][1])


def test_decline_does_nothing(wang: Person, open_sites: None) -> None:
    events_of(wang.user_id, "幫我預約")
    events = events_of(wang.user_id, "不用了")
    assert not [e for e in events if isinstance(e, ToolEvent)]
    assert text_of(events) == t("a1.chat.decline")


def test_revoke_via_chat_shows_only_the_fixed_confirmation(wang: Person) -> None:
    events = events_of(wang.user_id, "我要撤回健保的授權")
    [card] = [e for e in events if isinstance(e, ConfirmationEvent)]
    assert card.tool == "consent_mgmt"
    assert card.summary.startswith("確定要撤回嗎？撤回後您將失去：")
    assert text_of(events) == card.summary  # 不加任何勸留或其他文案
    with session_scope(DbRole.IDN_APP) as s:
        action = s.get_one(PendingAction, card.action_id)
        assert action.action_type == "consent_revoke" and action.status == "pending"


def test_points_and_exercise_intents(wang: Person) -> None:
    assert "點" in text_of(events_of(wang.user_id, "我有多少點數？"))
    reply = text_of(events_of(wang.user_id, "今天適合什麼運動"))
    assert "依據：" in reply and "注意" in reply


def test_unknown_message_gets_help(wang: Person) -> None:
    assert text_of(events_of(wang.user_id, "嗨")) == t("a1.chat.help")


# --- runtime 本身 ---


def test_schemas_hide_injected_fields() -> None:
    schemas = runtime.tool_schemas(runtime.AGENTS["A1"], {"user_id", "consent_scope"})
    assert {s["name"] for s in schemas} == set(runtime.AGENTS["A1"].allowed_tools)
    for schema in schemas:
        props = schema["input_schema"]["properties"]
        assert "user_id" not in props and "consent_scope" not in props and "confirmed" not in props
        assert "user_id" not in schema["input_schema"].get("required", [])


def test_runtime_overrides_identity_from_model(wang: Person, factory: Factory) -> None:
    other = factory.user()
    planner = ScriptedPlanner(
        [
            [
                StepResult(
                    [],
                    [
                        ToolCall(
                            "t1",
                            "risk_score",
                            {"user_id": str(other.id), "consent_scope": {"nhi": ["allergies"]}},
                        )
                    ],
                    "tool_use",
                )
            ],
            [end("好")],
        ]
    )
    events = run_scripted(wang.user_id, planner)
    assert [e.status for e in events if isinstance(e, ToolEvent)] == ["ok"]
    result = planner.seen[1]["messages"][-1]["content"][0]
    data = json.loads(result["content"])["data"]
    assert data["level"] in ("L2", "L3")  # 是王測試本人的資料，不是另一位使用者


def test_disallowed_tool_is_rejected_and_audited(wang: Person) -> None:
    planner = ScriptedPlanner(
        [
            [StepResult([], [ToolCall("t1", "sandbox_sql", {"sql": "SELECT 1"})], "tool_use")],
            [end()],
        ]
    )
    events = run_scripted(wang.user_id, planner)
    [event] = [e for e in events if isinstance(e, ToolEvent)]
    assert event.status == "rejected"
    assert planner.seen[1]["messages"][-1]["content"][0]["is_error"] is True
    assert audit_rows(tool="sandbox_sql", decision="rejected")


def test_bad_arguments_go_back_to_model_as_error(wang: Person) -> None:
    planner = ScriptedPlanner(
        [
            [StepResult([], [ToolCall("t1", "site_booking", {"mode": "book"})], "tool_use")],
            say("請再告訴我時段"),
        ]
    )
    events = run_scripted(wang.user_id, planner)
    [event] = [e for e in events if isinstance(e, ToolEvent)]
    assert event.status == "rejected" and event.detail
    assert text_of(events) == "請再告訴我時段"


def test_step_limit(wang: Person) -> None:
    loop = [StepResult([], [ToolCall(f"t{i}", "risk_score", {})], "tool_use") for i in range(10)]
    planner = ScriptedPlanner([[step] for step in loop])
    events = run_scripted(wang.user_id, planner)
    assert done_of(events).steps == runtime.MAX_STEPS
    assert text_of(events) == t("agent.too_many_steps")


def test_system_prompt_is_skill_md_with_cache_breakpoint(wang: Person) -> None:
    planner = ScriptedPlanner([[end("嗨")]])
    run_scripted(wang.user_id, planner)
    system = planner.seen[0]["system"]
    skill = runtime.skill_text("a1-personal-digital-twin")
    assert system[0]["text"] == skill and system[0]["cache_control"] == {"type": "ephemeral"}
    assert "# A1 個人數位雙生 Agent" in skill
    assert system[1]["text"] == "測試"


# --- ClaudePlanner（以假的串流物件測迴圈，不呼叫真正的 API） ---


class FakeStream:
    def __init__(self, texts: list[str], final: Any) -> None:
        self.texts = texts
        self.final = final

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[Any]:
        for text in self.texts:
            yield SimpleNamespace(type="text", text=text)

    def get_final_message(self) -> Any:
        return self.final


def test_claude_planner_loop(wang: Person, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="risk_score", input={})
    script = [
        FakeStream(["我查一下", "。"], SimpleNamespace(content=[tool_use], stop_reason="tool_use")),
        FakeStream(
            ["血壓連續偏高，建議複測。", "您患有高", "血壓。"],
            SimpleNamespace(
                content=[SimpleNamespace(type="text", text="…")], stop_reason="end_turn"
            ),
        ),
    ]

    def fake_stream(**kwargs: Any) -> FakeStream:
        calls.append({**kwargs, "messages": list(kwargs["messages"])})  # SDK 呼叫當下即序列化
        return script.pop(0)

    monkeypatch.setattr(llm_client, "stream", fake_stream)
    events = run_scripted(wang.user_id, runtime.ClaudePlanner(runtime.AGENTS["A1"]))
    reply = text_of(events)
    assert reply.startswith("我查一下。血壓連續偏高，建議複測。")
    assert "高血壓" not in reply and t("output_filter.fallback") in reply  # C-7 逐句過濾
    assert done_of(events).violations
    assert all(tool.get("eager_input_streaming") for tool in calls[0]["tools"])
    # 第二次呼叫帶回工具結果，且 assistant 回合原樣回填
    second = calls[1]["messages"]
    assert second[-2]["content"] == [tool_use]
    assert second[-1]["content"][0]["tool_use_id"] == "tu_1"


def test_claude_planner_does_not_run_truncated_tools(
    wang: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="risk_score", input={})
    stream = FakeStream([], SimpleNamespace(content=[tool_use], stop_reason="max_tokens"))
    monkeypatch.setattr(llm_client, "stream", lambda **_: stream)
    events = run_scripted(wang.user_id, runtime.ClaudePlanner(runtime.AGENTS["A1"]))
    assert not [e for e in events if isinstance(e, ToolEvent)]


def test_history_alternates_roles(wang: Person) -> None:
    rows = [
        ChatMessage(user_id=wang.user_id, role="assistant", content="孤兒回覆"),
        ChatMessage(user_id=wang.user_id, role="user", content="一"),
        ChatMessage(user_id=wang.user_id, role="user", content="二"),
        ChatMessage(user_id=wang.user_id, role="assistant", content="好"),
        ChatMessage(user_id=wang.user_id, role="user", content="三"),
    ]
    assert a1.alternate(rows) == [
        {"role": "user", "content": "一\n二"},
        {"role": "assistant", "content": "好"},
        {"role": "user", "content": "三"},
        {"role": "assistant", "content": "…"},
    ]
