"""同齡比較：只讀 anon.cohort_weekly（anon_reader 連線、經 k-匿名中介軟體；被遮蔽就不比較）。

民眾側工具（daily_report、health_summary）共用。這裡只碰 anon，不 import 任何 idn 模型。
"""

from __future__ import annotations

from decimal import Decimal

from app.db.engines import DbRole, get_engine
from app.deidentify.k_anon import execute_k_anonymous
from app.sysconfig import cached_value


def cohort_value(metric: str, band: str) -> Decimal | None:
    """全區、同年齡層、男女合計，最近一個完整週的值；被 k 遮蔽或沒有資料時回傳 None。"""
    with get_engine(DbRole.ANON_READER).connect() as conn:
        result = execute_k_anonymous(
            conn,
            "SELECT week, value, n FROM anon.cohort_weekly WHERE metric = :metric "
            "AND village_code = 'ALL' AND age_band = :band AND sex = 'ALL' "
            "ORDER BY week DESC LIMIT 2",
            {"metric": metric, "band": band},
            k=int(cached_value("k", 5)),
        )
    # 本週尚未結束，優先用上一個完整週
    rows = [r for r in result.rows[1:] + result.rows[:1] if not r["_masked"]]
    return Decimal(str(rows[0]["value"])) if rows else None
