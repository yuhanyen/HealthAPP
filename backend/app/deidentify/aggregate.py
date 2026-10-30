"""deidentify 批次（SPEC §4.4）：idn → anon 的唯一單向通道，每日 05:00（daily.batch）。

做法：在 idn 端以一條 SQL（GROUPING SETS）算出各格聚合，個人層級資料不離開資料庫；
回到應用層只剩（週, 里, 年齡層, 性別, 指標, 值, 人數）。經 suppress（主要＋次要遮蔽）後，
用另一條 anon_writer 連線寫入 anon。兩條連線、兩個角色，沒有任何 SQL 同時碰兩區。

聚合鍵：週（週一起算）、里、年齡層（10 歲一組，<20、80+）、性別；另發布 'ALL' 加總列。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.audit.writer import write_audit
from app.config import get_settings
from app.db.engines import DbRole, session_scope
from app.db.models_anon import (
    AppMetricsDaily,
    CohortWeekly,
    MerchantWeekly,
    SiteOpsWeekly,
    VisitOutcome,
)
from app.deidentify.suppress import ALL, Cell, suppress
from app.fhir import codes
from app.sysconfig import get_k, get_value

log = logging.getLogger(__name__)

# 指標 → (平均欄, 人數欄, 小數位)
METRICS: dict[str, tuple[str, str, int]] = {
    "steps_avg": ("steps_avg", "steps_n", 0),
    "steps_goal_rate": ("goal_avg", "goal_n", 4),
    "bp_uncontrolled_rate": ("unc_avg", "unc_n", 4),
    "bp_high_rate": ("high_avg", "high_n", 4),
    "measure_freq": ("mf_avg", "mf_n", 3),
    "site_usage": ("su_avg", "su_n", 4),
    "redeem_rate": ("rd_avg", "rd_n", 4),
    # M8：有 App 帳號者中，當週至少報到一場社區活動的比例（SPEC §17.1）
    "event_attend_rate": ("ea_avg", "ea_n", 4),
}

COHORT_SQL = """
WITH wk AS (SELECT CAST(w AS date) AS week FROM unnest(CAST(:weeks AS date[])) AS w),
members AS (
  SELECT u.id AS user_id, u.village_code, u.sex, wk.week,
         CASE WHEN a.age < 20 THEN '<20' WHEN a.age >= 80 THEN '80+'
              ELSE (a.age / 10 * 10)::text || '-' || (a.age / 10 * 10 + 9)::text END AS age_band
  FROM idn.users u
  CROSS JOIN wk
  CROSS JOIN LATERAL (SELECT date_part('year', age(wk.week + 6, u.birth_date))::int AS age) a
),
consented AS (
  SELECT user_id, source FROM idn.consents WHERE revoked_at IS NULL AND superseded_at IS NULL
),
steps_day AS (
  SELECT f.user_id, (f.effective_at AT TIME ZONE :tz)::date AS d,
         max(CASE WHEN f.code = :steps_daily
                  THEN (f.resource->'valueQuantity'->>'value')::numeric END) AS summary,
         sum(CASE WHEN f.code = :steps_hourly
                  THEN (f.resource->'valueQuantity'->>'value')::numeric END) AS hourly
  FROM idn.fhir_resources f
  WHERE f.source = 'wearable' AND f.code IN (:steps_daily, :steps_hourly)
    AND f.effective_at >= :start AND f.effective_at < :end
  GROUP BY 1, 2
),
steps_week AS (
  SELECT user_id, date_trunc('week', d)::date AS week,
         avg(coalesce(summary, hourly)) AS steps,
         avg(CASE WHEN coalesce(summary, hourly) >= :goal THEN 1.0 ELSE 0.0 END) AS goal_rate
  FROM steps_day WHERE d < :today GROUP BY 1, 2
),
bp AS (
  SELECT f.user_id, f.effective_at,
         max(CASE WHEN c->'code'->'coding'->0->>'code' = :bp_sys
                  THEN (c->'valueQuantity'->>'value')::numeric END) AS sys,
         max(CASE WHEN c->'code'->'coding'->0->>'code' = :bp_dia
                  THEN (c->'valueQuantity'->>'value')::numeric END) AS dia
  FROM idn.fhir_resources f
  CROSS JOIN LATERAL jsonb_array_elements(f.resource->'component') c
  WHERE f.source = 'site' AND f.code = :bp_panel
    AND f.effective_at >= :start_bp AND f.effective_at < :end
  GROUP BY f.id, f.user_id, f.effective_at
),
bp_latest AS (
  SELECT DISTINCT ON (m.user_id, m.week) m.user_id, m.week, (bp.sys >= 140 OR bp.dia >= 90) AS high
  FROM members m
  JOIN bp ON bp.user_id = m.user_id
   AND bp.effective_at < ((m.week + 7)::timestamp AT TIME ZONE :tz)
   AND bp.effective_at >= ((m.week - 21)::timestamp AT TIME ZONE :tz)
  ORDER BY m.user_id, m.week, bp.effective_at DESC
),
bp_week_count AS (
  SELECT m.user_id, m.week, count(bp.user_id) AS cnt
  FROM members m
  LEFT JOIN bp ON bp.user_id = m.user_id
   AND bp.effective_at >= (m.week::timestamp AT TIME ZONE :tz)
   AND bp.effective_at < ((m.week + 7)::timestamp AT TIME ZONE :tz)
  GROUP BY 1, 2
),
htn AS (
  SELECT DISTINCT user_id FROM idn.fhir_resources
  WHERE source = 'nhi' AND resource_type = 'Condition' AND left(code, 3) = ANY(:htn)
),
redeemed AS (
  SELECT DISTINCT r.user_id, date_trunc('week', r.redeemed_at AT TIME ZONE :tz)::date AS week
  FROM idn.redemptions r WHERE r.redeemed_at >= :start AND r.redeemed_at < :end
),
app_members AS (
  SELECT DISTINCT user_id FROM sys.accounts
  WHERE role = 'citizen' AND active AND user_id IS NOT NULL
),
attended AS (
  SELECT DISTINCT e.user_id, date_trunc('week', e.checked_in_at AT TIME ZONE :tz)::date AS week
  FROM idn.event_registrations e
  WHERE e.status = 'attended' AND e.checked_in_at >= :start AND e.checked_in_at < :end
),
facts AS (
  SELECT m.week, m.village_code, m.age_band, m.sex,
         sw.steps, sw.goal_rate,
         CASE WHEN h.user_id IS NOT NULL AND bl.high IS NOT NULL THEN bl.high::int END AS unc,
         bl.high::int AS high,
         CASE WHEN cs.user_id IS NOT NULL THEN bc.cnt END AS mf,
         CASE WHEN cs.user_id IS NOT NULL THEN (bc.cnt > 0)::int END AS su,
         CASE WHEN cm.user_id IS NOT NULL THEN (rd.user_id IS NOT NULL)::int END AS rd,
         CASE WHEN am.user_id IS NOT NULL THEN (ev.user_id IS NOT NULL)::int END AS ea
  FROM members m
  LEFT JOIN steps_week sw ON sw.user_id = m.user_id AND sw.week = m.week
  LEFT JOIN bp_latest bl ON bl.user_id = m.user_id AND bl.week = m.week
  LEFT JOIN htn h ON h.user_id = m.user_id
  LEFT JOIN bp_week_count bc ON bc.user_id = m.user_id AND bc.week = m.week
  LEFT JOIN consented cs ON cs.user_id = m.user_id AND cs.source = 'site'
  LEFT JOIN consented cm ON cm.user_id = m.user_id AND cm.source = 'merchant'
  LEFT JOIN redeemed rd ON rd.user_id = m.user_id AND rd.week = m.week
  LEFT JOIN app_members am ON am.user_id = m.user_id
  LEFT JOIN attended ev ON ev.user_id = m.user_id AND ev.week = m.week
)
SELECT week,
       CASE WHEN GROUPING(village_code) = 1 THEN 'ALL' ELSE village_code END AS village_code,
       CASE WHEN GROUPING(age_band) = 1 THEN 'ALL' ELSE age_band END AS age_band,
       CASE WHEN GROUPING(sex) = 1 THEN 'ALL' ELSE sex END AS sex,
       avg(steps) AS steps_avg, count(steps) AS steps_n,
       avg(goal_rate) AS goal_avg, count(goal_rate) AS goal_n,
       avg(unc) AS unc_avg, count(unc) AS unc_n,
       avg(high) AS high_avg, count(high) AS high_n,
       avg(mf) AS mf_avg, count(mf) AS mf_n,
       avg(su) AS su_avg, count(su) AS su_n,
       avg(rd) AS rd_avg, count(rd) AS rd_n,
       avg(ea) AS ea_avg, count(ea) AS ea_n
