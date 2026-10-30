"""活動公告與商家評分落地（SPEC §17.2）：只用 anon_writer 連線，只碰 pub，完全不接觸 idn。"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.adapters.base import SyncSummary
from app.adapters.events import SOURCE_CLASS, EventClient, EventsAdapter, Gazetteer
from app.adapters.merchant_rating import RatingClient, default_rating_client
from app.audit.writer import write_audit
from app.db.engines import DbRole, session_scope
from app.db.models_pub import Event, Merchant, Site, SourceWhitelist, Village

log = logging.getLogger(__name__)


def _audit(summary: SyncSummary) -> None:
    write_audit(
        None,
        actor_type="system",
        actor_id="scheduler",
        action="adapter.sync",
        tool=summary.adapter,
        output=asdict(summary),
        decision="executed" if summary.errors == 0 else "error",
    )


def sync_events(*, client: EventClient | None = None, use_llm: bool | None = None) -> SyncSummary:
    """抓公開公告 → 擷取 → 寫成 pending_review（已存在的同一則不重複寫）。"""
    summary = SyncSummary("EventsAdapter")
    try:
        with session_scope(DbRole.ANON_WRITER) as s:
            whitelist = list(
                s.scalars(
                    select(SourceWhitelist.url_pattern).where(
                        SourceWhitelist.active, SourceWhitelist.source_class == SOURCE_CLASS
                    )
                )
            )
            sites = s.execute(select(Site.id, Site.name, Site.village_code)).all()
            gazetteer = Gazetteer(
                sites={name: sid for sid, name, _ in sites},
                villages={
                    name: code for code, name in s.execute(select(Village.code, Village.name))
                },
                site_village={sid: village for sid, _, village in sites},
            )
            adapter = EventsAdapter(whitelist, gazetteer, client, use_llm=use_llm)
            raws = adapter.fetch(datetime.now(UTC) - timedelta(days=30))
            records = [r for raw in raws for r in adapter.normalize(raw)]
            known = set(
                s.scalars(
                    select(Event.dedupe_key).where(
                        Event.dedupe_key.in_([r["dedupe_key"] for r in records])
                    )
                )
            )
            fresh = [r for r in records if r["dedupe_key"] not in known]
            s.add_all(Event(**r) for r in fresh)
            summary.written = len(fresh)
            summary.skipped = len(raws) - len(fresh)
    except Exception:
        log.exception("活動公告同步失敗")
        summary.errors += 1
    _audit(summary)
    return summary


def sync_merchant_ratings(*, client: RatingClient | None = None) -> SyncSummary:
    """商家公開評分（Google Places 或 Mock）→ pub.merchants 的評分欄位。"""
    summary = SyncSummary("MerchantRatingAdapter")
    try:
        client = client or default_rating_client()
        with session_scope(DbRole.ANON_WRITER) as s:
            ids = list(s.scalars(select(Merchant.id)))
            for merchant_id, rating in client.ratings(ids).items():
                s.execute(
                    update(Merchant)
                    .where(Merchant.id == merchant_id)
                    .values(
                        rating=rating.rating,
                        rating_count=rating.count,
                        rating_source=rating.source,
                        rating_updated_at=datetime.now(UTC),
                    )
                )
                summary.written += 1
            summary.skipped = len(ids) - summary.written
    except Exception:
        log.exception("商家評分同步失敗")
        summary.errors += 1
    _audit(summary)
    return summary
