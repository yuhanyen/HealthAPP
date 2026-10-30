"""環境資料 Mock：氣溫依日夜變化、AQI 緩慢漂移，同一小時永遠同一個值。"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings


class MockEnvClient:
    def hourly(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        tz = ZoneInfo(get_settings().timezone)
        hour = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        records: list[dict[str, Any]] = []
        while hour <= end:
            rnd = random.Random(f"env:{hour.isoformat()}")
            day_rnd = random.Random(f"env:{hour.date().isoformat()}")
            base = 22 + 4 * math.sin((hour.timetuple().tm_yday - 100) / 365 * 2 * math.pi)
            diurnal = 5 * max(0.0, math.cos((hour.hour - 14) / 24 * 2 * math.pi))
            temp = round(base + diurnal + rnd.uniform(-0.6, 0.6), 1)
            aqi = int(40 + 60 * day_rnd.random() + 25 * math.sin(hour.hour / 24 * math.pi))
            records.append(
                {
                    "hour": hour,
                    "temp": temp,
                    "humidity": round(65 + 15 * day_rnd.random() - diurnal, 1),
                    "aqi": aqi,
                    "rain": day_rnd.random() < 0.15 and rnd.random() < 0.4,
                }
            )
            hour += timedelta(hours=1)
        return records
