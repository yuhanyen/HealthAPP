"""示範資料模擬器（seed 使用）：產生虛構人口與 N 週健康歷程。

- 步數每日總數取自 WearableAdapter 的 Mock（與每小時同步一致）。
- 據點量測頻率依年齡、慢性病；情境里（fixtures/population_scenario.json）近幾週量測變少、血壓變高。
- 健保紀錄：有 fixtures/nhi_bundle_*.json 的人由 NhiAdapter 同步，其餘在這裡產生。
- 所有健康資料都經 fhir/writer.ingest 寫入（照樣受授權範圍限制），不繞過正式管道。
"""

from __future__ import annotations

import json
import math
import random
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cached_property
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.adapters.base import FIXTURES_DIR
from app.adapters.mocks.nhi import MockNhiClient
from app.adapters.mocks.wearable import MockWearableClient
from app.db.crypto import hash_national_id
from app.db.models_idn import Booking, PointsLedger, Redemption, User
from app.fhir import builders
from app.fhir.writer import ingest

TZ = ZoneInfo("Asia/Taipei")
SURNAMES = list("陳林黃張李王吳劉蔡楊許鄭謝郭洪曾邱廖賴周徐孫馬朱胡郭何高羅鄭")
GIVEN_F = [
    "淑芬",
    "美玲",
    "麗華",
    "秀蘭",
    "雅婷",
    "怡君",
    "佩珊",
    "淑惠",
    "玉珍",
    "月英",
    "惠美",
    "靜宜",
    "宜蓁",
    "詩涵",
    "欣怡",
    "春梅",
    "麗娟",
    "美惠",
]
GIVEN_M = [
    "志明",
    "建宏",
    "俊傑",
    "文雄",
    "國華",
    "家豪",
    "明德",
    "金龍",
    "正雄",
    "宗翰",
    "冠宇",
    "信良",
    "承恩",
    "柏翰",
    "建志",
    "永福",
    "清水",
    "國榮",
]
CONSENT_ITEMS = {
    "wearable": ["steps", "heart_rate"],
    "site": ["bp", "weight", "height", "body_fat", "glucose"],
    "nhi": ["visits", "conditions", "medications", "labs"],
    "env": ["district"],
    "merchant": ["redemptions"],
}


def load_scenario() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES_DIR / "population_scenario.json").read_text(encoding="utf-8")
    )
    return data


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ)


@dataclass
class Person:
    nid: str
    name: str
    sex: str
    birth: date
    village: str
    demo: bool = False
    user_id: uuid.UUID | None = None
    htn: bool = False
    controlled: bool = True
    dm: bool = False
    ckd: bool = False
    consents: dict[str, list[str]] = field(default_factory=dict)

    @cached_property
    def subject(self) -> str:
        return hash_national_id(self.nid)

    def age(self, today: date) -> int:
        years = today.year - self.birth.year
        return years - ((today.month, today.day) < (self.birth.month, self.birth.day))


@dataclass
class History:
    resources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    bookings: list[Booking] = field(default_factory=list)
    ledger: list[PointsLedger] = field(default_factory=list)
    redemptions: list[Redemption] = field(default_factory=list)


def random_name(rng: random.Random, sex: str) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN_F if sex == "F" else GIVEN_M)


def random_birth(rng: random.Random, today: date, scenario: dict[str, Any]) -> date:
    brackets = scenario["age_distribution"]
    low, high, _ = rng.choices(brackets, weights=[b[2] for b in brackets])[0]
    age = rng.randint(low, high)
    return today - timedelta(days=age * 365 + rng.randint(0, 364))


