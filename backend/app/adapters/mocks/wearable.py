"""手機／穿戴裝置 Mock（真實：HealthKit／Health Connect，二期）。

依 fixtures/wearable_profiles.json 決定性地產生每小時步數與心率：同一人、同一小時永遠得到
同一個數字，所以「每小時同步」與「示範資料回填的每日總步數」會一致。
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from functools import cached_property
from typing import Any
from zoneinfo import ZoneInfo

from app.adapters.base import FIXTURES_DIR
from app.config import get_settings
from app.db.crypto import hash_national_id

HourRecord = dict[str, Any]


class MockWearableClient:
    def __init__(self, profiles: dict[str, Any] | None = None, today: date | None = None) -> None:
        self._profiles = profiles
        self._today = today

    @cached_property
    def profiles(self) -> dict[str, Any]:
        if self._profiles is not None:
            return self._profiles
        data: dict[str, Any] = json.loads(
            (FIXTURES_DIR / "wearable_profiles.json").read_text(encoding="utf-8")
        )
        return data

    @cached_property
    def _by_hash(self) -> dict[str, tuple[str, dict[str, Any]]]:
        table: dict[str, tuple[str, dict[str, Any]]] = {}
        for kind in ("personas", "cheats"):
            for nid, spec in self.profiles.get(kind, {}).items():
                table[hash_national_id(nid)] = (kind, spec)
        return table

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(get_settings().timezone)

    def today(self) -> date:
        return self._today or datetime.now(self.tz).date()

    def _days_ago(self, day: date) -> int:
        return (self.today() - day).days

    def _base_daily(self, subject: str) -> float:
        rng = self.profiles["default_daily_steps"]
        return float(rng["min"] + int(subject[:8], 16) % (rng["max"] - rng["min"]))

    def daily_target(self, subject: str, day: date) -> int:
        kind, spec = self._by_hash.get(subject, ("", {}))
        days_ago = self._days_ago(day)
        for override in spec.get("daily_override", []) if kind == "cheats" else []:
            if override["days_ago"] == days_ago:
                return int(override["steps"])
        mean = self._base_daily(subject)
        for seg in spec.get("segments", []) if kind == "personas" else []:
            if seg["to_days_ago"] <= days_ago <= seg["from_days_ago"]:
                mean = float(seg["mean"])
        rnd = random.Random(f"{subject}:{day.isoformat()}")
        sd = mean * self.profiles["default_daily_steps"]["sd_ratio"] * 0.5
        return max(300, int(rnd.gauss(mean, sd)))

    def hourly_steps(self, subject: str, day: date) -> list[int]:
        target = self.daily_target(subject, day)
        weights = self.profiles["hourly_weights"]
        rnd = random.Random(f"{subject}:{day.isoformat()}:hours")
        steps = [round(target * w * rnd.uniform(0.7, 1.3)) for w in weights]
        kind, spec = self._by_hash.get(subject, ("", {}))
        if kind == "cheats":
            for spike in spec.get("spikes", []):
                if spike["days_ago"] == self._days_ago(day):
                    steps[spike["hour"]] = int(spike["steps"])
        return steps

    def daily_total(self, subject: str, day: date) -> int:
        return sum(self.hourly_steps(subject, day))

    def resting_hr(self, subject: str) -> int:
        _, spec = self._by_hash.get(subject, ("", {}))
        if "resting_hr" in spec:
            return int(spec["resting_hr"])
        rng = self.profiles["resting_hr"]
        return int(rng["min"] + int(subject[8:12], 16) % (rng["max"] - rng["min"]))

    def hourly(self, subject: str, start: datetime, end: datetime) -> list[HourRecord]:
        """[start, end) 之間每個完整小時的步數與平均心率。"""
        tz = self.tz
        hour = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        if hour < start:
            hour += timedelta(hours=1)
        records: list[HourRecord] = []
        cache: dict[date, list[int]] = {}
        resting = self.resting_hr(subject)
        while hour + timedelta(hours=1) <= end:
            day = hour.date()
            steps = cache.setdefault(day, self.hourly_steps(subject, day))[hour.hour]
            records.append(
                {"hour": hour, "steps": steps, "heart_rate": resting + min(45, steps // 120)}
            )
            hour += timedelta(hours=1)
        return records
