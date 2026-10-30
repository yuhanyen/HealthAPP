"""schema_catalog（SPEC §8.2 A3）：去識別化區的資料表與指標說明，給 A3 寫 SQL 用。"""

from __future__ import annotations

from typing import Any

from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry

CATALOG: dict[str, Any] = {
    "anon.cohort_weekly": {
        "columns": "week date（週一）, village_code text, age_band text（<20、20-29…80+）, "
        "sex text（F/M/X）, metric text, value numeric, n int",
        "notes": "village_code／age_band／sex 為 'ALL' 的列是加總列。n<k 的格子不存在（已遮蔽）。"
        "聚合時請輸出 sum(n) AS n，並以 sum(value*n)/sum(n) 計加權平均。",
        "metrics": {
            "steps_avg": "每日步數平均",
            "steps_goal_rate": "步數達標（≥6000）天數比例",
            "bp_uncontrolled_rate": "高血壓者近 4 週最近一次血壓 ≥140/90 的比例",
            "bp_high_rate": "有量測者近 4 週最近一次血壓 ≥140/90 的比例",
            "measure_freq": "據點授權者當週量測次數平均",
            "site_usage": "據點授權者當週有到據點量測的比例",
            "redeem_rate": "商家授權者當週有兌換點數的比例",
            "event_attend_rate": "有 App 帳號者當週至少報到一場社區活動的比例",
        },
    },
    "anon.site_ops_weekly": {
        "columns": "week, site_id, visits, bookings, no_show",
        "notes": "到訪人數 ≥ k 才有列",
    },
    "anon.merchant_weekly": {
        "columns": "week, merchant_id, redemptions, points",
        "notes": "兌換人數 ≥ k 才有列",
    },
    "anon.visit_outcomes": {"columns": "week, village_code, task_type, reason_class, n"},
    "anon.sentiment_items": {
        "columns": "id, collected_at, source_class, source_url, village_code, "
        "topic, sentiment, intensity, summary",
        "notes": "已去除帳號、名稱、頭像、@提及、電話、Email",
    },
    "anon.sentiment_alerts": {
        "columns": "id, village_code, topic, category, count, baseline, ratio, created_at, status"
    },
    "pub.villages": {"columns": "code, name, district, population, geom（GeoJSON）"},
    "pub.sites": {"columns": "id, name, type, village_code, lat, lng, hours, equipment, active"},
}


class CatalogArgs(AgencyToolArgs):
    pass


@registry.tool(name="schema_catalog", schema=CatalogArgs, side="agency")
def schema_catalog(ctx: ToolContext, args: CatalogArgs) -> dict[str, Any]:
    """列出可查詢的去識別化資料表、欄位與指標定義。"""
    return {"tables": CATALOG}
