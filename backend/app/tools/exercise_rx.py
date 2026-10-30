"""exercise_rx（SPEC §7.1）：依就醫紀錄與環境給運動處方。

- 有心血管、糖尿病、腎病的就醫紀錄（需 nhi.conditions 授權）或風險 ≥ L2 → 保守版本
- AQI > 100、熱指數 > 32°C 或下雨 → 室內
- 步數目標 3,000–10,000（預設 6,000），依近兩週步數調整
不給高強度建議（SKILL：高強度需本人確認，一期一律不出）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.config import get_settings
from app.db.models_pub import EnvHourly
from app.fhir import codes
from app.i18n import t, t_list
from app.services import health_data as hd
from app.services import risk
from app.sysconfig import cached_value
from app.tools._common import profile, safe_all, today
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class EnvInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aqi: int | None = None
    heat_index: float | None = None
    rain: bool | None = None


class ExerciseRxArgs(CitizenToolArgs):
    env: EnvInput | None = None


def _latest_env(ctx: ToolContext) -> EnvInput:
    row = ctx.db.scalars(
        select(EnvHourly)
        .where(EnvHourly.district == get_settings().district)
        .order_by(EnvHourly.hour.desc())
        .limit(1)
    ).first()
    if row is None:
        return EnvInput()
    return EnvInput(aqi=row.aqi, heat_index=float(row.heat_index), rain=row.rain)


@registry.tool(name="exercise_rx", schema=ExerciseRxArgs, side="citizen")
def exercise_rx(ctx: ToolContext, args: ExerciseRxArgs) -> dict[str, Any]:
    """產生今天的運動處方：強度、時間、場地、注意事項與每日步數目標。"""
    scope = args.consent_scope
    day = today()
    me = profile(ctx.db, args.user_id, day)
    env = args.env or _latest_env(ctx)

    reasons: list[str] = []
    chronic = False
    if "conditions" in scope.get("nhi", []):
        found = hd.condition_codes(ctx.db, args.user_id)
        chronic = any(
            hd.has_condition(found, group)
            for group in (codes.CARDIOVASCULAR, codes.DIABETES, codes.KIDNEY, codes.HYPERTENSION)
        )
    level = risk.evaluate(ctx.db, args.user_id, scope, day).level
    conservative = chronic or level in ("L2", "L3") or me.age >= 75
    if conservative:
        reasons.append(t("rx.reason.conservative"))

    indoor = False
    if env.aqi is not None and env.aqi > 100:
        indoor = True
        reasons.append(t("rx.reason.aqi", aqi=env.aqi))
    if env.heat_index is not None and env.heat_index > 32:
        indoor = True
        reasons.append(t("rx.reason.heat", heat=env.heat_index))
    if env.rain:
        indoor = True
        reasons.append(t("rx.reason.rain"))

    goal = int(cached_value("step_goal_default", 6000))
    if "steps" in scope.get("wearable", []):
        recent = hd.daily_steps(ctx.db, args.user_id, day - timedelta(days=14), day)
        if recent:
            avg = sum(recent.values()) / len(recent)
            goal = int(
                min(6000, max(3000, avg * 1.1))
                if conservative
                else min(10000, max(6000, avg * 1.1))
            )
            goal = round(goal, -2)

    intensity = "low" if conservative else "moderate"
    venue = "indoor" if indoor else "outdoor"
    minutes = 15 if conservative else 30
    return safe_all(
        {
            "intensity": intensity,
            "minutes": minutes,
            "venue": venue,
            "step_goal": goal,
            "plan": t(
                "rx.plan",
                venue=t(f"rx.venue.{venue}"),
                intensity=t(f"rx.intensity.{intensity}"),
                minutes=minutes,
                example=t(f"rx.example.{venue}"),
            ),
            "contraindications": t_list(
                f"rx.contra.{'conservative' if conservative else 'standard'}"
            ),
            "basis": reasons,
            "env": env.model_dump(),
        }
    )
