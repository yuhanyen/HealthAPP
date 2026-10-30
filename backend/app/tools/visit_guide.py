"""visit_guide（SPEC §7.3）：依任務類型產生關懷訪綱（只有任務，不含任何健康資料）。"""

from __future__ import annotations

from typing import Any, Literal

from app.i18n import t_list
from app.tools._common import SiteToolArgs
from app.tools.registry import ToolContext, registry


class VisitGuideArgs(SiteToolArgs):
    task_type: Literal["revisit_reminder", "phone_followup", "home_visit"]


@registry.tool(name="visit_guide", schema=VisitGuideArgs, side="site")
def visit_guide(ctx: ToolContext, args: VisitGuideArgs) -> dict[str, Any]:
    """產生志工可用的關懷訪綱（不含健康數值或就醫內容）。"""
    return {"task_type": args.task_type, "steps": t_list(f"visit_guide.{args.task_type}")}
