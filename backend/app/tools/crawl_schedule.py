"""crawl_schedule（SPEC §8.2 A4）：抓取白名單來源（落地前去識別）並為未分類項目補上主題與情緒。"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.adapters.sentiment_land import sync_sentiment
from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentItem
from app.db.models_pub import Site, Village
from app.services import nlp
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class CrawlArgs(AgencyToolArgs):
    pass


def classify_unlabelled(limit: int = 200) -> int:
    with session_scope(DbRole.ANON_WRITER) as s:
        items = list(
            s.scalars(
                select(SentimentItem)
                .where(SentimentItem.topic == "未分類")
                .order_by(SentimentItem.id)
                .limit(limit)
            )
        )
        if not items:
            return 0
        gazetteer = list(s.scalars(select(Village.name))) + list(s.scalars(select(Site.name)))
        for item, label in zip(
            items, nlp.batch([i.summary for i in items], gazetteer=gazetteer), strict=True
        ):
            item.topic = label["topic"]
            item.sentiment = label["sentiment"]
            item.intensity = Decimal(str(label["intensity"]))
        return len(items)


@registry.tool(name="crawl_schedule", schema=CrawlArgs, side="agency")
def crawl_schedule(ctx: ToolContext, args: CrawlArgs) -> dict[str, Any]:
    """執行一輪輿情採集（只抓白名單來源，落地前去識別），並分類尚未分類的項目。"""
    summary = sync_sentiment()
    return {"sync": asdict(summary), "classified": classify_unlabelled()}
