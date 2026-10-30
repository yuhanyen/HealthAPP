"""C-4：任何回傳聚合結果的查詢，列對應人數 < k（預設 5）即遮蔽。

由 deidentify/k_anon.py 中介軟體統一處理（不在個別工具重寫）：
- 結果含人數欄 n → n < k 的列，數值欄與 n 一律清空並標記 _masked。
- 聚合查詢卻沒輸出 n → 拒絕執行（否則無從判斷是否該遮蔽）。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db.engines import DbRole, get_engine, session_scope
from app.deidentify.k_anon import (
    KAnonViolation,
    current_k,
    execute_k_anonymous,
    mask_small_cells,
)

pytestmark = pytest.mark.constraint

COLUMNS = ["village_code", "age_band", "value", "n"]
ROWS = [
    ("N01", "60-69", Decimal("0.31"), 12),
    ("N02", "70-79", Decimal("0.52"), 4),
    ("N03", "80-89", Decimal("0.40"), 5),
    ("N04", "60-69", Decimal("0.10"), None),
]


def test_rows_below_k_are_masked() -> None:
    result = mask_small_cells(COLUMNS, ROWS, k=5)

    assert result.k == 5
    assert result.masked_rows == 2
    by_village = {row["village_code"]: row for row in result.rows}
    assert by_village["N02"] == {
        "village_code": "N02",
        "age_band": "70-79",
        "value": None,
        "n": None,
        "_masked": True,
    }
    assert by_village["N04"]["_masked"] is True
    # n == k 不遮蔽
    assert by_village["N03"]["value"] == Decimal("0.40")
    assert by_village["N03"]["_masked"] is False


def test_k_is_a_parameter_not_hardcoded() -> None:
    assert mask_small_cells(COLUMNS, ROWS, k=13).masked_rows == 4
    assert mask_small_cells(COLUMNS, ROWS, k=1).masked_rows == 1  # 只剩 n 為 NULL 那列


def test_default_k_is_five() -> None:
    with session_scope(DbRole.IDN_APP) as s:
        assert current_k(s) == 5


@pytest.fixture
def cohort() -> dict[str, object]:
    """在 anon.cohort_weekly 放一組測試資料（n = 3、8、5），回傳查詢參數。"""
    params: dict[str, object] = {"w": date(2001, 1, 1), "m": f"test_{uuid.uuid4().hex[:8]}"}
    with get_engine(DbRole.ANON_WRITER).begin() as conn:
        for village, n in (("N01", 3), ("N02", 8), ("N03", 5)):
            conn.execute(
                text(
                    "INSERT INTO anon.cohort_weekly "
                    "(week, village_code, age_band, sex, metric, value, n) "
                    "VALUES (:w, :v, '60-69', 'F', :m, 0.5, :n)"
                ),
                {**params, "v": village, "n": n},
            )
    return params


def test_middleware_masks_real_anon_query(cohort: dict[str, object]) -> None:
    with get_engine(DbRole.ANON_READER).connect() as conn:
        result = execute_k_anonymous(
            conn,
            "SELECT village_code, value, n FROM anon.cohort_weekly "
            "WHERE week = :w AND metric = :m ORDER BY village_code",
            cohort,
            k=5,
        )
    assert result.masked_rows == 1
    assert [r["_masked"] for r in result.rows] == [True, False, False]
    assert result.rows[0]["value"] is None


def test_aggregate_query_must_expose_n(cohort: dict[str, object]) -> None:
    with get_engine(DbRole.ANON_READER).connect() as conn, pytest.raises(KAnonViolation):
        execute_k_anonymous(
            conn,
            "SELECT village_code, avg(value) AS v FROM anon.cohort_weekly "
            "WHERE week = :w AND metric = :m GROUP BY village_code",
            cohort,
            k=5,
        )


def test_aggregate_query_with_n_is_masked(cohort: dict[str, object]) -> None:
    with get_engine(DbRole.ANON_READER).connect() as conn:
        result = execute_k_anonymous(
            conn,
            "SELECT village_code, sum(n) AS n, avg(value) AS value FROM anon.cohort_weekly "
            "WHERE week = :w AND metric = :m GROUP BY village_code ORDER BY village_code",
            cohort,
            k=5,
        )
    assert result.masked_rows == 1
    assert result.rows[0]["village_code"] == "N01"
    assert result.rows[0]["n"] is None
