"""A4 社區輿情 Agent（SPEC §8.2；行為見 skills/a4-community-sentiment/SKILL.md）。

- 委派查詢（A2 → A4）：某些里、某段期間「外面發生什麼事」，交回每條都附來源連結的時間軸。
- 排程（每 6 小時）：crawl_schedule（白名單、落地前去識別、補分類）→ 依（里, 主題）偵測突增 →
  來源多樣性 ≥ 2 才 alert_emit（事件匯流 sentiment.alert，交給 A2）。
只做聚合、不建個人畫像；語料庫本身已無帳號資訊（C-8）。連線一律 anon_reader（C-2）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.adapters.base import mock_notice
from app.agents import agency, runtime
from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentAlert
from app.i18n import t
from app.llm import client as llm

log = logging.getLogger(__name__)

AGENT = "A4"
CATEGORY = {
    "據點開放": "service_gap",
    "排隊等候": "service_gap",
    "環境設施": "service_gap",
    "服務態度": "service_quality",
    "健康活動": "activity",
}
DISEASE_WORDS = ("流感", "登革熱", "腸病毒", "熱傷害", "確診")


def _call(actor: str, tool: str, **args: Any) -> dict[str, Any]:
    return agency.call(AGENT, actor, tool, **args)


def run(task: dict[str, Any]) -> dict[str, Any]:
    if llm.llm_available():
        return _run_claude(task)
    mock_notice("A4", "未設定 ANTHROPIC_API_KEY，A4 以固定流程整理時間軸")
    return timeline(task)


def timeline(task: dict[str, Any]) -> dict[str, Any]:
    """委派查詢：篩選語料 → 時間軸（每條附來源）→ 主題分佈 → 近 7 日突增。"""
    actor = f"{AGENT}:{task['task_id']}"
    spec = task["spec"]
    villages = agency.clean_villages(spec.get("village_codes"))
    days = int(spec.get("days", 84))
    found = _call(
        actor,
        "sentiment_search",
        village_codes=villages,
        days=days,
        topic=spec.get("topic"),
        limit=200,
    )
    items = found["items"]
    # 時間軸以負面與新聞為主（事件），依日期排列；只交有來源連結的項目
    picked = [
        i
        for i in items
        if i["source_url"] and (i["sentiment"] == "negative" or i["source_class"] == "news")
    ]
    picked = sorted(picked, key=lambda i: i["collected_at"])[-15:]
    events = [
        {
            "date": i["collected_at"][:10],
            "event": i["summary"],
            "source_class": i["source_class"],
            "source_url": i["source_url"],
            "village_code": i["village_code"],
            "topic": i["topic"],
            "sentiment": i["sentiment"],
        }
        for i in picked
    ]
    trends = []
    for village in villages or sorted({i["village_code"] for i in items if i["village_code"]}):
        for topic in sorted({i["topic"] for i in items if i["village_code"] == village}):
            result = _call(actor, "trend_detect", topic=topic, village_code=village)
            if result["window_count"]:
                trends.append(result)
    rising = [r for r in trends if r["is_alert"]]
    disease = any(w in i["summary"] for i in items for w in DISEASE_WORDS)
    return {
        "timeline": events,
        "topics": found["topics"][:3],
        "negative_share": found["negative_share"],
        "trends": trends,
        "trend_summary": t(
            "a4.trend_summary",
            n=found["count"],
            days=days,
            neg=round(found["negative_share"] * 100),
            rising="、".join(f"{r['village_code']}「{r['topic']}」" for r in rising)
            or t("a4.none"),
        ),
        "disease_note": t("a4.disease_note") if disease else None,
    }


def scheduled_run(as_of: datetime | None = None) -> list[int]:
    """每 6 小時：採集＋分類 → 偵測突增 → 發警訊（同里同主題 3 天內只發一次）。"""
    actor = f"{AGENT}:schedule"
    _call(actor, "crawl_schedule")
    now = as_of or datetime.now(UTC)
    recent = _call(actor, "sentiment_search", days=7, limit=300, as_of=now.isoformat())["items"]
    combos = sorted({(i["village_code"], i["topic"]) for i in recent if i["village_code"]})
    with session_scope(DbRole.ANON_READER) as s:
        open_alerts = {
            (village, topic)
            for village, topic in s.execute(
                select(SentimentAlert.village_code, SentimentAlert.topic).where(
                    SentimentAlert.created_at >= now - timedelta(days=3)
                )
            )
        }
    emitted: list[int] = []
    for village, topic in combos:
        if (village, topic) in open_alerts:
            continue
        trend = _call(
            actor, "trend_detect", topic=topic, village_code=village, as_of=now.isoformat()
        )
        if not trend["is_alert"]:
            continue
        alert = _call(
            actor,
            "alert_emit",
            village_code=village,
            topic=topic,
            category=CATEGORY.get(topic, "other"),
            count=trend["window_count"],
            baseline=trend["baseline_weekly"],
            ratio=trend["ratio"],
        )
        emitted.append(int(alert["alert_id"]))
        log.info(
            "[A4] 輿情警訊 %s %s：%s 則（基線 %s）",
            village,
            topic,
            trend["window_count"],
            trend["baseline_weekly"],
        )
    return emitted


def _run_claude(task: dict[str, Any]) -> dict[str, Any]:
    spec = runtime.AGENTS[AGENT]
    rc = runtime.RunContext(
        agent=spec,
        tool_context=agency.context_factory(AGENT, f"{AGENT}:{task['task_id']}"),
        context_text=t("a4.context", task=json.dumps(task, ensure_ascii=False)),
        actor_id=f"{AGENT}:{task['task_id']}",
    )
    done = None
    for event in runtime.run(
        rc,
        history=[],
        user_message=task["spec"].get("question", ""),
        planner=runtime.ClaudePlanner(spec),
    ):
        if isinstance(event, runtime.DoneEvent):
            done = event
    return (agency.last_json(done.text) if done else None) or {}
