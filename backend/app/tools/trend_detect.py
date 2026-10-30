"""trend_detect（SPEC §7.2）：輿情突增偵測。基線＝前 4 週平均；比值 ≥ 3 且來源類型 ≥ 2 才算警訊。

計的是公開貼文則數（不是人數），所以逐筆取出後在程式內計數，不經人數遮蔽。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field
from sqlalchemy import select

from app.db.models_anon import SentimentItem
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry

THRESHOLD = 3.0
MIN_SOURCES = 2
MIN_ITEMS = 3


class TrendArgs(AgencyToolArgs):
    topic: str | None = Field(default=None, description="省略＝所有主題")
    village_code: str | None = Field(default=None, description="省略＝全區")
    window_days: int = Field(default=7, ge=1, le=28)
    as_of: datetime | None = None


@registry.tool(name="trend_detect", schema=TrendArgs, side="agency")
def trend_detect(ctx: ToolContext, args: TrendArgs) -> dict[str, Any]:
    """比較最近 window_days 天與前 4 週基線的輿情則數；ratio ≥ 3 且來源類型 ≥ 2 為警訊。"""
    as_of = args.as_of or datetime.now(UTC)
    window_start = as_of - timedelta(days=args.window_days)
    baseline_start = window_start - timedelta(days=28)
    stmt = select(
        SentimentItem.collected_at,
        SentimentItem.source_class,
        SentimentItem.source_url,
        SentimentItem.sentiment,
    ).where(SentimentItem.collected_at >= baseline_start, SentimentItem.collected_at < as_of)
    if args.topic:
        stmt = stmt.where(SentimentItem.topic == args.topic)
    if args.village_code:
        stmt = stmt.where(SentimentItem.village_code == args.village_code)
    rows = ctx.db.execute(stmt).all()
    window = [r for r in rows if r.collected_at >= window_start]
    baseline = [r for r in rows if r.collected_at < window_start]
    expected = len(baseline) / 4 * (args.window_days / 7)
    ratio = len(window) / max(expected, 0.5)
    sources = sorted({r.source_class for r in window})
    is_alert = ratio >= THRESHOLD and len(sources) >= MIN_SOURCES and len(window) >= MIN_ITEMS
    return {
        "topic": args.topic,
        "village_code": args.village_code,
        "window_count": len(window),
        "baseline_weekly": round(len(baseline) / 4, 2),
        "ratio": round(ratio, 2),
        "source_classes": sources,
        "is_alert": is_alert,
        "negative_share": round(sum(r.sentiment == "negative" for r in window) / len(window), 2)
        if window
        else 0.0,
        "source_urls": sorted({r.source_url for r in window})[:20],
    }
