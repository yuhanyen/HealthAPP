"""risk_score（SPEC §7.1）：風險分級 L0–L3 與觸發指標（規則見 services/risk.py）。"""

from __future__ import annotations

from typing import Any

from app.services import risk
from app.tools._common import today
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class RiskScoreArgs(CitizenToolArgs):
    pass


@registry.tool(name="risk_score", schema=RiskScoreArgs, side="citizen")
def risk_score(ctx: ToolContext, args: RiskScoreArgs) -> dict[str, Any]:
    """依本人授權範圍內的資料判斷風險等級（L0–L3）。單筆量測不觸發，需要連續性。"""
    result = risk.evaluate(ctx.db, args.user_id, args.consent_scope, today())
    return {"level": result.level, "triggers": result.triggers, "evidence": result.evidence}
