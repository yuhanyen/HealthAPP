"""在既有的示範資料庫補上 M8 示範資料（不重建、不動既有資料；可重跑）。

新建資料庫請直接用 make seed（已包含 M8）。這支給「已經在用的開發資料庫」升級：
6 家餐飲商家與帳號、商家健康標籤與評分、公告來源白名單、設定、近兩週活動與過去出席、
王秀英的推薦偏好，最後重跑 deidentify 與今天的 A1 每日報告（報告加上「這週附近」）。

用法：python -m scripts.seed_m8（先 make migrate）
"""

from __future__ import annotations

import random
import sys
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.events_land import sync_events, sync_merchant_ratings
from app.adapters.merchant import hash_api_key
from app.agents import a1
from app.auth.security import hash_password
from app.db.engines import DbRole, session_scope
from app.db.models_idn import EventRegistration
from app.db.models_pub import Event, Merchant, Offer, SourceWhitelist
from app.db.models_sys import Account, Config
from app.deidentify.aggregate import recent_weeks, run_deidentify
from scripts.seed import (
    DEMO_PASSWORD,
    EVENTS,
    MERCHANT_TAGS,
    MERCHANTS,
    PAST_EVENTS,
    seed_events,
    seed_participation,
)
from scripts.simulate import now_taipei

M8_MERCHANTS = {"M13", "M14", "M15", "M16", "M17", "M18"}
NOTICE_PATTERNS = (
    "https://north.district.example.tw/news/*",
    "https://www.facebook.com/hepingli.office/*",
)


def _merchants(s: Session, today: date) -> int:
    existing = set(s.scalars(select(Merchant.id)))
    anchor = {m.village_code: (m.lat, m.lng) for m in s.scalars(select(Merchant))}
    added = 0
    password = hash_password(DEMO_PASSWORD)
    for mid, name, category, village, offers in MERCHANTS:
        if mid in existing:
            merchant = s.get_one(Merchant, mid)
            merchant.tags = MERCHANT_TAGS.get(mid, [])
            continue
        lat, lng = anchor.get(village, (24.80, 120.97))
        s.add(
            Merchant(
                id=mid,
                name=name,
                category=category,
                village_code=village,
                lat=lat + 0.0008,
                lng=lng - 0.0006,
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
                    stock=80,
                    valid_from=today.replace(month=1, day=1),
                    valid_to=today.replace(month=12, day=31),
                )
            )
        s.add(
            Account(
                username=f"merchant.{mid.lower()}",
                password_hash=password,
                role="merchant",
                display_name=name,
                merchant_id=mid,
            )
        )
        added += 1
    return added


def main() -> int:
    today = now_taipei().date()
    with session_scope(DbRole.MIGRATOR) as s:
        added = _merchants(s, today)
        patterns = set(s.scalars(select(SourceWhitelist.url_pattern)))
        for pattern in NOTICE_PATTERNS:
            if pattern not in patterns:
                s.add(
                    SourceWhitelist(
                        source_class="event_notice",
                        url_pattern=pattern,
                        approved_by="法制審（模擬）",
                    )
                )
        for key, value in {"merchant_min_rating": 4.0, "event_push_weekly_cap": 2}.items():
            if s.get(Config, key) is None:
                s.add(Config(key=key, value=value, updated_by="seed_m8"))
        has_events = s.scalar(
            select(func.count()).select_from(Event).where(Event.created_by == "seed")
        )
        if not has_events:
            seed_events(s, today)
    print(f"商家：新增 {added} 家；健康標籤已更新")

    ratings = sync_merchant_ratings()
    notices = sync_events()
    print(f"評分 {ratings.written} 家；公開公告新增 {notices.written} 則待審")

    with session_scope(DbRole.IDN_APP) as s:
        attended = s.scalar(
            select(func.count())
            .select_from(EventRegistration)
            .where(EventRegistration.event_id.in_([e[0] for e in PAST_EVENTS]))
        )
        wang = s.scalar(select(Account.user_id).where(Account.username == "wang.xiuying"))
        others = list(
            s.scalars(
                select(Account.user_id)
                .where(Account.role == "citizen", Account.username.like("citizen%"))
                .order_by(Account.username)
            )
        )
    if not attended and wang is not None:
        n = seed_participation(random.Random(20261030), wang, [u for u in others if u])
        print(f"過去活動出席 {n} 人次")

    stats = run_deidentify(weeks=recent_weeks(today, 12), today=today)
    print(f"群體統計重算：{stats.published} 格發布")
    batch = a1.run_daily_batch(today)
    print(f"A1 今日報告重算：{batch.reports} 份（{len(EVENTS)} 場近期活動）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
