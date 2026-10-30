"""Agent 對話的 SSE 串流（民眾 A1、機關 A2 共用）：event: text／tool／confirmation／done／error。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import asdict
from typing import Any

from fastapi.responses import StreamingResponse

from app.agents.runtime import AgentEvent
from app.i18n import t

log = logging.getLogger(__name__)


def sse(kind: str, data: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def payload(event: AgentEvent) -> dict[str, Any]:
    data = asdict(event)
    data.pop("type", None)
    return data


def stream_agent(run: Callable[[], Iterator[AgentEvent]]) -> StreamingResponse:
    def body() -> Iterator[str]:
        try:
            for event in run():
                yield sse(event.type, payload(event))
        except Exception:
            log.exception("Agent 對話失敗")
            yield sse("error", {"message": t("agent.unavailable")})

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
