"""merchant_lookup（SPEC §7.1）：附近可兌換的特約商家與優惠。商家 API key 欄位不讀。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models_pub import Merchant, Offer
from app.services import geo
from app.tools._common import profile, today
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class MerchantLookupArgs(CitizenToolArgs):
    lat: float | None = None
    lng: float | None = None
    radius_km: float = 1.5
    points: int | None = None


@registry.tool(name="merchant_lookup", schema=MerchantLookupArgs, side="citizen")
def merchant_lookup(ctx: ToolContext, args: MerchantLookupArgs) -> dict[str, Any]:
    """找住家附近（或指定座標）可兌換點數的特約商家與優惠；可只列出點數夠用的優惠。"""
    if args.lat is not None and args.lng is not None:
        centre = (args.lat, args.lng)
    else:
        centre = geo.village_centroids(ctx.db).get(
            profile(ctx.db, args.user_id).village_code, (0.0, 0.0)
        )
    day = today()
    merchants = ctx.db.execute(
        select(Merchant.id, Merchant.name, Merchant.category, Merchant.lat, Merchant.lng).where(
            Merchant.active
        )
    ).all()
    out: list[dict[str, Any]] = []
    for mid, name, category, lat, lng in merchants:
        distance = geo.distance_km(centre, (lat, lng))
        if distance > args.radius_km:
            continue
        offers = ctx.db.scalars(
            select(Offer).where(
                Offer.merchant_id == mid,
                Offer.stock > 0,
                Offer.valid_from <= day,
                Offer.valid_to >= day,
            )
        ).all()
        offers = [o for o in offers if args.points is None or o.points_cost <= args.points]
        if offers:
            out.append(
                {
                    "merchant_id": mid,
                    "name": name,
                    "category": category,
                    "distance_km": round(distance, 2),
                    "offers": [
                        {"offer_id": o.id, "title": o.title, "points_cost": o.points_cost}
                        for o in offers
                    ],
                }
            )
    out.sort(key=lambda m: m["distance_km"])
    return {"merchants": out}
