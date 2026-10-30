"""delegate_a3（SPEC §8.3）：A2 把「要算的事」委派給 A3 數據分析 Agent。

A3 必須交回查詢編號（queries 非空）與每個結論的依據，否則 A2 不採用（status=rejected）。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.deidentify.aggregate import METRICS
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class DelegateA3Args(AgencyToolArgs):
    question: str = Field(max_length=300, description="要回答的問題（一句話）")
    village_codes: list[str] = Field(default_factory=list, description="空陣列＝全部里")
    metrics: list[str] = Field(min_length=1, max_length=4)
    weeks: int = Field(default=12, ge=4, le=52)

    @field_validator("metrics")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        unknown = [m for m in value if m not in METRICS]
        if unknown:
            raise ValueError(f"未知指標：{'、'.join(unknown)}")
        return value


@registry.tool(name="delegate_a3", schema=DelegateA3Args, side="agency")
def delegate_a3(ctx: ToolContext, args: DelegateA3Args) -> dict[str, Any]:
    """委派 A3：指定里、指標（第一個為主要指標）、週數。
    A3 回傳發現（附查詢編號）、圖表、報告與資料截止日。"""
    from app.agents.delegate import delegate

    return delegate("A3", args.model_dump(), requested_by=ctx.audit_actor_id)