FROM facts
GROUP BY GROUPING SETS ((week, village_code, age_band, sex), (week, village_code),
                        (week, age_band), (week, sex), (week))
"""

SITE_OPS_SQL = """
WITH visits AS (
  SELECT f.resource->'performer'->0->'identifier'->>'value' AS site_id,
         date_trunc('week', f.effective_at AT TIME ZONE :tz)::date AS week,
         count(DISTINCT f.user_id) AS persons,
         count(DISTINCT (f.user_id, (f.effective_at AT TIME ZONE :tz)::date)) AS visits
  FROM idn.fhir_resources f
  WHERE f.source = 'site' AND f.code = :bp_panel
    AND f.effective_at >= :start AND f.effective_at < :end
  GROUP BY 1, 2
),
bk AS (
  SELECT site_id, date_trunc('week', slot_at AT TIME ZONE :tz)::date AS week,
         count(*) AS bookings, count(*) FILTER (WHERE status = 'no_show') AS no_show,
         count(DISTINCT user_id) AS bookers
  FROM idn.bookings WHERE slot_at >= :start AND slot_at < :end GROUP BY 1, 2
)
SELECT coalesce(v.site_id, b.site_id) AS site_id, coalesce(v.week, b.week) AS week,
       coalesce(v.visits, 0) AS visits, coalesce(v.persons, 0) AS persons,
       coalesce(b.bookings, 0) AS bookings, coalesce(b.no_show, 0) AS no_show,
       coalesce(b.bookers, 0) AS bookers
