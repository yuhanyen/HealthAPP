"""deidentify 批次：遮蔽規則（主要＋次要）與 idn → anon 端到端。"""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.engines import DbRole, session_scope
from app.db.models_anon import CohortWeekly, SiteOpsWeekly
from app.deidentify.aggregate import METRICS, run_deidentify
from app.deidentify.suppress import ALL, Cell, suppress_slice
from app.fhir import builders
from app.fhir.writer import ingest
from tests.conftest import Factory
from tests.helpers import TEST_SITE

TZ = ZoneInfo("Asia/Taipei")
K = 5


# ---------- 遮蔽規則（不碰 DB） ----------


def _cells_from_people(people: list[tuple[str, str, str]]) -> list[Cell]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for v, a, s in people:
        for key in ((v, a, s), (v, ALL, ALL), (ALL, a, ALL), (ALL, ALL, s), (ALL, ALL, ALL)):
            counts[key] += 1
    week = date(2001, 1, 1)
    return [Cell(week, v, a, s, "m", Decimal(n), n) for (v, a, s), n in counts.items()]


def _violations(cells: list[Cell], published: list[Cell], k: int) -> list[str]:
    """獨立於實作的檢查：已發布列 n ≥ k；每個已發布加總的每種拆分，被遮蔽子列合計為 0 或 ≥ k。"""
    pub = {c.key for c in published}
    n = {c.key: c.n for c in cells}
    problems = [f"{key} n={n[key]}" for key in pub if n[key] < k]
    fine = [key for key in n if ALL not in key]
    splits: list[tuple[tuple[str, str, str], list[tuple[str, str, str]]]] = []
    for v in {key[0] for key in fine}:
        splits.append(((v, ALL, ALL), [x for x in fine if x[0] == v]))
    for a in {key[1] for key in fine}:
        splits.append(((ALL, a, ALL), [x for x in fine if x[1] == a]))
    for s in {key[2] for key in fine}:
        splits.append(((ALL, ALL, s), [x for x in fine if x[2] == s]))
    total = (ALL, ALL, ALL)
    for axis in range(3):
        level = [key for key in n if key != total and key[axis] != ALL and key.count(ALL) == 2]
        splits.append((total, level))
    for parent, children in splits:
        if parent in pub:
            hidden = sum(n[c] for c in children if c not in pub)
            if 0 < hidden < k:
                problems.append(f"{parent} 可推回 {hidden} 人")
    return problems


@pytest.mark.parametrize("seed", range(40))
def test_suppression_invariants_on_random_populations(seed: int) -> None:
    rnd = random.Random(seed)
    villages = [f"V{i}" for i in range(rnd.randint(2, 6))]
    ages = ["20-29", "30-39", "40-49", "50-59", "60-69"][: rnd.randint(2, 5)]
    people = [
        (rnd.choice(villages), rnd.choice(ages), rnd.choice("FM"))
        for _ in range(rnd.randint(5, 120))
    ]
    cells = _cells_from_people(people)
    published, _, _ = suppress_slice(cells, K)
    assert _violations(cells, published, K) == []


def test_single_small_cell_triggers_secondary_suppression() -> None:
    people = [("V1", "40-49", "F")] * 7 + [("V1", "40-49", "M")] * 3
    published, primary, secondary = suppress_slice(_cells_from_people(people), K)
    keys = {c.key for c in published}
    assert ("V1", "40-49", "M") not in keys and ("V1", "40-49", "F") not in keys
    assert ("V1", ALL, ALL) in keys and (ALL, ALL, ALL) in keys
    assert primary >= 1 and secondary >= 1


# ---------- 端到端：idn → anon ----------


