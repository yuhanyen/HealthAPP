"""briefing_pack（SPEC §7.2）：談參（事實與立場分段；每個事實附依據；標示需承辦核定）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools._common import AgencyToolArgs, today
from app.tools.registry import ToolContext, registry


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    evidence: str = Field(description="查詢編號（Q-xxxx）或來源網址")


class BriefingArgs(AgencyToolArgs):
    topic: str
    occasion: str = "業務會議"
    facts: list[Fact] = Field(min_length=1)
    stance: str = Field(description="建議立場與作法（草案）")


@registry.tool(name="briefing_pack", schema=BriefingArgs, side="agency")
def briefing_pack(ctx: ToolContext, args: BriefingArgs) -> dict[str, Any]:
    """產生談參 markdown：事實（附依據）與立場分開寫，並標示「需承辦核定」。"""
    lines = [
        f"# 談參：{args.topic}",
        f"場合：{args.occasion}　日期：{today().isoformat()}　狀態：**草案，需承辦核定**",
        "",
        "## 一、事實",
        *[f"{i}. {f.claim}（依據：{f.evidence}）" for i, f in enumerate(args.facts, start=1)],
        "",
        "## 二、立場與建議作法",
        args.stance,
        "",
        "## 三、資料說明",
        "群體統計皆為去識別化資料，每格至少 k 人；被遮蔽的格子不揭露、不推估。",
    ]
    return {"markdown": "\n".join(lines), "needs_approval": True}
