"""A5 據點營運的其餘工具（SPEC §7.3）：一期只回 not_implemented 並由 registry 留稽核。

五個 stub 放同一檔（例外於「每工具一檔」）：二期實作時各自拆檔。
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from app.i18n import t
from app.tools._common import SiteToolArgs
from app.tools.registry import ToolContext, registry


class StubArgs(SiteToolArgs):
    model_config = ConfigDict(extra="allow")


def _stub(name: str, description: str) -> None:
    def fn(ctx: ToolContext, args: StubArgs) -> dict[str, Any]:
        return {"status": "not_implemented", "tool": name, "message": t("tool.not_implemented")}

    fn.__doc__ = description
    registry.tool(name=name, schema=StubArgs, side="site")(fn)


for _name, _desc in (
    ("site_schedule", "據點每日排程與預約總覽（二期）"),
    ("dispatch", "志工派工（二期）"),
    ("visit_report", "訪視回報整理（二期）"),
    ("equipment", "設備校正與耗材提醒（二期）"),
    ("event_mgmt", "活動名單與報到（二期）"),
):
    _stub(_name, _desc)