def _seed_steps(
    factory: Factory,
    village: str,
    sex: str,
    count: int,
    week: date,
    steps: int,
    *,
    bp: tuple[int, int] | None = None,
) -> None:
    for _ in range(count):
        user = factory.user(village=village, sex=sex, birth_date=date(1956, 6, 1))
        factory.consent(user.id, "wearable", ["steps"])
        if bp:
            factory.consent(user.id, "site", ["bp"])
        with session_scope(DbRole.IDN_APP) as s:
            ingest(
                s,
                user_id=user.id,
                source="wearable",
                emit_events=False,
                resources=[
                    builders.steps_daily(
                        datetime.combine(week + timedelta(days=d), datetime.max.time(), TZ).replace(
                            microsecond=0
                        ),
                        steps,
                    )
                    for d in range(7)
                ],
            )
            if bp:
                ingest(
                    s,
                    user_id=user.id,
                    source="site",
                    emit_events=False,
                    resources=[
                        builders.blood_pressure(
                            datetime.combine(week + timedelta(days=2), datetime.min.time(), TZ)
                            + timedelta(hours=9),
                            *bp,
                            site=TEST_SITE,
                        )
                    ],
                )


def _published(
    week: date, metric: str, village: str
) -> dict[tuple[str, str, str], tuple[Decimal, int]]:
    with session_scope(DbRole.ANON_READER) as s:
        rows = s.scalars(
            select(CohortWeekly).where(
                CohortWeekly.week == week,
                CohortWeekly.metric == metric,
                CohortWeekly.village_code.in_([village, ALL]),
            )
        ).all()
        return {(r.village_code, r.age_band, r.sex): (r.value, r.n) for r in rows}


def test_cohort_batch_end_to_end(factory: Factory) -> None:
    week = date(2001, 1, 8)
    village = factory.village()
    _seed_steps(factory, village, "F", 6, week, 5000, bp=(150, 95))
    _seed_steps(factory, village, "M", 6, week, 7000, bp=(120, 78))

    stats = run_deidentify(weeks=[week], today=date(2001, 3, 1))
    assert stats.k == K and stats.published > 0

    steps = _published(week, "steps_avg", village)
    assert steps[(village, "40-49", "F")] == (Decimal("5000"), 6)
    assert steps[(village, "40-49", "M")] == (Decimal("7000"), 6)
    assert steps[(village, ALL, ALL)] == (Decimal("6000"), 12)
    high = _published(week, "bp_high_rate", village)
    assert high[(village, ALL, ALL)] == (Decimal("0.5000"), 12)

    again = run_deidentify(weeks=[week], today=date(2001, 3, 1))
    assert again.published == stats.published  # 可重跑、不重複


def test_small_cells_never_reach_anon(factory: Factory) -> None:
    week = date(2001, 1, 15)
    village = factory.village()
    _seed_steps(factory, village, "F", 7, week, 4000)
    _seed_steps(factory, village, "M", 3, week, 9000)

    run_deidentify(weeks=[week], today=date(2001, 3, 1))
    steps = _published(week, "steps_avg", village)
    # 男 3 人被主要遮蔽；女 7 人因「里加總 − 女 = 男」被次要遮蔽；性別加總列同理。
    # 年齡層加總（10 人）的被遮蔽子格合計 10 ≥ k，可以發布。
    assert set(steps) == {(village, ALL, ALL), (ALL, "40-49", ALL), (ALL, ALL, ALL)}
    assert steps[(village, ALL, ALL)][1] == 10

    with session_scope(DbRole.ANON_READER) as s:
        smallest = min(
            s.scalars(select(CohortWeekly.n).where(CohortWeekly.metric.in_(list(METRICS))))
        )
    assert smallest >= K


def test_site_ops_published_only_with_k_persons(factory: Factory) -> None:
    week = date(2001, 1, 22)
    village = factory.village()
    _seed_steps(factory, village, "F", 5, week, 5000, bp=(130, 80))
    run_deidentify(weeks=[week], today=date(2001, 3, 1))
    with session_scope(DbRole.ANON_READER) as s:
        rows = s.scalars(select(SiteOpsWeekly).where(SiteOpsWeekly.week == week)).all()
    assert [(r.site_id, r.visits) for r in rows] == [(TEST_SITE, 5)]
