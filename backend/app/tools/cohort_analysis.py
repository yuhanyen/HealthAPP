"""cohort_analysis（SPEC §7.2）：去識別化群體指標的時序與熱區（GeoJSON）。

只讀 anon＋pub，經 k-匿名。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.deidentify.aggregate import METRICS
from app.deidentify.k_anon import execute_k_anonymous
from app.sysconfig import cached_value
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry

SERIES_SQL = """
SELECT c.week, c.village_code, c.value, c.n FROM anon.cohort_weekly c
WHERE c.metric = :metric AND c.age_band = 'ALL' AND c.sex = 'ALL'
  AND (cardinality(CAST(:villages AS text[])) = 0 OR c.village_code = ANY(CAST(:villages AS text[]))
       OR c.village_code = 'ALL')
  AND c.week >= (SELECT max(week) FROM anon.cohort_weekly WHERE metric = :metric) - (:weeks - 1) * 7
ORDER BY c.week, c.village_code
"""

HOTSPOT_SQL = """
SELECT v.code AS village_code, v.name, v.geom, c.value, c.n
FROM pub.villages v
JOIN anon.cohort_weekly c ON c.village_code = v.code AND c.metric = :metric
 AND c.age_band = 'ALL' AND c.sex = 'ALL'
 AND c.week = (SELECT max(week) FROM anon.cohort_weekly WHERE metric = :metric) - 7
ORDER BY v.code
"""


class CohortArgs(AgencyToolArgs):
    village_codes: list[str] = Field(default_factory=list, description="空陣列＝全部里")
    metrics: list[str] = Field(default_factory=lambda: ["steps_avg"])
    weeks: int = Field(default=12, ge=1, le=52)


@registry.tool(name="cohort_analysis", schema=CohortArgs, side="agency")
def cohort_analysis(ctx: ToolContext, args: CohortArgs) -> dict[str, Any]:
    """群體指標時序（各里與 ALL 全區）與最近一個完整週的熱區 GeoJSON。
    可用指標：steps_avg、steps_goal_rate、bp_uncontrolled_rate、bp_high_rate、measure_freq、site_usage、
    redeem_rate、event_attend_rate。"""
    k = int(cached_value("k", 5))
    out: dict[str, Any] = {"k": k, "metrics": {}}
    masked = 0
    for metric in args.metrics:
        if metric not in METRICS:
            raise ValueError(f"未知指標：{metric}")
        series = execute_k_anonymous(
            ctx.db,
            SERIES_SQL,
            {"metric": metric, "villages": args.village_codes, "weeks": args.weeks},
            k=k,
        )
        spots = execute_k_anonymous(ctx.db, HOTSPOT_SQL, {"metric": metric}, k=k)
        masked += series.masked_rows + spots.masked_rows
        by_village: dict[str, list[dict[str, Any]]] = {}
        for row in series.rows:
            by_village.setdefault(row["village_code"], []).append(
                {"week": row["week"].isoformat(), "value": _num(row["value"]), "n": row["n"]}
            )
        out["metrics"][metric] = {
            "series": by_village,
            "hotspots": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": r["geom"],
                        "properties": {
                            "village_code": r["village_code"],
                            "name": r["name"],
                            "value": _num(r["value"]),
                            "n": r["n"],
                            "masked": r["_masked"],
                        },
                    }
                    for r in spots.rows
                ],
            },
        }
    out["masked_rows"] = masked
    return out


def _num(value: Any) -> float | None:
    return None if value is None else float(value)
