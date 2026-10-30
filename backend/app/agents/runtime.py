"""Agent runtime（SPEC §8.1）：自建、可稽核的工具迴圈，不依賴 Agent 框架。

    messages ← system(SKILL.md) + context + history
    resp ← planner.step(tools = registry.schemas(agent.allowed_tools))
    for tool_use in resp: registry.call(...)   # C-2/C-3 檢查、C-5 待確認、C-6 稽核都在 registry
    until 沒有工具呼叫
    output ← StreamingFilter（A1，C-7，逐句）

- planner 有兩種：ClaudePlanner（有 ANTHROPIC_API_KEY）與 mock 規則式 planner（無金鑰，[MOCK]）。
  兩者走同一條 runtime，工具呼叫、確認、稽核、過濾完全相同。
- 身分與授權範圍由 runtime 注入（`inject`），覆寫模型給的值：模型無法改 user_id 或擴大授權。
- 送往 LLM 的內容不含姓名、電話、地址、身分證（工具輸出本來就不含）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from app.agents.output_filter import StreamingFilter
from app.audit.writer import write_audit
from app.config import get_settings
from app.i18n import t
from app.llm import client as llm
from app.tools import AGENCY_TOOLS, CITIZEN_TOOLS
from app.tools.registry import ToolContext, ToolRejected, registry

log = logging.getLogger(__name__)

MAX_STEPS = 6


@dataclass(frozen=True)
class AgentSpec:
    name: str
    skill_dir: str
    allowed_tools: tuple[str, ...]
    filter_output: bool = False
    max_tokens: int = 8000


AGENTS: Mapping[str, AgentSpec] = MappingProxyType(
    {
        "A1": AgentSpec("A1", "a1-personal-digital-twin", CITIZEN_TOOLS, filter_output=True),
        "A2": AgentSpec(
            "A2",
            "a2-public-health-orchestrator",
            (
                "cohort_analysis",
                "care_rules",
                "resource_sim",
                "briefing_pack",
                "delegate_a3",
                "delegate_a4",
            ),
            max_tokens=16000,
        ),
        "A3": AgentSpec(
            "A3",
            "a3-data-analysis",
            ("sandbox_sql", "sandbox_python", "chart_render", "report_build", "schema_catalog"),
            max_tokens=16000,
        ),
        "A4": AgentSpec(
            "A4",
            "a4-community-sentiment",
            tuple(
                n
                for n in AGENCY_TOOLS
                if n.startswith("nlp_")
                or n in ("crawl_schedule", "trend_detect", "alert_emit", "sentiment_search")
            ),
        ),
    }
)


@lru_cache
def skill_text(skill_dir: str) -> str:
    """SKILL.md 原文就是 system prompt（CLAUDE.md：不複製內容到程式碼）。"""
    path: Path = get_settings().skills_dir / skill_dir / "SKILL.md"
    return path.read_text(encoding="utf-8")


# --- 事件（runtime → 呼叫端，例如 SSE） ---


@dataclass
class TextEvent:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolEvent:
    tool: str
    status: Literal["ok", "confirmation_required", "rejected", "error"]
    detail: str | None = None
    type: Literal["tool"] = "tool"


@dataclass
class ConfirmationEvent:
    action_id: uuid.UUID
    tool: str
    summary: str
    expires_at: str
    type: Literal["confirmation"] = "confirmation"


@dataclass
class DoneEvent:
    text: str
    planner: str
    steps: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    meta: dict[str, Any] | None = None
    type: Literal["done"] = "done"


AgentEvent = TextEvent | ToolEvent | ConfirmationEvent | DoneEvent


# --- planner ---


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Delta:
    text: str


@dataclass
class StepResult:
    content: list[Any]  # 原樣回填 assistant 回合（含 thinking／fallback 區塊）
    tool_calls: list[ToolCall]
    stop_reason: str
    meta: dict[str, Any] | None = None


class Planner(Protocol):
    name: str

    def step(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[Any]
    ) -> Iterator[Delta | StepResult]: ...


class ClaudePlanner:
    """Claude Messages API（串流）。不設取樣參數、adaptive thinking、不強制 tool_choice。"""

    name = "claude"
    JSON_RETRIES = 2

    def __init__(self, agent: AgentSpec) -> None:
        self.agent = agent

    def step(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[Any]
    ) -> Iterator[Delta | StepResult]:
        api_tools = [{**schema, "eager_input_streaming": True} for schema in tools]
        retries = 0
        while True:
            try:
                with llm.stream(
                    agent=self.agent.name,
                    system=system,
                    messages=messages,
                    tools=api_tools,
                    max_tokens=self.agent.max_tokens,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield Delta(event.text)
                    final = stream.get_final_message()
                break
            except ValueError:
                # 工具輸入的 JSON 完全無法解析（尚無 tool_use_id 可回覆）：重送這一回合
                retries += 1
                if retries > self.JSON_RETRIES:
                    raise
        uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason in ("refusal", "max_tokens") or not uses:
            # 拒答或輸出被截斷時不執行任何工具（截斷的工具輸入可能是不完整的參數）
            yield StepResult(final.content, [], final.stop_reason or "end_turn")
            return
        calls = [ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {}) for b in uses]
        yield StepResult(final.content, calls, final.stop_reason or "tool_use")


# --- runtime ---


@dataclass
class RunContext:
    agent: AgentSpec
    tool_context: Callable[[], AbstractContextManager[ToolContext]]  # 每次工具呼叫一個交易
    context_text: str  # 執行期情境（日期、授權範圍、雙生快照…），接在 SKILL.md 之後
    actor_id: str
    inject: dict[str, Any] = field(default_factory=dict)


def tool_schemas(agent: AgentSpec, hidden: set[str]) -> list[dict[str, Any]]:
    """給模型的工具定義；runtime 會注入的欄位（user_id、consent_scope）不讓模型填。"""
    out = []
    for schema in registry.schemas(agent.allowed_tools):
        params = dict(schema["input_schema"])
        params["properties"] = {
            k: v for k, v in params.get("properties", {}).items() if k not in hidden
        }
        if "required" in params:
            params["required"] = [r for r in params["required"] if r not in hidden]
        params.pop("title", None)
        out.append({**schema, "input_schema": params})
    return out


def system_blocks(rc: RunContext) -> list[dict[str, Any]]:
    return [
        # SKILL.md＋工具定義固定不變，放在快取斷點前
        {
            "type": "text",
            "text": skill_text(rc.agent.skill_dir),
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": rc.context_text},
    ]


def _validation_message(exc: ValidationError) -> str:
    parts = [f"{'.'.join(map(str, e['loc'])) or '參數'}：{e['msg']}" for e in exc.errors()[:3]]
    return t("agent.error.bad_args", detail="；".join(parts))


Executed = tuple[dict[str, Any], ToolEvent, ConfirmationEvent | None]


def _execute(rc: RunContext, call: ToolCall) -> Executed:
    """執行一個工具呼叫，回傳 (tool_result 區塊, 工具事件, 待確認事件)。"""
    args = {**call.input, **rc.inject}

    def error(status: Literal["rejected", "error"], message: str) -> Executed:
        block = {"type": "tool_result", "tool_use_id": call.id, "is_error": True}
        return {**block, "content": message}, ToolEvent(call.name, status, message), None

    try:
        with rc.tool_context() as ctx:
            result = registry.call(call.name, args, ctx)
    except ToolRejected as exc:
        return error("rejected", str(exc))
    except ValidationError as exc:
        return error("rejected", _validation_message(exc))
    except (PermissionError, LookupError, ValueError) as exc:
        return error("rejected", str(exc) or type(exc).__name__)
    except Exception as exc:
        # 領域錯誤（預約額滿、點數不足…）的訊息已在地化；其他未預期錯誤只回通用訊息
        if getattr(exc, "key", None):
            return error("rejected", str(exc))
        log.exception("[%s] 工具 %s 執行失敗", rc.agent.name, call.name)
        return error("error", t("agent.error.tool_failed"))

    pending: ConfirmationEvent | None = None
    if result.status == "confirmation_required":
        assert result.action_id is not None and result.data is not None
        content = {
            "status": "confirmation_required",
            "message": result.message,
            "summary": result.data["summary"],
        }
        pending = ConfirmationEvent(
            result.action_id, call.name, result.data["summary"], result.data["expires_at"]
        )
    else:
        content = {"status": "ok", "data": result.data}
    block = {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": json.dumps(content, ensure_ascii=False, default=str),
    }
    return block, ToolEvent(call.name, result.status), pending


def run(
    rc: RunContext,
    *,
    history: list[dict[str, Any]],
    user_message: str,
    planner: Planner,
) -> Iterator[AgentEvent]:
    """跑一次 Agent（對話一輪）。逐步產生事件；最後一個一定是 DoneEvent。"""
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": user_message}]
    system = system_blocks(rc)
    tools = tool_schemas(rc.agent, set(rc.inject))
    gate = StreamingFilter() if rc.agent.filter_output else None
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    meta: dict[str, Any] | None = None
    steps = 0

    def emit(pieces: list[str]) -> Iterator[TextEvent]:
        for piece in pieces:
            texts.append(piece)
            yield TextEvent(piece)

    while steps < MAX_STEPS:
        steps += 1
        result: StepResult | None = None
        for item in planner.step(system=system, messages=messages, tools=tools):
            if isinstance(item, Delta):
                yield from emit(gate.feed(item.text) if gate else [item.text])
            else:
                result = item
        if gate:
            yield from emit(gate.flush())
        assert result is not None, "planner 必須回傳 StepResult"
        meta = result.meta or meta
        messages.append({"role": "assistant", "content": result.content})
        if not result.tool_calls:
            if result.stop_reason == "refusal" and not texts:
                yield from emit([t("agent.refusal")])
            break
        tool_results = []
        for call in result.tool_calls:
            block, event, pending = _execute(rc, call)
            tool_results.append(block)
            calls.append({"tool": call.name, "status": event.status})
            yield event
            if pending:
                yield pending
        messages.append({"role": "user", "content": tool_results})
    else:
        yield from emit([t("agent.too_many_steps")])

    text = "".join(texts)
    violations = gate.violations if gate else []
    write_audit(
        None,
        actor_type="agent",
        actor_id=rc.actor_id,
        action="agent.run",
        decision="executed",
        consent_scope=rc.inject.get("consent_scope"),
        input={"message": user_message},
        output={"text": text},
        reason=f"planner={planner.name}; steps={steps}; tools={len(calls)}; "
        f"filtered={len(violations)}",
    )
    yield DoneEvent(text, planner.name, steps, calls, violations, meta)


def pick_planner(agent: AgentSpec, mock: Callable[[], Planner]) -> Planner:
    """有金鑰用 Claude，否則用規則式 mock（日誌標 [MOCK]）。"""
    return ClaudePlanner(agent) if llm.llm_available() else mock()
