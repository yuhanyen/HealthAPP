"""載入示範資料（make seed）：10 個里、50 位可登入的示範民眾、6 個據點、12 家商家，
加上約 1,150 位不可登入的背景居民（讓群體統計通過 k=5 遮蔽後仍有內容）。

全部虛構（固定亂數種子，可重現）。主角「王秀英」對應 SPEC §15 示範情境 1–3。
健康資料一律經正式管道寫入：歷程經 fhir/writer、健保經 NhiAdapter、近 72 小時穿戴經
WearableAdapter、環境經 EnvAdapter、輿情經 SentimentAdapter；最後跑 deidentify 回填 anon。

用法：python -m scripts.seed [--reset] [--population 1200] [--weeks 12]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig
from psycopg import sql
from sqlalchemy import func, select

from app.adapters import runner
from app.adapters.base import FIXTURES_DIR
from app.adapters.events_land import sync_events, sync_merchant_ratings
from app.adapters.merchant import hash_api_key
from app.adapters.sentiment_land import sync_sentiment
from app.audit.writer import write_audit
from app.auth.security import hash_password
from app.config import get_settings
from app.consent import service as consent_service
from app.consent.schemas import ConsentScope
from app.db.engines import DbRole, dispose_engines, session_scope
from app.db.models_idn import CareTask, Consent, FamilyLink, FhirResource, PointsLedger, User
from app.db.models_pub import Event, Merchant, Offer, Site, SourceWhitelist, Village
from app.db.models_sys import Account, Config
from app.deidentify.aggregate import recent_weeks, run_deidentify
from app.services import events as event_service
from app.services import health_data
from app.services import points as points_service
from app.services import prefs as prefs_service
from scripts.simulate import (
    Person,
    Simulator,
    assign_consents,
    assign_health,
    insert_people,
    load_scenario,
    make_people,
    now_taipei,
    random_name,
    site_of_village,
)

BACKEND = Path(__file__).resolve().parents[1]
DEMO_PASSWORD = "Demo2026!"
DISTRICT = "北區"
TAIPEI = ZoneInfo("Asia/Taipei")

SITES = [
    ("S01", "北區衛生所", "health_station", "N02"),
    ("S02", "光明里活動中心健康站", "community_center", "N01"),
    ("S03", "和平里社區關懷據點", "care_point", "N03"),
    ("S04", "仁愛里長青據點", "care_point", "N05"),
    ("S05", "忠孝社區藥局健康站", "pharmacy", "N07"),
    ("S06", "長春里活動中心健康站", "community_center", "N09"),
]
MERCHANTS = [
    ("M01", "晨光早餐店", "早餐店", "N01", [("無糖豆漿一杯", 50)]),
    ("M02", "阿嬤蔬果行", "蔬果行", "N02", [("當季蔬菜一把", 80), ("香蕉一串", 60)]),
    ("M03", "動起來運動用品", "運動用品", "N03", [("運動毛巾", 300)]),
    ("M04", "安心社區藥局", "藥局", "N04", [("血壓紀錄本", 100)]),
    ("M05", "巷口咖啡", "咖啡廳", "N05", [("美式咖啡折 20 元", 120)]),
    ("M06", "里仁書店", "書店", "N06", [("健康飲食書籍 9 折", 200)]),
    ("M07", "北區市場阿伯魚攤", "傳統市場", "N07", [("鮮魚折 30 元", 150)]),
    ("M08", "麥香麵包坊", "麵包店", "N08", [("全麥吐司一條", 90)]),
    ("M09", "好鄰居雜貨店", "雜貨店", "N09", [("燕麥片一包", 110)]),
    ("M10", "活力健身房", "健身房", "N10", [("體驗課一堂", 400), ("單次入場", 250)]),
    ("M11", "古早味豆花", "甜品", "N03", [("無糖豆花一碗", 70)]),
    ("M12", "順風自行車行", "自行車行", "N01", [("免費打氣與檢查", 40)]),
    # M8：餐飲（SPEC §17；評分見 fixtures/merchant_ratings.json，M18 刻意沒有評分）
    ("M13", "和平健康便當", "便當", "N03", [("少鹽健康便當折 20 元", 100)]),
    ("M14", "蓮心素食坊", "素食", "N03", [("素食套餐折 30 元", 150)]),
    ("M15", "老街牛肉麵", "麵食", "N04", [("小菜一份", 60)]),
    ("M16", "巷仔內滷肉飯", "小吃", "N03", [("貢丸湯一碗", 50)]),
    ("M17", "綠食光健康餐盒", "健康餐盒", "N02", [("健康餐盒折 40 元", 180)]),
    ("M18", "長青粥品", "小吃", "N05", [("地瓜粥一碗", 70)]),
]
MERCHANT_TAGS = {
    "M01": ["low_sugar"],
    "M02": ["vegetarian"],
    "M04": ["elder_friendly"],
    "M08": ["low_sugar"],
    "M11": ["low_sugar"],
    "M13": ["low_salt", "healthy_meal", "elder_friendly"],
    "M14": ["vegetarian", "low_salt"],
    "M17": ["low_salt", "low_sugar", "healthy_meal"],
    "M18": ["soft_food", "elder_friendly", "low_salt"],
}
# 近兩週的活動（據點／機關上架，直接 published）：
# (編號, 據點, 類別, 名稱, 幾天後, 開始, 結束, 名額, 點數, 標籤)
EVENTS: list[tuple[str, str, str, str, int, str, str, int | None, int, list[str]]] = [
    (
        "EV-S0301",
        "S03",
        "measure",
        "血壓量測與衛教小講堂",
        2,
        "09:30",
        "11:00",
        25,
        20,
        ["bp", "indoor", "gentle", "free"],
    ),
    (
        "EV-S0201",
        "S02",
        "exercise",
        "長青椅子有氧",
        4,
        "14:00",
        "15:00",
        30,
        15,
        ["gentle", "indoor", "steps"],
    ),
    (
        "EV-A0001",
        "S03",
        "walk",
        "北區樂齡健走日（和平公園集合）",
        6,
        "07:30",
        "09:00",
        None,
        30,
        ["steps", "outdoor", "gentle", "free"],
    ),
    (
        "EV-S0101",
        "S01",
        "measure",
        "社區健康篩檢（血壓、血糖、體脂）",
        9,
        "08:30",
        "11:30",
        60,
        20,
        ["bp", "glucose", "weight", "indoor", "free"],
    ),
    (
        "EV-S0501",
        "S05",
        "education",
        "用藥安全講座",
        3,
        "15:00",
        "16:00",
        20,
        0,
        ["indoor", "gentle"],
    ),
    (
        "EV-S0601",
        "S06",
        "nutrition",
        "營養師共餐：少鹽料理示範",
        5,
        "11:00",
        "13:00",
        15,
        10,
        ["bp", "indoor"],
    ),
    ("EV-S0401", "S04", "social", "長者手作聯誼", 7, "14:00", "16:00", 20, 0, ["indoor", "gentle"]),
    ("EV-S0302", "S03", "walk", "清晨健走團", 1, "06:30", "07:30", None, 10, ["steps", "outdoor"]),
]
# 過去的活動（出席紀錄供儀表板與群體統計）：(編號, 據點, 類別, 名稱, 幾天前, 點數)
PAST_EVENTS = [
    ("EV-P0001", "S03", "walk", "和平里健走團", 20, 10),
    ("EV-P0002", "S01", "measure", "北區血壓量測日", 13, 20),
    ("EV-P0003", "S02", "exercise", "長青椅子有氧", 6, 15),
]
# 有健保 Bundle fixture 的示範民眾一定授權 nhi
BUNDLE_PEOPLE = {"DEMO000001", "DEMO000004", "DEMO000007"}


def load_villages() -> list[dict[str, Any]]:
    geo = json.loads((FIXTURES_DIR / "villages.geojson").read_text(encoding="utf-8"))
    return list(geo["features"])


def seed_pub(
    s: Any, rng: random.Random, features: list[dict[str, Any]]
) -> dict[str, tuple[float, float]]:
    centroids: dict[str, tuple[float, float]] = {}
    for f in features:
        p = f["properties"]
        s.add(
            Village(
                code=p["code"],
                name=p["name"],
                district=p["district"],
                population=p["population"],
                geom=f["geometry"],
            )
        )
        centroids[p["code"]] = tuple(p["centroid"])
    s.flush()

    def near(code: str) -> tuple[float, float]:
        lng, lat = centroids[code]
        return round(lat + rng.uniform(-0.002, 0.002), 6), round(
            lng + rng.uniform(-0.002, 0.002), 6
        )

    weekday = {d: "08:30-12:00" for d in ("mon", "tue", "thu", "fri")} | {"wed": "13:30-17:00"}
    for sid, name, kind, village in SITES:
        lat, lng = near(village)
        hours = weekday | {"sat": "09:00-11:00"} if kind == "health_station" else weekday
        equipment = ["血壓計", "體重計", "身高計"] + (
            ["體脂計", "血糖機"] if kind != "care_point" else []
        )
        s.add(
            Site(
                id=sid,
                name=name,
                type=kind,
                village_code=village,
                lat=lat,
                lng=lng,
                hours=hours,
                equipment=equipment,
            )
        )
    today = now_taipei().date()
    for mid, name, category, village, offers in MERCHANTS:
        lat, lng = near(village)
        s.add(
            Merchant(
                id=mid,
                name=name,
                category=category,
                village_code=village,
                lat=lat,
                lng=lng,
                hours={"daily": "07:00-20:00"},
                api_key_hash=hash_api_key(f"demo-merchant-key-{mid}"),
                tags=MERCHANT_TAGS.get(mid, []),
            )
        )
        s.flush()
        for j, (title, cost) in enumerate(offers, start=1):
            s.add(
                Offer(
                    id=f"{mid}-O{j}",
                    merchant_id=mid,
                    title=title,
                    points_cost=cost,
                    stock=rng.randint(30, 150),
                    valid_from=today.replace(month=1, day=1),
                    valid_to=today.replace(month=12, day=31),
                )
            )
    for source_class, pattern in (
        ("google_places", "https://maps.google.com/*"),
        ("forum", "https://forum.example.tw/*"),
        ("news", "https://news.example.tw/*"),
        # M8：公開活動公告（區公所最新消息、里辦公處公開粉專）
        ("event_notice", "https://north.district.example.tw/news/*"),
        ("event_notice", "https://www.facebook.com/hepingli.office/*"),
    ):
        s.add(
            SourceWhitelist(
                source_class=source_class, url_pattern=pattern, approved_by="法制審（模擬）"
            )
        )
    for key, value in {
        "k": 5,
        "step_goal_default": 6000,
        "confirm_ttl_hours": 24,
        "risk_rules": {
            "bp_systolic": 140,
            "bp_diastolic": 90,
            "bp_consecutive": 3,
            "steps_drop_ratio": 0.4,
            "l3_no_revisit_days": 30,
        },
        "anti_cheat": {"max_steps_per_hour": 3000, "max_steps_per_day": 40000},
        "merchant_min_rating": 4.0,
        "event_push_weekly_cap": 2,
    }.items():
        s.add(Config(key=key, value=value, updated_by="seed"))
    return centroids


def demo_people(
    rng: random.Random, scenario: dict[str, Any], villages: list[tuple[str, int]]
) -> list[Person]:
    today = now_taipei().date()
    fixed = {
        "DEMO000001": ("王秀英", "F", today.replace(year=today.year - 73, month=5, day=12), "N03"),
        "DEMO000002": ("王怡君", "F", today.replace(year=today.year - 45, month=8, day=20), "N03"),
    }
    people = make_people(
        rng, 50, first_index=1, villages=villages, today=today, scenario=scenario, prefix="DEMO"
    )
    for person in people:
        person.demo = True
        if person.nid in fixed:
            person.name, person.sex, person.birth, person.village = fixed[person.nid]
            assign_health(rng, person, today, scenario)
        else:
            person.name = random_name(rng, person.sex)
        if person.nid == "DEMO000001":
            person.htn, person.controlled = True, False
            person.consents = {
                "wearable": ["heart_rate", "sleep", "steps"],
                "site": ["body_fat", "bp", "glucose", "height", "weight"],
                "nhi": ["conditions", "labs", "medications", "visits"],
                "env": ["district"],
                "merchant": ["redemptions"],
            }
        elif person.nid == "DEMO000002":
            person.consents = {
                "wearable": ["steps"],
                "env": ["district"],
                "merchant": ["redemptions"],
            }
        else:
            assign_consents(rng, person, today, scenario)
            person.consents.setdefault("wearable", ["steps", "heart_rate"])
        if person.nid in BUNDLE_PEOPLE:
            person.consents["nhi"] = [
                "allergies",
                "conditions",
                "immunizations",
                "labs",
                "medications",
                "visits",
            ]
        # 與健保 Bundle 內容一致（糖尿病；慢性腎病＋高血壓）
        if person.nid == "DEMO000004":
            person.dm = True
        elif person.nid == "DEMO000007":
            person.htn, person.ckd = True, True
    return people


def seed_consents(s: Any, people: list[Person]) -> None:
    background = 0
    for person in people:
        assert person.user_id is not None
        for source, items in person.consents.items():
            if person.demo:  # 示範民眾逐筆走正式服務（逐筆稽核）
                consent_service.grant(
                    s,
                    user_id=person.user_id,
                    source=source,
                    scope=ConsentScope(read=items),
                    actor_type="user",
                    actor_id=str(person.user_id),
                )
            else:
                s.add(
                    Consent(
                        user_id=person.user_id,
                        source=source,
                        scope={
                            "read": sorted(items),
                            "share_summary_to": [],
                            "allow_proxy_redeem": False,
                        },
                    )
                )
                background += 1
    write_audit(
        s,
        actor_type="system",
        actor_id="seed",
        action="consent.bulk_grant",
        decision="executed",
        output={"background_consents": background},
        reason="背景居民（虛構）的授權批次建立",
    )


def seed_accounts(s: Any, people: list[Person]) -> list[tuple[str, str, str]]:
    pw = hash_password(DEMO_PASSWORD)
    demo = [p for p in people if p.demo]
    rows: list[tuple[str, str, str, dict[str, Any]]] = [
        ("wang.xiuying", "citizen", demo[0].name, {"user_id": demo[0].user_id}),
        ("wang.yijun", "citizen", demo[1].name, {"user_id": demo[1].user_id}),
    ]
    rows += [
        (f"citizen{i:02d}", "citizen", p.name, {"user_id": p.user_id})
        for i, p in enumerate(demo[2:], start=3)
    ]
    rows += [
        (f"site.{sid.lower()}", "site_staff", f"{name}承辦", {"site_id": sid})
        for sid, name, _, _ in SITES
    ]
    rows += [("volunteer.s03", "volunteer", "和平里志工", {"site_id": "S03"})]
    rows += [
        (f"merchant.{mid.lower()}", "merchant", name, {"merchant_id": mid})
        for mid, name, *_ in MERCHANTS
    ]
    rows += [
        ("agency.north", "agency", "北區衛生所承辦", {}),
        ("agency.bureau", "agency", "衛生局承辦", {}),
        ("admin", "admin", "系統管理者", {}),
    ]
    for username, role, display, link in rows:
        s.add(Account(username=username, password_hash=pw, role=role, display_name=display, **link))
    return [(u, r, d) for u, r, d, _ in rows]


def seed_relations(s: Any, rng: random.Random, people: list[Person]) -> None:
    demo = [p for p in people if p.demo]
    s.add(FamilyLink(owner_user_id=demo[0].user_id, viewer_user_id=demo[1].user_id))
    for owner, viewer in rng.sample(list(zip(demo[2::2], demo[3::2], strict=False)), k=3):
        s.add(FamilyLink(owner_user_id=owner.user_id, viewer_user_id=viewer.user_id))
    elders = [p for p in demo[2:] if p.age(now_taipei().date()) >= 65]
    for code, person, site, task_type, rule in (
        ("N-0417", demo[0], "S03", "revisit_reminder", "R-BP-L3"),
        ("N-0418", elders[0], "S01", "phone_followup", "R-NORESP-24H"),
        ("N-0419", elders[1], "S05", "home_visit", "R-STEPS-DROP"),
    ):
        s.add(
            CareTask(
                code=code, user_id=person.user_id, site_id=site, task_type=task_type, rule_id=rule
            )
        )


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime.combine(day, datetime.min.time(), TAIPEI) + timedelta(
        hours=hour, minutes=minute
    )


def seed_events(s: Any, today: date) -> None:
    """據點與機關上架的近期活動，以及三場已結束的活動（M8）。"""
    sites = {sid: (name, village) for sid, name, _, village in SITES}
    for eid, sid, category, title, days, start, end, capacity, reward, tags in EVENTS:
        name, village = sites[sid]
        on = today + timedelta(days=days)
        s.add(
            Event(
                id=eid,
                title=title,
                category=category,
                description=f"{name}主辦。{title}，歡迎里民參加。",
                organizer="北區衛生所" if eid.startswith("EV-A") else name,
                site_id=sid,
                village_code=village,
                venue="和平公園" if "公園" in title else name,
                start_at=_at(on, start),
                end_at=_at(on, end),
                capacity=capacity,
                points_reward=reward,
                tags=tags,
                source="agency" if eid.startswith("EV-A") else "site",
                status="published",
                created_by="seed",
            )
        )
    for eid, sid, category, title, days_ago, reward in PAST_EVENTS:
        name, village = sites[sid]
        on = today - timedelta(days=days_ago)
        s.add(
            Event(
                id=eid,
                title=title,
                category=category,
                organizer=name,
                site_id=sid,
                village_code=village,
                venue=name,
                start_at=_at(on, "09:00"),
                end_at=_at(on, "10:30"),
                points_reward=reward,
                tags=["gentle"],
                source="site",
                status="published",
                created_by="seed",
            )
        )


def seed_participation(rng: random.Random, wang: uuid.UUID, others: list[uuid.UUID]) -> int:
    """王秀英的推薦偏好，與過去三場活動的出席（報到發點走正式的 events.check_in）。"""
    attended = 0
    with session_scope(DbRole.IDN_APP) as s:
        prefs_service.save_prefs(
            s,
            wang,
            prefs_service.Prefs(
                event_categories=["walk", "measure"],
                merchant_categories=["早餐店", "便當"],
                dietary=["low_salt"],
                min_rating=4.0,
            ),
        )
        staff = {
            a.site_id: a.id for a in s.scalars(select(Account).where(Account.role == "site_staff"))
        }
        for eid, sid, *_ in PAST_EVENTS:
            event = s.get_one(Event, eid)
            for user_id in [wang, *rng.sample(others, k=min(18, len(others)))]:
                event_service.check_in(
                    s,
                    event=event,
                    user_id=user_id,
                    staff_account_id=staff[sid],
                    now=event.start_at + timedelta(minutes=10),
                )
                attended += 1
    return attended


def reset_database() -> None:
    s = get_settings()
    dispose_engines()
    conninfo = (
        f"host={s.pg_host} port={s.pg_port} dbname=postgres "
        f"user={s.pg_superuser} password={s.pg_superpass}"
    )
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(s.pg_db))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(s.pg_db)))
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app" / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    print(f"已重建資料庫 {s.pg_db} 並套用 migrations")


def settle_recent_points(people: list[Person], today: date) -> int:
    """最近兩天（模擬未發點）以正式的 points.evaluate_day 判定：達標發點、異常步數標記待審。
    DEMO000010 的作弊樣本（單小時 > 3,000 步）會在這裡被標記（SPEC §14 M6 驗收）。"""
    flagged = 0
    for person in people:
        if not person.demo or person.user_id is None:
            continue
        for days_ago in (2, 1):
            day = today - timedelta(days=days_ago)
            with session_scope(DbRole.IDN_APP) as s:
                if "steps" not in consent_service.effective_scope(s, person.user_id).get(
                    "wearable", []
                ):
                    continue
                result = points_service.evaluate_day(
                    s,
                    person.user_id,
                    day,
                    goal=6000,
                    hourly=health_data.hourly_steps(s, person.user_id, day),
                    total=health_data.daily_steps(
                        s, person.user_id, day, day + timedelta(days=1)
                    ).get(day, 0),
                )
                flagged += result.flagged is not None
    return flagged


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="刪除並重建資料庫後再載入")
    parser.add_argument("--population", type=int, default=1200, help="總人口（含 50 位示範民眾）")
    parser.add_argument("--weeks", type=int, default=12, help="歷程週數")
    args = parser.parse_args(argv[1:])
    if args.reset:
        reset_database()

    started = time.monotonic()
    rng = random.Random(20261029)
    scenario = load_scenario()
    features = load_villages()
    village_weights = [(f["properties"]["code"], f["properties"]["population"]) for f in features]
    today = now_taipei().date()

    with session_scope(DbRole.MIGRATOR) as s:
        if s.execute(select(func.count()).select_from(User)).scalar_one() > 0:
            print("資料庫已有使用者資料，略過。要重建請執行：make reset")
            return 0
        centroids = seed_pub(s, rng, features)
        s.flush()
        seed_events(s, today)
        people = demo_people(rng, scenario, village_weights)
        people += make_people(
            rng,
            max(0, args.population - 50),
            first_index=51,
            villages=village_weights,
            today=today,
            scenario=scenario,
        )
        insert_people(s, people)
        seed_consents(s, people)
        accounts = seed_accounts(s, people)
        seed_relations(s, rng, people)
        offers = [(o.id, o.merchant_id, o.points_cost) for o in s.scalars(select(Offer))]
    print(f"人口與授權：{len(people)} 人（{time.monotonic() - started:.0f}s）")

    sim = Simulator(
        rng,
        today=today,
        weeks=args.weeks,
        scenario=scenario,
        site_of_village=site_of_village(centroids, {sid: v for sid, _, _, v in SITES}),
        offers=offers,
    )
    for start in range(0, len(people), 100):
        with session_scope(DbRole.MIGRATOR) as s:
            for person in people[start : start + 100]:
                sim.write(s, person)
    print(f"健康歷程 {args.weeks} 週（{time.monotonic() - started:.0f}s）")

    demo_ids = {p.user_id for p in people if p.demo and p.user_id is not None}
    runner.sync_nhi(user_ids=demo_ids)
    runner.sync_wearable(user_ids=demo_ids, lookback_hours=72)
    runner.sync_env(lookback_hours=72)
    sync_sentiment()
    sync_merchant_ratings()
    notices = sync_events()
    flagged = settle_recent_points(people, today)
    print(f"最近兩天達標判定：{flagged} 天被防作弊標記待審")
    demo_users = [p.user_id for p in people if p.demo and p.user_id is not None]
    attended = seed_participation(rng, demo_users[0], demo_users[2:])
    print(
        f"活動：{len(EVENTS)} 場已上架、{notices.written} 則公開公告待審、過去出席 {attended} 人次"
    )
    stats = run_deidentify(weeks=recent_weeks(today, args.weeks), today=today)

    with session_scope(DbRole.MIGRATOR) as s:
        write_audit(
            s,
            actor_type="system",
            actor_id="seed",
            action="seed.load",
            decision="executed",
            reason=f"載入示範資料：{len(people)} 人、{args.weeks} 週",
        )
        counts = {
            "users": len(people),
            "fhir_resources": s.scalar(select(func.count()).select_from(FhirResource)),
            "points_ledger": s.scalar(select(func.count()).select_from(PointsLedger)),
            "accounts": len(accounts),
        }
    print("示範資料已載入：" + "、".join(f"{k} {v}" for k, v in counts.items()))
    print(
        f"群體統計：{stats.published} 格發布、{stats.primary_suppressed} 格因 n<{stats.k} 遮蔽、"
        f"{stats.secondary_suppressed} 格次要遮蔽（{time.monotonic() - started:.0f}s）"
    )
    print(f"\n示範帳號（密碼一律 {DEMO_PASSWORD}）：")
    for username, role, display in accounts:
        if not username.startswith("citizen"):
            print(f"  {username:<16} {role:<11} {display}")
    print("  citizen03 … citizen50   citizen     其餘 48 位示範民眾")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
