"""事件處理登記（SPEC §6「消費者」欄）。M1 只有 consent.revoked；其餘於各里程碑補上。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.consent.service import run_deletion_job
from app.db.engines import DbRole, session_scope
from app.events.consumer import handles

log = logging.getLogger(__name__)


@handles("adapters", "consent.revoked")
def stop_source_and_delete(event: dict[str, Any]) -> None:
    """Adapter 端：該來源已由 is_source_allowed() 即時擋下；這裡立刻執行資料刪除任務。"""
    job_id = event.get("deletion_job_id")
    log.info("[adapters] 已停止拉取 %s（consent=%s）", event["source"], event["consent_id"])
    if job_id:
        with session_scope(DbRole.IDN_APP) as s:
            counts = run_deletion_job(s, uuid.UUID(job_id))
        log.info("[adapters] 刪除任務 %s 完成：%s", job_id, counts)


@handles("deidentify", "daily.batch")
def deidentify_daily(event: dict[str, Any]) -> None:
    """05:00：重算本週與上週的群體統計（遲到的資料也會被納入）。"""
    from app.deidentify.aggregate import run_deidentify

    run_deidentify()


@handles("a1", "consent.revoked")
def refresh_twin_scope(event: dict[str, Any]) -> None:
    """A1：雙生快照、引用該來源的報告與對話已隨刪除任務清除；之後只用剩餘授權。"""
    log.info("[A1] 授權撤回，後續報告不再引用 %s", event["source"])


@handles("a1", "daily.batch")
def a1_daily_batch(event: dict[str, Any]) -> None:
    """05:00：每位 App 使用者更新雙生並產生今日報告（07:00 由排程推送）。"""
    from datetime import date

    from app.agents.a1 import run_daily_batch

    run_daily_batch(date.fromisoformat(event["date"]) if event.get("date") else None)


@handles("a2", "sentiment.alert")
def a2_alert(event: dict[str, Any]) -> None:
    """A4 送來警訊：A2 委派 A3（數據）與 A4（事件）對照，結果寫回警訊（給公衛儀表板）。"""
    from app.agents.a2 import analyze_alert

    analyze_alert(int(event["alert_id"]))


@handles("a1", "measurement.created")
def a1_measurement(event: dict[str, Any]) -> None:
    """據點量測進來：連續偏高達 L2 以上時即時提醒本人（同一天只提醒一次）。"""
    from app.agents.a1 import on_measurement

    if event.get("source") != "site":  # 穿戴每小時同步也會發此事件，血壓只來自據點
        return
    if on_measurement(uuid.UUID(event["user_id"])):
        log.info("[A1] 已發出異常提醒")