def assign_health(
    rng: random.Random, person: Person, today: date, scenario: dict[str, Any]
) -> None:
    age = person.age(today)
    decade = str(min(70, age // 10 * 10))
    person.htn = rng.random() < scenario["hypertension_prevalence"].get(decade, 0.03)
    person.controlled = rng.random() < 0.65
    person.dm = age >= 50 and rng.random() < scenario["diabetes_prevalence_50_plus"]
    person.ckd = age >= 60 and rng.random() < 0.03


def assign_consents(
    rng: random.Random, person: Person, today: date, scenario: dict[str, Any]
) -> None:
    odds = dict(scenario["consent_odds"])
    if person.age(today) >= 50:
        odds["site"] = min(0.95, odds["site"] + 0.15)
    person.consents = {src: list(CONSENT_ITEMS[src]) for src, p in odds.items() if rng.random() < p}


def make_people(
    rng: random.Random,
    count: int,
    *,
    first_index: int,
    villages: Sequence[tuple[str, int]],
    today: date,
    scenario: dict[str, Any],
    prefix: str = "SIM",
) -> list[Person]:
    people = []
    codes, weights = zip(*villages, strict=True)
    for i in range(first_index, first_index + count):
        sex = rng.choice("FM")
        person = Person(
            nid=f"{prefix}{i:06d}",
            name=random_name(rng, sex),
            sex=sex,
            birth=random_birth(rng, today, scenario),
            village=rng.choices(codes, weights=weights)[0],
        )
        assign_health(rng, person, today, scenario)
        assign_consents(rng, person, today, scenario)
        people.append(person)
    return people


def insert_people(session: Session, people: Sequence[Person]) -> None:
    for person in people:
        user = User(
            national_id_hash=person.subject,
            name=person.name,
            birth_date=person.birth,
            sex=person.sex,
            phone=f"0900{int(person.nid[-6:]):06d}" if person.demo else None,
            address=f"北區{person.village}示範路{int(person.nid[-3:]) % 200 + 1}號",
            village_code=person.village,
            elder_mode=person.age(datetime.now(TZ).date()) >= 65,
        )
        session.add(user)
        session.flush()
        person.user_id = user.id


class Simulator:
    def __init__(
        self,
        rng: random.Random,
        *,
        today: date,
        weeks: int,
        scenario: dict[str, Any],
        site_of_village: dict[str, str],
        offers: Sequence[tuple[str, str, int]],
    ) -> None:
        self.rng = rng
        self.today = today
        self.weeks = weeks
        self.scenario = scenario
        self.site_of_village = site_of_village
        self.offers = sorted(offers, key=lambda o: o[2])
        self.mock = MockWearableClient(today=today)
        self.bundled = set(MockNhiClient().bundles_by_subject)
        self.first_day = week_start(today) - timedelta(weeks=weeks - 1)
        self.trend_from = week_start(today) - timedelta(weeks=scenario["trend_weeks"] - 1)

    def days(self) -> list[date]:
        count = (self.today - self.first_day).days
        return [self.first_day + timedelta(days=i) for i in range(count)]

    def in_trend(self, person: Person, day: date) -> bool:
        return person.village in self.scenario["trend_villages"] and day >= self.trend_from

    def bp(self, person: Person, day: date) -> tuple[int, int]:
        age = person.age(day)
        sys_mu: float
        dia_mu: float
        if person.htn:
            sys_mu, dia_mu = (128, 80) if person.controlled else (151, 94)
            if self.in_trend(person, day):
                sys_mu += self.scenario["bp_shift"]
                dia_mu += self.scenario["bp_shift"] // 2
        else:
            sys_mu, dia_mu = 118 + max(0, age - 40) * 0.3, 76
        return int(self.rng.gauss(sys_mu, 8)), int(self.rng.gauss(dia_mu, 6))

    def visit_probability(self, person: Person, day: date) -> float:
        age = person.age(day)
        p = 0.04 if age < 50 else 0.10 if age < 65 else 0.18
        if person.htn:
            p += 0.22  # 高血壓族群規律量測
        if self.in_trend(person, day):
            p *= self.scenario["site_visit_factor"]
        return p

    def wang_readings(self) -> list[tuple[int, int, int, str]]:
        # SPEC §8.4 範例：近三次 142/91、145/93、139/90（天數為距今）
        return [(56, 128, 82, "S03"), (7, 142, 91, "S03"), (4, 145, 93, "S03"), (2, 139, 90, "S01")]

    def history(self, person: Person) -> History:
        h = History()
        consents = person.consents
        days = self.days()
        daily: dict[date, int] = {}

        if "wearable" in consents:
            daily = {d: self.mock.daily_total(person.subject, d) for d in days}
            h.resources["wearable"] = [
                builders.steps_daily(at(d, 23, 59), steps) for d, steps in daily.items()
            ]

        if "site" in consents:
            site_res: list[dict[str, Any]] = []
            home_site = self.site_of_village[person.village]
            if person.nid == "DEMO000001":
                for days_ago, s, d, site in self.wang_readings():
                    when = at(self.today - timedelta(days=days_ago), 9, 20)
                    site_res.append(builders.blood_pressure(when, s, d, site=site))
            else:
                for week in range(self.weeks):
                    monday = self.first_day + timedelta(weeks=week)
                    if monday > self.today or self.rng.random() >= self.visit_probability(
                        person, monday
                    ):
                        continue
                    day = monday + timedelta(days=self.rng.randint(0, 4))
                    if day >= self.today:
                        continue
                    when = at(day, self.rng.randint(8, 11), self.rng.randint(0, 59))
                    site_res.append(
                        builders.blood_pressure(when, *self.bp(person, day), site=home_site)
                    )
                    if self.rng.random() < 0.3:
                        kg = round(self.rng.gauss(68 if person.sex == "M" else 57, 8), 1)
                        site_res.append(builders.body_weight(when, kg, site=home_site))
                    if self.rng.random() < 0.3:
                        h.bookings.append(self._booking(person, home_site, when, "completed"))
                if self.rng.random() < 0.15:
                    future = at(self.today + timedelta(days=self.rng.randint(1, 6)), 9)
                    h.bookings.append(self._booking(person, home_site, future, "booked"))
                if self.rng.random() < 0.05:
                    missed = at(self.today - timedelta(days=self.rng.randint(3, 20)), 10)
                    h.bookings.append(self._booking(person, home_site, missed, "no_show"))
            if site_res:
                h.resources["site"] = site_res

        if "nhi" in consents and person.subject not in self.bundled:
            nhi = self._nhi(person)
            if nhi:
                h.resources["nhi"] = nhi

        if daily:
            self._points(person, daily, "merchant" in consents, h)
        return h

    def _booking(self, person: Person, site: str, when: datetime, status: str) -> Booking:
        assert person.user_id is not None
        return Booking(
            user_id=person.user_id,
            site_id=site,
            slot_at=when,
            purpose="血壓量測",
            status=status,
            created_by="user",
        )

    def _nhi(self, person: Person) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        since = self.today - timedelta(days=self.rng.randint(400, 2400))
        if person.htn:
            out += [
                builders.condition(since, "I10", "Essential (primary) hypertension"),
                builders.medication_statement(since, "C08CA01", "Amlodipine 5mg"),
            ]
        if person.dm:
            out += [
                builders.condition(
                    since - timedelta(days=90),
                    "E11.9",
                    "Type 2 diabetes mellitus without complications",
                ),
                builders.medication_statement(since, "A10BA02", "Metformin 500mg"),
            ]
        if person.ckd:
            out.append(builders.condition(since, "N18.3", "Chronic kidney disease, stage 3"))
        visits = max(1, round(self.weeks * 7 / (35 if (person.htn or person.dm) else 120)))
        for _ in range(visits):
            day = self.first_day + timedelta(days=self.rng.randint(0, self.weeks * 7 - 2))
            if day < self.today:
                dept = "內科" if person.htn else "新陳代謝科" if person.dm else "家庭醫學科"
                out.append(builders.encounter(at(day, 10), dept))
        return out

    def _points(self, person: Person, daily: dict[date, int], redeems: bool, h: History) -> None:
        assert person.user_id is not None
        balance = 0
        # 最近兩天留給 seed 以正式的 points.evaluate_day 判定（含防作弊），不在模擬裡直接發點
        settled = self.today - timedelta(days=2)
        for day, steps in sorted(daily.items()):
            if steps >= 6000 and day < settled:
                balance += 10
                h.ledger.append(
                    PointsLedger(
                        user_id=person.user_id,
                        delta=10,
                        reason="goal.steps",
                        ref_type="date",
                        ref_id=day.isoformat(),
                        balance_after=balance,
                        created_at=at(day, 23, 59),
                    )
                )
            if redeems and day.weekday() == 5 and self.rng.random() < 0.18:
                affordable = [o for o in self.offers if o[2] <= balance]
                if affordable:
                    offer_id, merchant_id, cost = self.rng.choice(affordable)
                    balance -= cost
                    when = at(day, 15, self.rng.randint(0, 59))
                    redemption = Redemption(
                        id=uuid.uuid4(),
                        user_id=person.user_id,
                        merchant_id=merchant_id,
                        offer_id=offer_id,
                        points=cost,
                        qr_token=secrets.token_urlsafe(16),
                        status="verified",
                        redeemed_at=when,
                        verified_at=when + timedelta(minutes=3),
                    )
                    h.redemptions.append(redemption)
                    h.ledger.append(
                        PointsLedger(
                            user_id=person.user_id,
                            delta=-cost,
                            reason="redeem",
                            ref_type="redemption",
                            ref_id=str(redemption.id),
                            balance_after=balance,
                            created_at=when,
                        )
                    )

    def write(self, session: Session, person: Person) -> int:
        assert person.user_id is not None
        h = self.history(person)
        written = 0
        for source, resources in h.resources.items():
            written += ingest(
                session,
                user_id=person.user_id,
                source=source,
                resources=resources,
                emit_events=False,
            ).written
        session.add_all(h.bookings)
        session.add_all(h.redemptions)
        session.flush()
        session.add_all(h.ledger)
        return written


def site_of_village(
    villages: dict[str, tuple[float, float]], sites: dict[str, str]
) -> dict[str, str]:
    """每個里最近的據點（sites：據點 id → 所在里）。"""
    out: dict[str, str] = {}
    for code, (lng, lat) in villages.items():
        out[code] = min(
            sites,
            key=lambda sid: math.dist((lng, lat), villages[sites[sid]]),
        )
    return out


def now_taipei() -> datetime:
    return datetime.now(UTC).astimezone(TZ)