FROM visits v FULL JOIN bk b ON b.site_id = v.site_id AND b.week = v.week
WHERE coalesce(v.site_id, b.site_id) IS NOT NULL
"""

MERCHANT_SQL = """
SELECT merchant_id, date_trunc('week', redeemed_at AT TIME ZONE :tz)::date AS week,
       count(*) AS redemptions, sum(points) AS points, count(DISTINCT user_id) AS persons
FROM idn.redemptions
WHERE status IN ('issued', 'verified') AND redeemed_at >= :start AND redeemed_at < :end
GROUP BY 1, 2
"""


VISIT_SQL = """
SELECT date_trunc('week', r.created_at AT TIME ZONE :tz)::date AS week,
       u.village_code, t.task_type, r.reason_class, count(*) AS n
FROM idn.visit_reports r
JOIN idn.care_tasks t ON t.id = r.care_task_id
JOIN idn.users u ON u.id = t.user_id
WHERE r.created_at >= :start AND r.created_at < :end
GROUP BY 1, 2, 3, 4
"""


APP_SQL = """
WITH activity AS (
  SELECT user_id, (opened_at AT TIME ZONE :tz)::date AS day FROM idn.daily_reports
  WHERE opened_at >= :since
  UNION
  SELECT user_id, (created_at AT TIME ZONE :tz)::date FROM idn.chat_messages
  WHERE role = 'user' AND created_at >= :since
  UNION
  SELECT user_id, (read_at AT TIME ZONE :tz)::date FROM idn.notifications WHERE read_at >= :since
),
earlier7 AS (SELECT DISTINCT user_id FROM activity WHERE day BETWEEN :day - 13 AND :day - 7),
recent7 AS (SELECT DISTINCT user_id FROM activity WHERE day BETWEEN :day - 6 AND :day),
earlier30 AS (SELECT DISTINCT user_id FROM activity WHERE day BETWEEN :day - 59 AND :day - 30),
recent30 AS (SELECT DISTINCT user_id FROM activity WHERE day BETWEEN :day - 29 AND :day),
pushes AS (
  SELECT count(*) AS sent, count(read_at) AS opened FROM idn.notifications
  WHERE (created_at AT TIME ZONE :tz)::date = :day AND channel = 'push'
),
app_users AS (
  SELECT u.elder_mode FROM sys.accounts a JOIN idn.users u ON u.id = a.user_id
  WHERE a.role = 'citizen' AND a.active
)
SELECT
  (SELECT count(DISTINCT user_id) FROM activity WHERE day = :day) AS dau,
  (SELECT count(*) FROM recent30) AS mau,
  (SELECT count(*) FROM earlier7) AS base7,
  (SELECT count(*) FROM earlier7 e JOIN recent7 r USING (user_id)) AS kept7,
  (SELECT count(*) FROM earlier30) AS base30,
  (SELECT count(*) FROM earlier30 e JOIN recent30 r USING (user_id)) AS kept30,
  (SELECT sent FROM pushes) AS pushes_sent,
  (SELECT opened FROM pushes) AS pushes_opened,
  (SELECT count(*) FROM app_users) AS users,
  (SELECT count(*) FROM app_users WHERE elder_mode) AS elder_users
