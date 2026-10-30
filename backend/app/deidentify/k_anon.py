"""C-4：查詢層 k-匿名中介軟體。所有回傳聚合結果的查詢都經這裡，不在個別工具重寫。

規則：
- 結果含人數欄 n：n < k（或 n 為 NULL）的列遮蔽——數值欄與 n 清空、保留維度欄（里、年齡層…），
  並標 _masked=True，讓呈現端照實寫「已遮蔽（< k）」而不是補值。
- SQL 是聚合查詢（GROUP BY／count／sum／avg…）卻沒輸出 n：拒絕執行。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.i18n import t
from app.sysconfig import get_k

N_COLUMN = "n"
_AGGREGATE = re.compile(
    r"\bgroup\s+by\b|\b(count|sum|avg|min|max|stddev\w*|variance|percentile_\w+|array_agg|string_agg)\s*\(",
    re.I,
)


class KAnonViolation(ValueError):
    def __init__(self) -> None:
        super().__init__(t("kanon.error.aggregate_without_n"))


@dataclass(frozen=True)
class KAnonResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    masked_rows: int
    k: int


def _is_measure(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def mask_small_cells(
    columns: Sequence[str], rows: Sequence[Sequence[Any] | Mapping[str, Any]], *, k: int
) -> KAnonResult:
    cols = list(columns)
    has_n = N_COLUMN in cols
    out: list[dict[str, Any]] = []
    masked = 0
    for raw in rows:
        row = dict(raw) if isinstance(raw, Mapping) else dict(zip(cols, raw, strict=True))
        small = has_n and (row[N_COLUMN] is None or row[N_COLUMN] < k)
        if small:
            masked += 1
            row = {c: (None if c == N_COLUMN or _is_measure(v) else v) for c, v in row.items()}
        row["_masked"] = small
        out.append(row)
    return KAnonResult(columns=cols, rows=out, masked_rows=masked, k=k)


def execute_k_anonymous(
    conn: Connection | Session, sql: str, params: Mapping[str, Any] | None = None, *, k: int
) -> KAnonResult:
    result = conn.execute(text(sql), dict(params or {}))
    columns = list(result.keys())
    if _AGGREGATE.search(sql) and N_COLUMN not in columns:
        raise KAnonViolation()
    return mask_small_cells(columns, [tuple(r) for r in result], k=k)


def current_k(session: Session) -> int:
    """目前 k 值（sys.config.k，預設 5）。機關側連線讀不到 sys，k 由應用層讀好再傳入。"""
    return get_k(session)
