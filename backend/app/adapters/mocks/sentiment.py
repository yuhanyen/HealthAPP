"""公開輿情 Mock：讀 fixtures/sentiment_*.json（含帳號、頭像等身分欄位，用來驗證 C-8）。

項目可寫絕對時間（collected_at），或相對時間（days_ago＋hour，以台灣「今天」為基準），
讓示範情境 6（北區據點關閉的抱怨突增）在任何一天重建資料都落在「最近一週」。
未來時間的項目先不回傳（模擬資料陸續出現）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.base import FIXTURES_DIR
from app.config import get_settings


def _collected_at(item: dict[str, Any], today: datetime) -> datetime:
    if "collected_at" in item:
        return datetime.fromisoformat(item["collected_at"])
    tz = ZoneInfo(get_settings().timezone)
    day = today.astimezone(tz).date() - timedelta(days=int(item["days_ago"]))
    return datetime.combine(day, time(int(item.get("hour", 9))), tz)


class MockSentimentClient:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now

    def items(self, since: datetime) -> list[dict[str, Any]]:
        now = self.now or datetime.now(UTC)
        out: list[dict[str, Any]] = []
        for path in sorted(FIXTURES_DIR.glob("sentiment_*.json")):
            for item in json.loads(path.read_text(encoding="utf-8")):
                at = _collected_at(item, now)
                if since < at <= now:
                    out.append({**item, "collected_at": at.isoformat()})
        return out
