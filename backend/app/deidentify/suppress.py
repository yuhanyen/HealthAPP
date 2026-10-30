"""群體統計的小格遮蔽（k-匿名）：主要遮蔽 + 次要（互補）遮蔽。

cohort_weekly 同時發布最細格（里×年齡層×性別）與加總列（全區、各里、各年齡層、各性別，
以 'ALL' 表示）。只刪掉 n < k 的格子還不夠：「加總 − 已發布子格」就能推回被刪的格子。
規則：對每一列已發布的加總、以及它的每一種拆分方式，被遮蔽子格的人數合計必須是 0 或 ≥ k；
不符合就再遮蔽同層人數最少的已發布子格，反覆到穩定為止。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ALL = "ALL"

Key = tuple[str, str, str]  # (village_code, age_band, sex)


@dataclass(frozen=True)
class Cell:
    week: date
    village_code: str
    age_band: str
    sex: str
    metric: str
    value: Decimal
    n: int

    @property
    def key(self) -> Key:
        return (self.village_code, self.age_band, self.sex)


@dataclass(frozen=True)
class SuppressionStats:
    total: int
    primary: int
    secondary: int

    @property
    def published(self) -> int:
        return self.total - self.primary - self.secondary


def _partitions(keys: Iterable[Key]) -> list[tuple[Key, list[Key]]]:
    """（加總列, 構成它的一組子列）；只列出實際存在的列（不存在＝人數 0）。"""
    keyset = set(keys)
    fine = [k for k in keyset if ALL not in k]
    groups: dict[Key, list[Key]] = defaultdict(list)
    for v, a, s in fine:
        groups[(v, ALL, ALL)].append((v, a, s))
        groups[(ALL, a, ALL)].append((v, a, s))
        groups[(ALL, ALL, s)].append((v, a, s))
    total = (ALL, ALL, ALL)
    by_village = [k for k in keyset if k[0] != ALL and k[1:] == (ALL, ALL)]
    by_age = [k for k in keyset if k[1] != ALL and k[0] == ALL and k[2] == ALL]
    by_sex = [k for k in keyset if k[2] != ALL and k[:2] == (ALL, ALL)]
    parts = [(parent, children) for parent, children in groups.items() if parent in keyset]
    if total in keyset:
        parts += [(total, by_village), (total, by_age), (total, by_sex)]
    return parts


def suppress_slice(cells: list[Cell], k: int) -> tuple[list[Cell], int, int]:
    """單一（週, 指標）的格子 → 可發布格子、主要遮蔽數、次要遮蔽數。"""
    by_key = {c.key: c for c in cells}
    suppressed = {key for key, c in by_key.items() if c.n < k}
    primary = len(suppressed)
    parts = _partitions(by_key)
    changed = True
    while changed:
        changed = False
        for parent, children in parts:
            if parent in suppressed:
                continue
            hidden = sum(by_key[c].n for c in children if c in suppressed)
            if 0 < hidden < k:
                visible = sorted(
                    (c for c in children if c not in suppressed), key=lambda c: (by_key[c].n, c)
                )
                if visible:
                    suppressed.add(visible[0])
                else:  # 理論上不會發生（加總已發布表示 n ≥ k），保守起見連加總一起遮
                    suppressed.add(parent)
                changed = True
    published = [c for key, c in by_key.items() if key not in suppressed]
    return published, primary, len(suppressed) - primary


def suppress(cells: Iterable[Cell], k: int) -> tuple[list[Cell], SuppressionStats]:
    slices: dict[tuple[date, str], list[Cell]] = defaultdict(list)
    for cell in cells:
        slices[(cell.week, cell.metric)].append(cell)
    published: list[Cell] = []
    total = primary = secondary = 0
    for slice_cells in slices.values():
        kept, p, s = suppress_slice(slice_cells, k)
        published += kept
        total += len(slice_cells)
        primary += p
        secondary += s
    return published, SuppressionStats(total=total, primary=primary, secondary=secondary)
