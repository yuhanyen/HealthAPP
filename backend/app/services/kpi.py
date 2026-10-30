"""計畫 KPI（SPEC §9 /agency/kpi、§14 M7）：只讀 anon（全區 'ALL' 列），經 k-匿名中介軟體。

本期 vs 上期：period=week 為最近一個完整週 vs 前一週；period=month 為最近 4 個完整週 vs 前 4 週
（以人數加權平均）。本週尚未結束，不納入。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from sqlalchemy import Connection, text

from app.deidentify.k_anon import execute_k_anonymous
from app.i18n import t

KPI_METRICS = (
    "bp_uncontrolled_rate",
    "measure_freq",
    "site_usage",
    "steps_avg",
    "steps_goal_rate",
    "redeem_rate",
)
# 指標越低越好的（其餘越高越好），用來判斷「改善／惡化」
LOWER_IS_BETTER = frozenset({"bp_uncontrolled_rate", "bp_high_rate"})
# 計畫目標（示範值，可由 sys.config 的 kpi_targets 覆寫）
DEFAULT_TARGETS: dict[str, float] = {
    "bp_uncontrolled_rate": 0.40,
    "measure_freq": 0.20,
    "site_usage": 0.15,
    "steps_avg": 6000,
    "steps_goal_rate": 0.50,
    "redeem_rate": 0.10,
    "retention_7": 0.60,
    "push_open_rate": 0.50,
    "elder_mode_rate": 0.30,
}
APP_METRICS = ("dau", "mau", "retention_7", "retention_30", "push_open_rate", "elder_mode_rate")

KPI_SQL = """
SELECT metric, week, value, n FROM anon.cohort_weekly
WHERE village_code = 'ALL' AND age_band = 'ALL' AND sex = 'ALL'
  AND metric = ANY(CAST(:metrics AS text[]))
  AND week <= (SELECT max(week) FROM anon.cohort_weekly) - 7
  AND week > (SELECT max(week) FROM anon.cohort_weekly) - 7 - :weeks * 7
ORDER BY metric, week
"""


def _weighted(rows: list[dict[str, Any]]) -> tuple[float | None, int]:
    usable = [r for r in rows if not r["_masked"] and r["value"] is not None]
    n = sum(int(r["n"]) for r in usable)
    if not n:
        return None, 0
    return sum(float(r["value"]) * int(r["n"]) for r in usable) / n, n


def status(metric: str, current: float | None, target: float | None, direction: str) -> str:
    """達標／未達標；達標但趨勢惡化標「需關注」。"""
    if current is None or target is None:
        return "unknown"
    met = current <= target if metric in LOWER_IS_BETTER else current >= target
    if not met:
        return "missed"
    return "watch" if direction == "worse" else "met"


APP_SQL = """
SELECT day, dau, mau, retention_7, retention_30, push_open_rate, elder_mode_rate
FROM anon.app_metrics_daily ORDER BY day DESC LIMIT :days
"""


def app_kpis(
    conn: Connection, *, period: Literal["week", "month"], targets: dict[str, float]
) -> list[dict[str, Any]]:
    """App 使用指標：最近一天 vs 7 天（或 28 天）前。

    anon.app_metrics_daily 只在月活躍人數 ≥ k 時有列。
    """
    span = 7 if period == "week" else 28
    rows = [dict(r) for r in conn.execute(text(APP_SQL), {"days": span + 1}).mappings()]
    if not rows:
        return []
    latest, earlier = rows[0], rows[-1] if len(rows) > span else None
    items = []
    for metric in APP_METRICS:
        current = float(latest[metric])
        previous = float(earlier[metric]) if earlier else None
        direction = "stable"
        if previous:
            change = (current - previous) / abs(previous)
            if abs(change) >= 0.03:
                direction = "better" if change > 0 else "worse"
        items.append(
            {
                "metric": metric,
                "label": t(f"kpi.app.{metric}"),
                "current": round(current, 4),
                "previous": None if previous is None else round(previous, 4),
                "target": targets.get(metric),
                "direction": direction,
                "status": status(metric, current, targets.get(metric), direction),
                "period_end": str(latest["day"]),
            }
        )
    return items


def kpis(
    conn: Connection,
    *,
    k: int,
    period: Literal["week", "month"] = "week",
    targets: dict[str, float] | None = None,
) -> dict[str, Any]:
    targets = {**DEFAULT_TARGETS, **(targets or {})}
    span = 1 if period == "week" else 4
    result = execute_k_anonymous(
        conn, KPI_SQL, {"metrics": list(KPI_METRICS), "weeks": span * 2}, k=k
    )
    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.rows:
        by_metric[row["metric"]].append(row)
    items = []
    for metric in KPI_METRICS:
        rows = sorted(by_metric.get(metric, []), key=lambda r: r["week"])
        current, n = _weighted(rows[-span:])
        previous, _ = _weighted(rows[-2 * span : -span])
        direction = "stable"
        if current is not None and previous is not None and previous:
            change = (current - previous) / abs(previous)
            if abs(change) >= 0.03:
                better = (change < 0) if metric in LOWER_IS_BETTER else (change > 0)
                direction = "better" if better else "worse"
        items.append(
            {
                "metric": metric,
                "label": t(f"metric.{metric}"),
                "current": None if current is None else round(current, 4),
                "previous": None if previous is None else round(previous, 4),
                "n": n,
                "target": targets.get(metric),
                "direction": direction,
                "status": status(metric, current, targets.get(metric), direction),
                "period_end": str(rows[-1]["week"]) if rows else None,
            }
        )
    return {
        "period": period,
        "k": k,
        "masked_rows": result.masked_rows,
        "items": items,
        "app": app_kpis(conn, period=period, targets=targets),
    }
