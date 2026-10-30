"""chart_render（SPEC §7.2）：依規格畫 SVG 圖表並存成產物。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services import artifacts
from app.services.charts import render_svg
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class Series(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[float | None]


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["line", "bar"] = "line"
    title: str = ""
    x: list[str] = Field(min_length=1)
    series: list[Series] = Field(min_length=1)
    y_label: str | None = None


class ChartArgs(AgencyToolArgs):
    spec: ChartSpec


@registry.tool(name="chart_render", schema=ChartArgs, side="agency")
def chart_render(ctx: ToolContext, args: ChartArgs) -> dict[str, Any]:
    """畫折線圖或長條圖（SVG）。回傳產物編號與網址，可放進 report_build。"""
    name = artifacts.save(render_svg(args.spec.model_dump()), "svg")
    return {"artifact": name, "url": artifacts.url_of(name)}
