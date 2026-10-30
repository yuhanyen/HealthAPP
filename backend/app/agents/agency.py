"""機關側 Agent（A2／A3／A4）共用：連線一律用該 Agent 綁定的 DB 帳號（C-2：anon_reader），
工具一律經 registry（C-6 稽核、allowed_tools 檢查）。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from app.agents.accounts import AGENT_DB_ROLE
from app.agents.runtime import AGENTS
from app.db.engines import session_scope
from app.tools.registry import ToolContext, registry

VILLAGE_CODE = re.compile(r"^[A-Za-z0-9]{1,12}$")


@contextmanager
def tool_context(agent: str, actor_id: str) -> Iterator[ToolContext]:
    with session_scope(AGENT_DB_ROLE[agent]) as s:
        yield ToolContext(
            agent=agent,
            db=s,
            allowed_tools=frozenset(AGENTS[agent].allowed_tools),
            actor_id=actor_id,
        )


def context_factory(agent: str, actor_id: str) -> Callable[[], AbstractContextManager[ToolContext]]:
    return lambda: tool_context(agent, actor_id)


def call(agent: str, actor_id: str, tool: str, **args: Any) -> dict[str, Any]:
    with tool_context(agent, actor_id) as ctx:
        return registry.call(tool, args, ctx).data or {}


def clean_villages(codes: list[str] | None) -> list[str]:
    """委派規格裡的里代碼只接受英數字（會被放進分析 SQL 的字面值）。"""
    return sorted({c for c in codes or [] if VILLAGE_CODE.match(c)})


def last_json(text: str) -> dict[str, Any] | None:
    """從模型的最後回覆取出 JSON（```json 區塊或最後一個 {...}）。"""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    candidates = blocks or re.findall(r"(\{.*\})", text, re.S)
    for raw in reversed(candidates):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
