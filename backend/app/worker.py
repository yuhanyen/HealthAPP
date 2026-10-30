"""背景 worker：事件消費者 + 排程。啟動：python -m app.worker（make worker）。"""

from __future__ import annotations

import logging
import signal
import threading
from types import FrameType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.events import handlers as _register_handlers  # noqa: F401  註冊事件處理
from app.events.bus import get_bus
from app.events.consumer import run_forever
from app.scheduler import jobs

log = logging.getLogger("app.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(jobs.sweep_deletion_jobs, "interval", minutes=10, id="sweep_deletion_jobs")
    scheduler.add_job(jobs.expire_pending_actions, "interval", minutes=30, id="expire_actions")
    tz = settings.timezone
    scheduler.add_job(
        jobs.publish_daily_batch, CronTrigger(hour=5, minute=0, timezone=tz), id="daily_batch"
    )
    scheduler.add_job(
        jobs.daily_care_list,
        CronTrigger(hour=5, minute=30, timezone=tz),
        id="care_list",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.deliver_daily_reports,
        CronTrigger(hour=7, minute=0, timezone=tz),
        id="deliver_reports",
        max_instances=1,
        coalesce=True,
    )
    # SPEC §3：每小時同步、05:00 批次、每 6 小時爬蟲；健保在批次前 15 分鐘先同步
    scheduler.add_job(
        jobs.sync_hourly,
        CronTrigger(minute=5, timezone=tz),
        id="sync_hourly",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.sync_nhi_daily,
        CronTrigger(hour=4, minute=45, timezone=tz),
        id="sync_nhi",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.sync_sentiment_6h,
        CronTrigger(hour="*/6", minute=20, timezone=tz),
        id="sync_sentiment",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.sync_events_6h,
        CronTrigger(hour="*/6", minute=15, timezone=tz),
        id="sync_events",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        jobs.sync_merchant_ratings_daily,
        CronTrigger(hour=4, minute=30, timezone=tz),
        id="sync_merchant_ratings",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    jobs.sweep_deletion_jobs()  # 啟動時先補做一次

    stop = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        log.info("收到停止訊號")
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        run_forever(get_bus(), stop)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