"""


def _rate(part: int, whole: int) -> Decimal:
    return Decimal(part / whole).quantize(Decimal("0.0001")) if whole else Decimal(0)


def app_metrics(idn: Session, days: Sequence[date], k: int) -> list[dict[str, Any]]:
    """App 使用指標（只有全區總數與比例）；當月活躍人數 < k 的日子不發布。"""
    tz = get_settings().timezone
    since = datetime.combine(min(days) - timedelta(days=60), datetime.min.time(), ZoneInfo(tz))
    out = []
    for day in days:
        r = idn.execute(text(APP_SQL), {"tz": tz, "since": since, "day": day}).mappings().one()
        if r["mau"] < k:
            continue
        out.append(
            {
                "day": day,
                "dau": int(r["dau"]),
                "mau": int(r["mau"]),
                "retention_7": _rate(r["kept7"], r["base7"]),
                "retention_30": _rate(r["kept30"], r["base30"]),
                "push_open_rate": _rate(r["pushes_opened"] or 0, r["pushes_sent"] or 0),
                "elder_mode_rate": _rate(r["elder_users"], r["users"]),
            }
        )
    return out


def visit_outcomes(rows: Sequence[Any], weeks: Sequence[date], k: int) -> list[dict[str, Any]]:
    """訪視原因分佈（週 × 里 × 任務類型 × 原因）。n < k 的里不發布；
    若「全區 − 已發布的里」落在 1..k-1，再遮蔽最小的里（避免用相減推回），全區 ≥ k 才發布。"""
    groups: dict[tuple[date, str, str], dict[str, int]] = defaultdict(dict)
    for r in rows:
        if r["week"] in weeks:
            groups[(r["week"], r["task_type"], r["reason_class"])][r["village_code"]] = int(r["n"])
    out: list[dict[str, Any]] = []
    for (week, task_type, reason), villages in groups.items():
        total = sum(villages.values())
        shown = {v: n for v, n in villages.items() if n >= k}
        while shown and 0 < total - sum(shown.values()) < k:
            shown.pop(min(shown, key=lambda v: shown[v]))
        base = {"week": week, "task_type": task_type, "reason_class": reason}
        out += [{**base, "village_code": v, "n": n} for v, n in shown.items()]
        if total >= k:
            out.append({**base, "village_code": ALL, "n": total})
    return out


@dataclass
class DeidentifyStats:
    weeks: list[str]
    k: int
    cells: int
    published: int
    primary_suppressed: int
    secondary_suppressed: int
    site_rows: int
    merchant_rows: int
    visit_rows: int = 0


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def recent_weeks(today: date, count: int) -> list[date]:
    this_week = week_start(today)
    return [this_week - timedelta(weeks=i) for i in range(count - 1, -1, -1)]


def _params(weeks: Sequence[date], today: date) -> dict[str, Any]:
    tz = ZoneInfo(get_settings().timezone)
    first, last = min(weeks), max(weeks)
    return {
        "weeks": list(weeks),
        "tz": get_settings().timezone,
        "today": today,
        "start": datetime.combine(first, datetime.min.time(), tz),
        "start_bp": datetime.combine(first - timedelta(days=21), datetime.min.time(), tz),
        "end": datetime.combine(last + timedelta(days=7), datetime.min.time(), tz),
        "steps_daily": codes.STEPS_DAILY,
        "steps_hourly": codes.STEPS_HOURLY,
        "bp_panel": codes.BP_PANEL,
        "bp_sys": codes.BP_SYSTOLIC,
        "bp_dia": codes.BP_DIASTOLIC,
        "htn": list(codes.HYPERTENSION),
    }


def cohort_cells(idn: Session, weeks: Sequence[date], today: date, goal: int) -> list[Cell]:
    rows = idn.execute(text(COHORT_SQL), {**_params(weeks, today), "goal": goal}).mappings()
    cells: list[Cell] = []
    for row in rows:
        for metric, (avg_col, n_col, places) in METRICS.items():
            n = int(row[n_col])
            if n == 0 or row[avg_col] is None:
                continue
            cells.append(
                Cell(
                    week=row["week"],
                    village_code=row["village_code"],
                    age_band=row["age_band"],
                    sex=row["sex"],
                    metric=metric,
                    value=round(Decimal(row[avg_col]), places),
                    n=n,
                )
            )
    return cells


def run_deidentify(
    *, weeks: Sequence[date] | None = None, today: date | None = None
) -> DeidentifyStats:
    today = today or datetime.now(ZoneInfo(get_settings().timezone)).date()
    weeks = sorted(weeks or recent_weeks(today, 2))
    params = _params(weeks, today)

    with session_scope(DbRole.IDN_APP) as idn:
        k = get_k(idn)
        goal = int(get_value(idn, "step_goal_default", 6000))
        cells = cohort_cells(idn, weeks, today, goal)
        site_rows = idn.execute(text(SITE_OPS_SQL), params).mappings().all()
        merchant_rows = idn.execute(text(MERCHANT_SQL), params).mappings().all()
        visit_rows = idn.execute(text(VISIT_SQL), params).mappings().all()
        app_days = [today - timedelta(days=d) for d in range(7, 0, -1)]  # 昨天以前的完整日
        app_rows = app_metrics(idn, app_days, k)

    published, stats = suppress(cells, k)
    visits_ok = visit_outcomes(visit_rows, weeks, k)
    site_ok = [
        r
        for r in site_rows
        if r["week"] in weeks and r["persons"] >= k and (r["bookers"] == 0 or r["bookers"] >= k)
    ]
    merchant_ok = [r for r in merchant_rows if r["week"] in weeks and r["persons"] >= k]

    with session_scope(DbRole.ANON_WRITER) as anon:
        anon.execute(delete(CohortWeekly).where(CohortWeekly.week.in_(weeks)))
        anon.execute(delete(SiteOpsWeekly).where(SiteOpsWeekly.week.in_(weeks)))
        anon.execute(delete(MerchantWeekly).where(MerchantWeekly.week.in_(weeks)))
        anon.execute(delete(VisitOutcome).where(VisitOutcome.week.in_(weeks)))
        anon.add_all(VisitOutcome(**row) for row in visits_ok)
        anon.execute(delete(AppMetricsDaily).where(AppMetricsDaily.day.in_(app_days)))
        anon.add_all(AppMetricsDaily(**row) for row in app_rows)
        anon.add_all(
            CohortWeekly(
                week=c.week,
                village_code=c.village_code,
                age_band=c.age_band,
                sex=c.sex,
                metric=c.metric,
                value=c.value,
                n=c.n,
            )
            for c in published
        )
        anon.add_all(
            SiteOpsWeekly(
                week=r["week"],
                site_id=r["site_id"],
                visits=r["visits"],
                bookings=r["bookings"],
                no_show=r["no_show"],
            )
            for r in site_ok
        )
        anon.add_all(
            MerchantWeekly(
                week=r["week"],
                merchant_id=r["merchant_id"],
                redemptions=r["redemptions"],
                points=int(r["points"]),
            )
            for r in merchant_ok
        )

    result = DeidentifyStats(
        weeks=[w.isoformat() for w in weeks],
        k=k,
        cells=stats.total,
        published=stats.published,
        primary_suppressed=stats.primary,
        secondary_suppressed=stats.secondary,
        site_rows=len(site_ok),
        merchant_rows=len(merchant_ok),
        visit_rows=len(visits_ok),
    )
    write_audit(
        None,
        actor_type="system",
        actor_id="deidentify",
        action="deidentify.batch",
        output=asdict(result),
        decision="executed",
        reason=f"{datetime.now(UTC).isoformat()} 聚合 {len(weeks)} 週",
    )
    log.info("deidentify 完成：%s", asdict(result))
    return result
