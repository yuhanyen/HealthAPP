"""sentiment_search（A4）：在去識別化輿情語料庫中篩選某地區、期間、主題的項目，供時間軸與主題分佈。

語料庫只有內容摘要、時間、來源連結、地點、主題、情緒（帳號等身分欄位在落地前已移除，C-8）。
計的是貼文則數（不是人數），不經人數遮蔽。
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field
from sqlalchemy import select

from app.db.models_anon import SentimentItem
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class SentimentSearchArgs(AgencyToolArgs):
    village_codes: list[str] = Field(default_factory=list, description="空陣列＝全區（含未標地點）")
    topic: str | None = None
    days: int = Field(default=28, ge=1, le=120)
    limit: int = Field(default=100, ge=1, le=300)
    as_of: datetime | None = None


@registry.tool(name="sentiment_search", schema=SentimentSearchArgs, side="agency")
def sentiment_search(ctx: ToolContext, args: SentimentSearchArgs) -> dict[str, Any]:
    """查詢去識別化輿情：某些里、最近 days 天、（可選）某主題。回傳項目（含來源連結）與主題分佈。"""
    as_of = args.as_of or datetime.now(UTC)
    stmt = select(SentimentItem).where(
        SentimentItem.collected_at >= as_of - timedelta(days=args.days),
        SentimentItem.collected_at <= as_of,
    )
    if args.village_codes:
        # 未標地點的項目（例如全區新聞）也納入，由時間軸自行判讀
        stmt = stmt.where(
            SentimentItem.village_code.in_(args.village_codes)
            | SentimentItem.village_code.is_(None)
        )
    if args.topic:
        stmt = stmt.where(SentimentItem.topic == args.topic)
    rows = list(ctx.db.scalars(stmt.order_by(SentimentItem.collected_at.desc()).limit(args.limit)))
    topics = Counter(r.topic for r in rows)
    return {
        "count": len(rows),
        "items": [
            {
                "collected_at": r.collected_at.isoformat(),
                "source_class": r.source_class,
                "source_url": r.source_url,
                "village_code": r.village_code,
                "topic": r.topic,
                "sentiment": r.sentiment,
                "summary": r.summary,
            }
            for r in rows
        ],
        "topics": [
            {"topic": topic, "count": n, "share": round(n / len(rows), 2)}
            for topic, n in topics.most_common()
        ],
        "negative_share": round(sum(r.sentiment == "negative" for r in rows) / len(rows), 2)
        if rows
        else 0.0,
    }
