"""health_summary（SPEC §17.3）：民眾健康儀表板與月報的資料（期間統計、與前期比較、同齡比較）。

- 只回授權範圍內的指標；沒授權的列在 not_authorized（前端提示去授權）。
- 步數只算到昨天（今天還沒走完）；量測類（血壓、體重、血糖）含今天。
- 同齡比較只用 anon.cohort_weekly（services/peer，經 k-匿名中介軟體；被遮蔽就不比較）。
- 文字一律經 C-7 過濾；只描述數字，不做疾病判斷。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select

from app.db.models_idn import EventRegistration, PointsLedger
from app.fhir import codes
from app.i18n import t
from app.services import health_data as hd
from app.services import peer
from app.sysconfig import cached_value
from app.tools._common import md, profile, safe_all, today, tz
from app.tools.registry import CitizenToolArgs, ToolContext, registry

PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90}
BP_REFERENCE = (140, 90)
METRIC_SCOPE = {
    "steps": ("wearable", "steps"),
    "heart_rate": ("wearable", "heart_rate"),
    "bp": ("site", "bp"),
    "weight": ("site", "weight"),
    "glucose": ("site", "glucose"),
}


class HealthSummaryArgs(CitizenToolArgs):
    period: Literal["week", "month", "quarter"] = "month"


def _start(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tz())


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(current: float | None, previous: float | None) -> int | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous * 100)


def _steps(ctx: ToolContext, args: HealthSummaryArgs, start: date, end: date) -> dict[str, Any]:
    days = (end - start).days
    daily = hd.daily_steps(ctx.db, args.user_id, start, end)
    before = hd.daily_steps(ctx.db, args.user_id, start - timedelta(days=days), start)
    goal = int(cached_value("step_goal_default", 6000))
    avg, prev = _avg(list(map(float, daily.values()))), _avg(list(map(float, before.values())))
    best = max(daily.items(), key=lambda kv: kv[1]) if daily else None
    band = profile(ctx.db, args.user_id).age_band
    peer_value = peer.cohort_value("steps_avg", band)
    goal_days = sum(v >= goal for v in daily.values())
    out: dict[str, Any] = {
        "series": [{"date": d.isoformat(), "value": v} for d, v in sorted(daily.items())],
        "avg": round(avg) if avg is not None else None,
        "prev_avg": round(prev) if prev is not None else None,
        "change_pct": _pct(avg, prev),
        "goal": goal,
        "goal_days": goal_days,
        "days_with_data": len(daily),
        "best": {"date": best[0].isoformat(), "value": best[1]} if best else None,
        "peer_avg": int(peer_value) if peer_value is not None else None,
        "peer_band": band,
    }
    if avg is not None:
        out["text"] = t(
            "health.line.steps",
            avg=f"{round(avg):,}",
            goal_days=goal_days,
            days=len(daily),
            goal=f"{goal:,}",
        )
    return out


def _bp(ctx: ToolContext, args: HealthSummaryArgs, start: date, end: date) -> dict[str, Any]:
    days = (end - start).days
    readings = hd.bp_readings(ctx.db, args.user_id, since=_start(start), limit=200)
    readings = [r for r in readings if r.at < _start(end)][::-1]
    previous = [
        r
        for r in hd.bp_readings(ctx.db, args.user_id, since=_start(start - timedelta(days=days)))
        if r.at < _start(start)
    ]
    high = sum(r.high for r in readings)
    out: dict[str, Any] = {
        "series": [
            {
                "at": r.at.isoformat(),
                "systolic": r.systolic,
                "diastolic": r.diastolic,
                "site": r.site,
            }
            for r in readings
        ],
        "count": len(readings),
        "reference": list(BP_REFERENCE),
        "high": high,
        "in_range": len(readings) - high,
    }
    if readings:
        s = round(sum(r.systolic for r in readings) / len(readings))
        d = round(sum(r.diastolic for r in readings) / len(readings))
        out["avg"] = [s, d]
        out["latest"] = {
            "at": readings[-1].at.isoformat(),
            "value": [readings[-1].systolic, readings[-1].diastolic],
        }
        out["text"] = t("health.line.bp", n=len(readings), s=s, d=d, high=high)
    if previous:
        out["prev_avg"] = [
            round(sum(r.systolic for r in previous) / len(previous)),
            round(sum(r.diastolic for r in previous) / len(previous)),
        ]
    return out


def _single(
    ctx: ToolContext, args: HealthSummaryArgs, code: str, start: date, end: date, key: str
) -> dict[str, Any]:
    readings = hd.observations(ctx.db, args.user_id, code, since=_start(start), until=_start(end))
    values = [r.value for r in readings]
    out: dict[str, Any] = {
        "series": [{"at": r.at.isoformat(), "value": r.value} for r in readings],
        "count": len(readings),
    }
    if readings:
        out["avg"] = round(sum(values) / len(values), 1)
        out["latest"] = {"at": readings[-1].at.isoformat(), "value": readings[-1].value}
        out["change"] = round(values[-1] - values[0], 1) if len(values) > 1 else None
        out["text"] = t(
            f"health.line.{key}",
            n=len(readings),
            latest=f"{values[-1]:g}",
            date=md(readings[-1].at),
            avg=f"{out['avg']:g}",
        )
    return out


def _bmi(ctx: ToolContext, args: HealthSummaryArgs, weight: dict[str, Any]) -> float | None:
    height = hd.latest_observation(ctx.db, args.user_id, codes.BODY_HEIGHT)
    if not height or "latest" not in weight or height[0] <= 0:
        return None
    metres = height[0] / 100
    return round(float(weight["latest"]["value"]) / (metres * metres), 1)


@registry.tool(name="health_summary", schema=HealthSummaryArgs, side="citizen")
def health_summary(ctx: ToolContext, args: HealthSummaryArgs) -> dict[str, Any]:
    """本人一段期間（近 7／30／90 天）的健康統計：各指標時序、平均、達標、與前期及同齡比較。"""
    scope = args.consent_scope
    days = PERIOD_DAYS[args.period]
    end = today() + timedelta(days=1)  # 不含明天；量測含今天
    start = end - timedelta(days=days)
    allowed = {m for m, (src, item) in METRIC_SCOPE.items() if item in scope.get(src, [])}

    metrics: dict[str, Any] = {}
    if "steps" in allowed:
        metrics["steps"] = _steps(ctx, args, start - timedelta(days=1), end - timedelta(days=1))
    if "heart_rate" in allowed:
        hr = hd.daily_heart_rate(ctx.db, args.user_id, start, end - timedelta(days=1))
        avg = _avg(list(map(float, hr.values())))
        metrics["heart_rate"] = {
            "series": [{"date": d.isoformat(), "value": v} for d, v in sorted(hr.items())],
            "avg": round(avg) if avg is not None else None,
            **({"text": t("health.line.heart_rate", avg=round(avg))} if avg is not None else {}),
        }
    if "bp" in allowed:
        metrics["bp"] = _bp(ctx, args, start, end)
    if "weight" in allowed:
        weight = _single(ctx, args, codes.BODY_WEIGHT, start, end, "weight")
        if "height" in scope.get("site", []):
            weight["bmi"] = _bmi(ctx, args, weight)
        metrics["weight"] = weight
    if "glucose" in allowed:
        metrics["glucose"] = _single(ctx, args, codes.GLUCOSE, start, end, "glucose")

    earned = ctx.db.scalar(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(
            PointsLedger.user_id == args.user_id,
            PointsLedger.delta > 0,
            PointsLedger.created_at >= _start(start),
        )
    )
    attended = ctx.db.scalar(
        select(func.count())
        .select_from(EventRegistration)
        .where(
            EventRegistration.user_id == args.user_id,
            EventRegistration.status == "attended",
            EventRegistration.checked_in_at >= _start(start),
        )
    )
    lines = [m["text"] for m in metrics.values() if m.get("text")]
    return safe_all(
        {
            "period": args.period,
            "days": days,
            "start": start.isoformat(),
            "end": (end - timedelta(days=1)).isoformat(),
            "title": t(f"health.title.{args.period}"),
            "metrics": metrics,
            "not_authorized": sorted(set(METRIC_SCOPE) - allowed),
            "activity": {"points_earned": int(earned or 0), "events_attended": int(attended or 0)},
            "lines": lines,
            "note": t("health.note"),
        }
    )
