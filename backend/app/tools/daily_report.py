"""daily_report（SPEC §7.1）：今日／上週／同齡的指標表、白話解讀、語音稿。

同齡比較只用 anon.cohort_weekly（另開 anon_reader 連線、經 k-匿名中介軟體；被遮蔽就不比較）。
只讀授權範圍內的項目；所有文字經 C-7 輸出過濾。資料涵蓋到「昨天」（05:00 批次產生）。
"""

from __future__ import annotations

from datetime import date, timedelta
from datetime import date as Date
from typing import Any

from app.fhir import codes
from app.i18n import t
from app.services import health_data as hd
from app.services import peer, risk
from app.tools._common import md, profile, safe, safe_all, site_name, today
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class DailyReportArgs(CitizenToolArgs):
    date: Date | None = None


def peer_steps(band: str) -> int | None:
    value = peer.cohort_value("steps_avg", band)
    return int(value) if value is not None else None


def _steps_line(ctx: ToolContext, args: DailyReportArgs, day: date, band: str) -> dict[str, Any]:
    daily = hd.daily_steps(ctx.db, args.user_id, day - timedelta(days=8), day + timedelta(days=1))
    value = daily.get(day)
    if value is None:
        return {"metric": "steps", "value": None, "text": f"步數：{t('report.no_data')}"}
    week = [v for d, v in daily.items() if d < day]
    parts = []
    if week:
        avg = sum(week) / len(week)
        pct = round(abs(value - avg) / avg * 100) if avg else 0
        direction = t("report.more") if value >= avg else t("report.less")
        parts.append(t("report.steps_compare_week", direction=direction, pct=pct))
    peer = peer_steps(band)
    if peer:
        parts.append(t("report.steps_compare_peer", peer=f"{peer:,}"))
    return {
        "metric": "steps",
        "value": value,
        "last_week_avg": round(sum(week) / len(week)) if week else None,
        "peer_avg": peer,
        "text": t(
            "report.steps", value=f"{value:,}", compare="；".join(parts) or "—", date=md(day)
        ),
    }


def _bp_line(ctx: ToolContext, args: DailyReportArgs) -> dict[str, Any] | None:
    readings = hd.bp_readings(ctx.db, args.user_id, limit=3)
    if not readings:
        return None
    last = readings[0]
    streak = 0
    for r in readings:
        if not r.high:
            break
        streak += 1
    if streak >= 2:
        note = t("report.bp_note_high_streak", n=streak)
    else:
        note = t("report.bp_note_high") if last.high else t("report.bp_note_ok")
    return {
        "metric": "bp",
        "value": [last.systolic, last.diastolic],
        "at": last.at.isoformat(),
        "text": t(
            "report.bp",
            s=last.systolic,
            d=last.diastolic,
            note=note,
            site=site_name(ctx.db, last.site),
            date=md(last.at),
        ),
    }


@registry.tool(name="daily_report", schema=DailyReportArgs, side="citizen")
def daily_report(ctx: ToolContext, args: DailyReportArgs) -> dict[str, Any]:
    """產生本人的每日健康報告：指標（昨天、前一週平均、同齡平均）、狀態一句話、語音稿。"""
    report_day = args.date or today()
    data_day = report_day - timedelta(days=1)
    scope = args.consent_scope
    me = profile(ctx.db, args.user_id, report_day)

    metrics: list[dict[str, Any]] = []
    if "steps" in scope.get("wearable", []):
        metrics.append(_steps_line(ctx, args, data_day, me.age_band))
    if "bp" in scope.get("site", []):
        bp = _bp_line(ctx, args)
        if bp:
            metrics.append(bp)
    if "weight" in scope.get("site", []):
        weight = hd.latest_observation(ctx.db, args.user_id, codes.BODY_WEIGHT)
        if weight:
            metrics.append(
                {
                    "metric": "weight",
                    "value": weight[0],
                    "at": weight[1].isoformat(),
                    "text": t("report.weight", value=weight[0], site="據點", date=md(weight[1])),
                }
            )

    result = risk.evaluate(ctx.db, args.user_id, scope, report_day)
    status = t(f"report.status.{result.level}")
    voice = " ".join(
        [t("report.voice_intro"), status, *[m["text"].split("　依據")[0] for m in metrics]]
    )
    return safe_all(
        {
            "date": report_day.isoformat(),
            "title": t("report.title", date=f"{report_day.month}/{report_day.day}"),
            "status_sentence": safe(status),
            "risk": {"level": result.level, "triggers": result.triggers},
            "metrics": metrics,
            "voice_script": voice,
            "elder_mode": me.elder_mode,
        }
    )
