"""merchant_recommend（SPEC §17.3）：依本人偏好與評分門檻推薦附近的商家與餐廳。

- 評分低於門檻（本人設定，否則 sys.config.merchant_min_rating，預設 4.0）或沒有評分的店家不推薦，
  並回報略過了幾家。
- 偏好：喜歡的類別、飲食需求（少鹽、少糖、素食…）；健康訊號只在授權範圍內加分（例如血壓量測偏高
  → 有少鹽選擇的店家），理由文字經 C-7 過濾。
- 商家 API key 欄位不讀。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field
from sqlalchemy import select

from app.db.models_pub import Merchant, Offer
from app.i18n import t
from app.services import events as ev
from app.services import geo, prefs, signals
from app.services import points as points_service
from app.tools._common import profile, safe_all, today
from app.tools.registry import CitizenToolArgs, ToolContext, registry

DINING = ("早餐店", "便當", "素食", "麵食", "小吃", "咖啡廳", "甜品", "麵包店", "健康餐盒")
TAGS = ("low_salt", "low_sugar", "vegetarian", "soft_food", "healthy_meal", "elder_friendly")


class MerchantRecommendArgs(CitizenToolArgs):
    category: str | None = Field(
        default=None, description="dining（所有餐飲）或單一類別，例如「早餐店」「素食」"
    )
    radius_km: float | None = Field(default=None, gt=0, le=10)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    limit: int = Field(default=5, ge=1, le=20)


def _offers(ctx: ToolContext, merchant_id: str, day: date, balance: int) -> list[dict[str, Any]]:
    rows = ctx.db.scalars(
        select(Offer).where(
            Offer.merchant_id == merchant_id,
            Offer.stock > 0,
            Offer.valid_from <= day,
            Offer.valid_to >= day,
        )
    ).all()
    return [
        {
            "offer_id": o.id,
            "title": o.title,
            "points_cost": o.points_cost,
            "affordable": o.points_cost <= balance,
        }
        for o in rows
    ]


@registry.tool(name="merchant_recommend", schema=MerchantRecommendArgs, side="citizen")
def merchant_recommend(ctx: ToolContext, args: MerchantRecommendArgs) -> dict[str, Any]:
    """依本人喜好、飲食需求與評分門檻，推薦附近的特約商家與餐廳（附評分、理由與可兌換優惠）。"""
    day = today()
    me = profile(ctx.db, args.user_id, day)
    mine = prefs.get_prefs(ctx.db, args.user_id)
    threshold = args.min_rating if args.min_rating is not None else mine.rating_threshold
    radius = args.radius_km or mine.max_distance_km or prefs.DEFAULT_MERCHANT_DISTANCE_KM
    home = ev.home_point(ctx.db, me.village_code)
    found = signals.health_signals(ctx.db, args.user_id, args.consent_scope, day)
    wanted_tags = {
        signals.MERCHANT_TAG_OF[k]: s for k, s in found.items() if k in signals.MERCHANT_TAG_OF
    }
    balance = points_service.balance(ctx.db, args.user_id)

    rows = ctx.db.execute(
        select(
            Merchant.id,
            Merchant.name,
            Merchant.category,
            Merchant.lat,
            Merchant.lng,
            Merchant.rating,
            Merchant.rating_count,
            Merchant.rating_source,
            Merchant.tags,
        ).where(Merchant.active)
    ).all()
    low, unrated = 0, 0
    scored: list[tuple[float, dict[str, Any]]] = []
    for mid, name, category, lat, lng, rating, count, source, tags in rows:
        if args.category == "dining" and category not in DINING:
            continue
        if args.category not in (None, "dining") and category != args.category:
            continue
        distance = geo.distance_km(home, (lat, lng)) if home else None
        if distance is not None and distance > radius:
            continue
        if rating is None:
            unrated += 1
            continue
        if float(rating) < threshold:
            low += 1
            continue
        tag_set = set(tags)
        score = float(rating) * 2 + min(count or 0, 500) / 500
        reasons = [
            t(
                "rec.reason.rating",
                rating=f"{float(rating):.1f}",
                count=count or 0,
                source=t(f"rec.rating_source.{source or 'mock'}"),
            )
        ]
        basis: list[str] = []
        if category in mine.merchant_categories:
            score += 2
            reasons.append(t("rec.reason.likes", category=category))
        diet = [d for d in mine.dietary if d in tag_set]
        if diet:
            score += 1.5
            reasons.append(
                t("rec.reason.dietary", items="、".join(t(f"rec.tag.{d}") for d in diet))
            )
        for tag, signal in wanted_tags.items():
            if tag in tag_set:
                score += 1.5
                reasons.append(t(f"rec.reason.health_{tag}"))
                if signal.basis:
                    basis.append(signal.basis)
        offers = _offers(ctx, mid, day, balance)
        if any(o["affordable"] for o in offers):
            score += 1
            cheapest = min((o for o in offers if o["affordable"]), key=lambda o: o["points_cost"])
            reasons.append(
                t("rec.reason.offer", title=cheapest["title"], cost=cheapest["points_cost"])
            )
        if distance is not None:
            score -= distance
            reasons.append(t("rec.reason.near", km=f"{distance:.1f}"))
        scored.append(
            (
                score,
                {
                    "merchant_id": mid,
                    "name": name,
                    "category": category,
                    "rating": float(rating),
                    "rating_count": count,
                    "rating_source": source,
                    "tags": sorted(tag_set),
                    "tag_labels": [t(f"rec.tag.{x}") for x in sorted(tag_set) if x in TAGS],
                    "distance_km": round(distance, 2) if distance is not None else None,
                    "reasons": reasons,
                    "basis": basis,
                    "offers": offers,
                    "score": round(score, 2),
                },
            )
        )
    scored.sort(key=lambda pair: -pair[0])
    return safe_all(
        {
            "merchants": [item for _, item in scored[: args.limit]],
            "min_rating": threshold,
            "radius_km": radius,
            "excluded_low_rating": low,
            "excluded_unrated": unrated,
            "excluded_text": t("rec.excluded", low=low, unrated=unrated, min=f"{threshold:.1f}")
            if low or unrated
            else "",
            "balance": balance,
        }
    )
