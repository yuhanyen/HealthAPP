"""sandbox_sql（SPEC §7.2）：A3 的唯讀查詢。anon_reader 連線＋k-匿名中介軟體，每次給查詢編號。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import Field

from app.services.sandbox import run_sql
from app.sysconfig import cached_value
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class SandboxSqlArgs(AgencyToolArgs):
    sql: str = Field(min_length=10, max_length=8000)


@registry.tool(name="sandbox_sql", schema=SandboxSqlArgs, side="agency")
def sandbox_sql(ctx: ToolContext, args: SandboxSqlArgs) -> dict[str, Any]:
    """對去識別化區（anon.*、pub.*）執行單一 SELECT。聚合查詢必須輸出人數欄 n；n < k 的列自動遮蔽。
    回傳查詢編號（Q-xxxx），結論要引用它。"""
    return asdict(run_sql(args.sql, k=int(cached_value("k", 5)), agent=ctx.agent))
