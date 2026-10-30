"""排程工作（worker 內以 APScheduler 執行）。"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.actions.state_machine import expire_due
from app.audit.writer import write_audit
from app.config import get_settings
from app.consent.service import pending_deletion_jobs, run_deletion_job
from app.db.engines import DbRole, session_scope
from app.events.bus import get_bus

log = logging.getLogger(__name__)


def sweep_deletion_jobs() -> int:
    """補做所有待執行的刪除任務（事件遺失或 worker 重啟時的安全網，確保 24h SLA）。"""
    with session_scope(DbRole.IDN_APP) as s:
        job_ids = pending_deletion_jobs(s)
    done = 0
    for job_id in job_ids:
        with session_scope(DbRole.IDN_APP) as s:
            if run_deletion_job(s, job_id) is not None:
                done += 1
    if done:
        log.info("刪除任務補做 %d 筆", done)
    return done


def sync_hourly() -> None:
    from app.adapters.runner import sync_env, sync_wearable

    sync_env()
    sync_wearable()


def sync_nhi_daily() -> None:
    from app.adapters.runner import sync_nhi

    sync_nhi()


def sync_sentiment_6h() -> None:
    """A4 排程（每 6 小時）：採集＋分類＋突增偵測＋警訊。"""
    from app.agents.a4 import scheduled_run

    scheduled_run()


def sync_events_6h() -> None:
    """公開活動公告匯入（SPEC §17.2；每 6 小時）：一律待審，機關審核後才推給民眾。"""
    from app.adapters.events_land import sync_events

    sync_events()


def sync_merchant_ratings_daily() -> None:
    from app.adapters.events_land import sync_merchant_ratings

    sync_merchant_ratings()


def daily_care_list() -> None:
    """05:30 A2 關懷代號清單（care.list.ready 送各據點）。"""
    from app.agents.a2 import daily_care_list as run

    run()


def expire_pending_actions() -> int:
    with session_scope(DbRole.IDN_APP) as s:
        return expire_due(s)


def deliver_daily_reports() -> int:
    """07:00 推送今日報告（SKILL A1 步驟 8）。"""
    from app.agents.a1 import deliver_reports

    return deliver_reports()


def publish_daily_batch() -> str:
    """05:00 觸發每日批次（A1 全體、deidentify、A2 關懷清單於後續里程碑消費）。"""
    today = datetime.now(ZoneInfo(get_settings().timezone)).date().isoformat()
    msg_id = get_bus().publish("daily.batch", date=today)
    write_audit(
        None,
        actor_type="system",
        actor_id="scheduler",
        action="daily.batch",
        decision="executed",
        input={"date": today},
    )
    return msg_id
