"""nlp_sentiment（SPEC §7.2）：輿情情緒分類。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.services import nlp
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class NlpArgs(AgencyToolArgs):
    texts: list[str] = Field(min_length=1, max_length=100)


@registry.tool(name="nlp_sentiment", schema=NlpArgs, side="agency")
def nlp_sentiment(ctx: ToolContext, args: NlpArgs) -> dict[str, Any]:
    """逐則判斷情緒（negative／neutral／positive）與強度 0–1。輸入須為已去識別的文字。"""
    return {
        "labels": [
            {"sentiment": r["sentiment"], "intensity": r["intensity"]}
            for r in nlp.batch(args.texts)
        ]
    }
