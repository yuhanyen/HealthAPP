"""Claude API 存取（SPEC §3：Anthropic Messages + tool use）。

- 模型由 .env 的 CLAUDE_MODEL 指定（預設 claude-opus-5-5）。
- 此模型不接受 temperature 等取樣參數（SPEC §13-9 的溫度改以 effort 控制，見 DECISIONS）；
  思考一律 adaptive（省略 thinking 參數），不強制 tool_choice。
- 預設開啟伺服器端拒答備援（server-side fallback），被安全分類器拒答時改由備援模型接手。
- 公司網路有 TLS 攔截：以 truststore 改用作業系統憑證庫。
- 沒有 ANTHROPIC_API_KEY 時 llm_available() 為 False，呼叫端改走規則式（[MOCK]）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from app.adapters.base import mock_notice
from app.config import get_settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORT = {"A1": "low", "A2": "medium", "A3": "medium", "A4": "low", "nlp": "low"}


def llm_available() -> bool:
    available = bool(get_settings().anthropic_api_key)
    if not available:
        mock_notice("LLM", "未設定 ANTHROPIC_API_KEY，Agent 與 NLP 改用規則式")
    return available


@lru_cache
def get_client() -> Any:
    import truststore

    truststore.inject_into_ssl()
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, max_retries=2)


def model() -> str:
    return get_settings().claude_model


System = str | list[dict[str, Any]]


def _kwargs(
    *,
    agent: str,
    system: System,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    output_format: dict[str, Any] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "output_config": {
            "effort": EFFORT.get(agent, "medium"),
            **({"format": output_format} if output_format else {}),
        },
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }
    if tools:
        kwargs["tools"] = tools
    return kwargs


def create(
    *,
    agent: str,
    system: System,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 16000,
    output_format: dict[str, Any] | None = None,
) -> Any:
    """一次 Messages 呼叫（非串流）。回傳 SDK 的 Message 物件。"""
    return get_client().beta.messages.create(
        **_kwargs(
            agent=agent,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            output_format=output_format,
        )
    )


def stream(
    *,
    agent: str,
    system: System,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 16000,
) -> Any:
    """串流呼叫（Agent 對話用）。

    回傳 SDK 的 stream context manager：迭代得到 text 事件，結束後 get_final_message()。
    """
    return get_client().beta.messages.stream(
        **_kwargs(
            agent=agent,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            output_format=None,
        )
    )


def text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text")


NLP_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentiment": {
                            "type": "string",
                            "enum": ["negative", "neutral", "positive"],
                        },
                        "intensity": {"type": "number"},
                        "topic": {
                            "type": "string",
                            "enum": [
                                "排隊等候",
                                "據點開放",
                                "環境設施",
                                "服務態度",
                                "健康活動",
                                "其他",
                            ],
                        },
                        "entities": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["sentiment", "intensity", "topic", "entities"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}


def nlp_batch(texts: list[str], gazetteer: Sequence[str]) -> list[dict[str, Any]]:
    """一次分類多則短文（情緒、主題、地名實體）。輸入已去識別。"""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    message = create(
        agent="nlp",
        system=(
            "你是社區輿情分類器。對每則短文給出情緒（negative/neutral/positive）、"
            "強度 0–1、主題（從列舉中選一）、文中出現的地名或據點名（只能從清單挑選）。"
            "依輸入順序輸出，數量必須相同。"
        ),
        messages=[{"role": "user", "content": f"地名清單：{'、'.join(gazetteer)}\n\n{numbered}"}],
        max_tokens=4000,
        output_format=NLP_SCHEMA,
    )
    if message.stop_reason == "refusal":
        raise RuntimeError("LLM 拒答")
    items: list[dict[str, Any]] = json.loads(text_of(message))["items"]
    if len(items) != len(texts):
        raise RuntimeError("LLM 回傳筆數不符")
    return items
