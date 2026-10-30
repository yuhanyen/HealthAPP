"""report_build（SPEC §7.2）：組成 HTML 分析報告（段落＋圖表＋附錄：查詢編號、資料截止、k 值）。"""

from __future__ import annotations

import base64
import re
from html import escape
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services import artifacts
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    body: str = ""
    charts: list[str] = Field(default_factory=list, description="chart_render 回傳的產物編號")


class ReportArgs(AgencyToolArgs):
    title: str
    sections: list[Section] = Field(min_length=1)
    appendix: list[str] = Field(default_factory=list)


def markdown_lite(text: str) -> str:
    """段落、條列、粗體；其餘一律跳脫。"""
    out: list[str] = []
    in_list = False
    for line in text.splitlines():
        stripped = line.strip()
        item = re.match(r"^[-*•]\s+(.*)", stripped)
        if item:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(item.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if stripped:
            out.append(f"<p>{_inline(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(text))


@registry.tool(name="report_build", schema=ReportArgs, side="agency")
def report_build(ctx: ToolContext, args: ReportArgs) -> dict[str, Any]:
    """把分析段落與圖表組成一份 HTML 報告；附錄放查詢編號、資料截止日與 k 值。"""
    parts = [
        "<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>",
        f"<title>{escape(args.title)}</title><style>body{{font-family:'Noto Sans TC',"
        "'Microsoft JhengHei',sans-serif;max-width:880px;margin:32px auto;padding:0 16px;"
        "line-height:1.7;color:#1c2430}}img{max-width:100%}h2{border-bottom:1px solid #e5e7eb;"
        "padding-bottom:4px}.appendix{color:#5d6878;font-size:14px}"
        "pre{white-space:pre-wrap;background:#f4f6f8;padding:8px;border-radius:6px}"
        "</style></head><body>",
        f"<h1>{escape(args.title)}</h1>",
    ]
    for section in args.sections:
        parts.append(f"<h2>{escape(section.heading)}</h2>{markdown_lite(section.body)}")
        for chart in section.charts:
            path = artifacts.path_of(chart)
            if path and path.suffix == ".svg":
                # 內嵌成 data URI：報告自成一檔（下載時以 CSP sandbox 提供，不能再向外載入圖片）
                data = base64.b64encode(path.read_bytes()).decode()
                parts.append(f"<img src='data:image/svg+xml;base64,{data}' alt='{escape(chart)}'>")
    if args.appendix:
        items = "".join(
            f"<li><pre>{escape(a)}</pre></li>" if "\n" in a else f"<li>{escape(a)}</li>"
            for a in args.appendix
        )
        parts.append(f"<h2>附錄</h2><ul class='appendix'>{items}</ul>")
    parts.append("</body></html>")
    name = artifacts.save("".join(parts), "html")
    return {"artifact": name, "url": artifacts.url_of(name)}
