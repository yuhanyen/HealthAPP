"""nlp_topic（SPEC §7.2）：輿情主題分類。"""

from __future__ import annotations

from typing import Any

from app.services import nlp
from app.tools.nlp_sentiment import NlpArgs
from app.tools.registry import ToolContext, registry


@registry.tool(name="nlp_topic", schema=NlpArgs, side="agency")
def nlp_topic(ctx: ToolContext, args: NlpArgs) -> dict[str, Any]:
    """逐則判斷主題：排隊等候、據點開放、環境設施、服務態度、健康活動、其他。"""
    return {"labels": [r["topic"] for r in nlp.batch(args.texts)]}
