"""輿情落地（每 6 小時）：只用 anon_writer 連線，只碰 anon 與 pub 白名單，完全不接觸 idn。"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.adapters.base import SyncSummary
from app.adapters.sentiment import SentimentAdapter, SentimentClient
from app.audit.writer import write_audit
from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentItem
from app.db.models_pub import SourceWhitelist

log = logging.getLogger(__name__)


def sync_sentiment(*, client: SentimentClient | None = None) -> SyncSummary:
    summary = SyncSummary("SentimentAdapter")
    try:
        with session_scope(DbRole.ANON_WRITER) as s:
            whitelist = list(
                s.scalars(select(SourceWhitelist.url_pattern).where(SourceWhitelist.active))
            )
            since = s.scalar(select(func.max(SentimentItem.collected_at))) or datetime(
                2000, 1, 1, tzinfo=UTC
            )
            adapter = SentimentAdapter(whitelist, client)
            raws = adapter.fetch(since)
            records = [r for raw in raws for r in adapter.normalize(raw)]
            # 同一連結＋同一內容視為同一則（轉貼、重抓），不重複計數
            seen = {
                (url, summary_text)
                for url, summary_text in s.execute(
                    select(SentimentItem.source_url, SentimentItem.summary).where(
                        SentimentItem.source_url.in_({r["source_url"] for r in records})
                    )
                )
            }
            fresh = []
            for record in records:
                key = (record["source_url"], record["summary"])
                if key not in seen:
                    seen.add(key)
                    fresh.append(record)
            s.add_all(SentimentItem(**record) for record in fresh)
            summary.written = len(fresh)
            summary.skipped = len(raws) - len(fresh)
    except Exception:
        log.exception("輿情同步失敗")
        summary.errors += 1
    write_audit(
        None,
        actor_type="system",
        actor_id="scheduler",
        action="adapter.sync",
        tool=summary.adapter,
        output=asdict(summary),
        decision="executed" if summary.errors == 0 else "error",
    )
    return summary
