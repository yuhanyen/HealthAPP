"""風險分級（SPEC §7.1 risk_score）。單筆不觸發，需連續性；只看授權範圍內的資料。

- L2：最近 3 次據點血壓（30 天內）都 ≥ 140/90
- L1：近 14 日步數均值較前 14 日下降 ≥ 40%（兩段各至少 7 天資料）
- L3：已達 L2，且 30 日內沒有回診（需 nhi.visits 授權才能判斷）
閾值可由 sys.config.risk_rules 調整。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services import health_data as hd
from app.sysconfig import cached_value

LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_RULES = {
    "bp_systolic": 140,
    "bp_diastolic": 90,
    "bp_consecutive": 3,
    "steps_drop_ratio": 0.4,
    "l3_no_revisit_days": 30,
}


@dataclass
class RiskResult:
    level: str = "L0"
    triggers: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def raise_to(self, level: str, trigger: str) -> None:
        if LEVELS.index(level) > LEVELS.index(self.level):
            self.level = level
        self.triggers.append(trigger)


def _in_scope(scope: dict[str, list[str]], source: str, item: str) -> bool:
    return item in scope.get(source, [])


def evaluate(
    session: Session, user_id: uuid.UUID, scope: dict[str, list[str]], today: date
) -> RiskResult:
    rules = {**DEFAULT_RULES, **cached_value("risk_rules", {})}
    result = RiskResult()
    now = datetime.combine(today, datetime.min.time(), hd.tz())

    if _in_scope(scope, "site", "bp"):
        recent = hd.bp_readings(
            session, user_id, since=now - timedelta(days=30), limit=int(rules["bp_consecutive"])
        )
        result.evidence["bp_last"] = [
            {
                "systolic": r.systolic,
                "diastolic": r.diastolic,
                "at": r.at.isoformat(),
                "site": r.site,
            }
            for r in recent
        ]
        high = [
            r.systolic >= rules["bp_systolic"] or r.diastolic >= rules["bp_diastolic"]
            for r in recent
        ]
        if len(high) == rules["bp_consecutive"] and all(high):
            result.raise_to("L2", "bp_consecutive_high")

    if _in_scope(scope, "wearable", "steps"):
        steps = hd.daily_steps(session, user_id, today - timedelta(days=28), today)
        last = [v for d, v in steps.items() if d >= today - timedelta(days=14)]
        prev = [v for d, v in steps.items() if d < today - timedelta(days=14)]
        if len(last) >= 7 and len(prev) >= 7:
            last_avg, prev_avg = sum(last) / len(last), sum(prev) / len(prev)
            result.evidence["steps_14d_avg"] = round(last_avg)
            result.evidence["steps_prev14d_avg"] = round(prev_avg)
            if prev_avg > 0 and 1 - last_avg / prev_avg >= rules["steps_drop_ratio"]:
                result.raise_to("L1", "steps_drop")

    if result.level == "L2" and _in_scope(scope, "nhi", "visits"):
        last_visit = hd.last_encounter(session, user_id)
        result.evidence["last_visit"] = last_visit.date().isoformat() if last_visit else None
        if last_visit is None or last_visit < now - timedelta(
            days=int(rules["l3_no_revisit_days"])
        ):
            result.raise_to("L3", "no_revisit_30d")
    return result
