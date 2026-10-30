"""delegate_a4（SPEC §8.3）：A2 把「這段時間外面發生什麼事」委派給 A4 社區輿情 Agent。

A4 交回的時間軸每一條都必須有來源連結，否則 A2 不採用（status=rejected）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class DelegateA4Args(AgencyToolArgs):
    question: str = Field(max_length=300, description="要回答的問題（一句話）")
    village_codes: list[str] = Field(default_factory=list, description="空陣列＝全區")
    days: int = Field(default=84, ge=1, le=120)
    topic: str | None = None


@registry.tool(name="delegate_a4", schema=DelegateA4Args, side="agency")
def delegate_a4(ctx: ToolContext, args: DelegateA4Args) -> dict[str, Any]:
    """委派 A4：指定里、期間（天）、主題；A4 回傳附來源連結的事件時間軸、主題分佈與突增偵測。"""
    from app.agents.delegate import delegate

    return delegate("A4", args.model_dump(), requested_by=ctx.audit_actor_id)
