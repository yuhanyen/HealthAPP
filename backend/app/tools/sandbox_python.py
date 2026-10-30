"""sandbox_python（SPEC §7.2）：子行程執行分析程式碼（60 秒、無網路、只有傳入的去識別化資料）。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import Field

from app.services import artifacts
from app.services.sandbox import run_python
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class SandboxPythonArgs(AgencyToolArgs):
    code: str = Field(min_length=1, max_length=20000)
    inputs: dict[str, Any] = Field(default_factory=dict)


@registry.tool(name="sandbox_python", schema=SandboxPythonArgs, side="agency")
def sandbox_python(ctx: ToolContext, args: SandboxPythonArgs) -> dict[str, Any]:
    """執行 Python（只有標準函式庫）。程式中可用 INPUTS（傳入的資料）與 OUT_DIR（輸出檔案目錄）。
    禁止網路；超過 60 秒中止。寫到 OUT_DIR 的 svg/csv/json 會成為產物。"""
    result = run_python(args.code, args.inputs)
    data = asdict(result)
    data["artifact_urls"] = [artifacts.url_of(a) for a in result.artifacts]
    return data
