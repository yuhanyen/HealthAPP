"""可識別區 Adapter 的同步執行（排程呼叫）：wearable 每小時、nhi 每日、env 每小時。

每位使用者一個交易；授權在 fhir/writer 的同一交易內再確認一次（撤回當下立即停止）。
每次同步寫一筆 system 稽核摘要。輿情（anon）見 sentiment_land.py。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.base import FetchContext, SyncSummary
from app.adapters.env import EnvAdapter
from app.adapters.nhi import NhiAdapter
from app.adapters.wearable import WearableAdapter
from app.audit.writer import write_audit
from app.config import get_settings
from app.db.engines import DbRole, session_scope
from app.db.models_idn import Consent, FhirResource, User
from app.db.models_pub import EnvHourly
from app.fhir import codes
from app.fhir.writer import ingest

log = logging.getLogger(__name__)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def active_subjects(session: Session, source: str) -> list[tuple[uuid.UUID, str]]:
    rows = session.execute(
        select(User.id, User.national_id_hash)
        .join(Consent, Consent.user_id == User.id)
        .where(
            Consent.source == source,
            Consent.revoked_at.is_(None),
            Consent.superseded_at.is_(None),
        )
        .order_by(User.id)
    ).all()
    return [(row[0], row[1]) for row in rows]


def _finish(summary: SyncSummary) -> SyncSummary:
    write_audit(
        None,
        actor_type="system",
        actor_id="scheduler",
        action="adapter.sync",
        tool=summary.adapter,
        output=asdict(summary),
        decision="executed" if summary.errors == 0 else "error",
    )
    log.info("%s 同步完成：%s", summary.adapter, asdict(summary))
    return summary


def sync_wearable(
    *,
    now: datetime | None = None,
    adapter: WearableAdapter | None = None,
    user_ids: set[uuid.UUID] | None = None,
    lookback_hours: int = 24,
) -> SyncSummary:
    now = now or datetime.now(UTC)
    adapter = adapter or WearableAdapter()
    summary = SyncSummary("WearableAdapter")
    with session_scope(DbRole.IDN_APP) as s:
        subjects = active_subjects(s, "wearable")
    for user_id, subject in subjects:
        if user_ids is not None and user_id not in user_ids:
            continue
        summary.users += 1
        try:
            with session_scope(DbRole.IDN_APP) as s:
                last = s.scalar(
                    select(func.max(FhirResource.effective_at)).where(
                        FhirResource.user_id == user_id,
                        FhirResource.source == "wearable",
                        FhirResource.code == codes.STEPS_HOURLY,
                    )
                )
                start = (
                    last + timedelta(hours=1)
                    if last
                    else _floor_hour(now - timedelta(hours=lookback_hours))
                )
                raws = adapter.fetch(start, FetchContext(user_id, subject, until=now))
                resources = [r for raw in raws for r in adapter.normalize(raw)]
                if resources:
                    result = ingest(s, user_id=user_id, source="wearable", resources=resources)
                    summary.written += result.written
                    summary.skipped += result.out_of_scope
        except Exception:
            log.exception("wearable 同步失敗：%s", user_id)
            summary.errors += 1
    return _finish(summary)


def sync_nhi(
    *, adapter: NhiAdapter | None = None, user_ids: set[uuid.UUID] | None = None
) -> SyncSummary:
    adapter = adapter or NhiAdapter()
    summary = SyncSummary("NhiAdapter")
    with session_scope(DbRole.IDN_APP) as s:
        subjects = active_subjects(s, "nhi")
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    for user_id, subject in subjects:
        if user_ids is not None and user_id not in user_ids:
            continue
        summary.users += 1
        try:
            raws = adapter.fetch(epoch, FetchContext(user_id, subject))
            resources = [r for raw in raws for r in adapter.normalize(raw)]
            if resources:
                with session_scope(DbRole.IDN_APP) as s:
                    result = ingest(s, user_id=user_id, source="nhi", resources=resources)
                summary.written += result.written
                summary.skipped += result.out_of_scope
        except Exception:
            log.exception("nhi 同步失敗：%s", user_id)
            summary.errors += 1
    return _finish(summary)


def sync_env(
    *, now: datetime | None = None, adapter: EnvAdapter | None = None, lookback_hours: int = 24
) -> SyncSummary:
    now = now or datetime.now(UTC)
    adapter = adapter or EnvAdapter()
    district = get_settings().district
    summary = SyncSummary("EnvAdapter")
    try:
        with session_scope(DbRole.IDN_APP) as s:
            last = s.scalar(select(func.max(EnvHourly.hour)).where(EnvHourly.district == district))
            start = (
                last + timedelta(hours=1)
                if last
                else _floor_hour(now - timedelta(hours=lookback_hours))
            )
            rows = [
                {**row, "district": district}
                for raw in adapter.fetch(start, FetchContext(until=now))
                for row in adapter.normalize(raw)
            ]
            if rows:
                stmt = insert(EnvHourly).values(rows)
                s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[EnvHourly.hour, EnvHourly.district],
                        set_={k: stmt.excluded[k] for k in ("temp", "heat_index", "aqi", "rain")},
                    )
                )
            summary.written = len(rows)
    except Exception:
        log.exception("env 同步失敗")
        summary.errors += 1
    return _finish(summary)
