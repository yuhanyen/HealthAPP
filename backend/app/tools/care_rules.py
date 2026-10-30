"""care_rules（SPEC §7.2）：規則在 idn 執行，並發 care.list.ready。

只回傳代號（無姓名、無健康內容）。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date as Date
from typing import Any

from app.services.care import run_care_rules
from app.tools._common import AgencyToolArgs, today
from app.tools.registry import ToolContext, registry


class CareRulesArgs(AgencyToolArgs):
    date: Date | None = None


@registry.tool(name="care_rules", schema=CareRulesArgs, side="agency")
def care_rules(ctx: ToolContext, args: CareRulesArgs) -> dict[str, Any]:
    """執行關懷規則，產生需要據點關懷的代號清單（只有代號、任務類型、規則編號、據點）。"""
    created = run_care_rules(args.date or today())
    return {"care_tasks": [asdict(c) for c in created], "count": len(created)}
