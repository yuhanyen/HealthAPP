"""公開活動公告 Mock：讀 fixtures/event_notices.json（含發文者、電話等身分欄位，用來驗證 C-8）。

公告的活動日期以「今天 + days_ahead」代入 {date}、{weekday}，任何一天重建資料都落在近兩週。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.base import FIXTURES_DIR
from app.config import get_settings

WEEKDAYS = "一二三四五六日"


class MockEventClient:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now

    def items(self, since: datetime) -> list[dict[str, Any]]:
        now = self.now or datetime.now(UTC)
        today = now.astimezone(ZoneInfo(get_settings().timezone)).date()
        out: list[dict[str, Any]] = []
        for item in json.loads((FIXTURES_DIR / "event_notices.json").read_text(encoding="utf-8")):
            on = today + timedelta(days=int(item["days_ahead"]))
            text = item["text"].format(date=f"{on.month}/{on.day}", weekday=WEEKDAYS[on.weekday()])
            payload = {k: v for k, v in item.items() if k not in ("days_ahead", "text")}
            out.append({**payload, "text": text})
        return out
