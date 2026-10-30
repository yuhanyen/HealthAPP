"""event_recommend（SPEC §17.3）：近日社區活動推薦，參考本人健康狀況、興趣、距離與點數。

- 健康理由只來自授權範圍內的資料（services/signals），文字經 C-7 過濾。
- 每場附 `push_title`／`push_body`：推播用，只有活動名稱、時間、地點、點數，不含任何健康理由。
- 名額只取人數（idn.event_seats_taken），不讀別人的報名。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field

from app.db.models_pub import EVENT_CATEGORIES
from app.i18n import t
from app.services import events as ev
from app.services import geo, prefs, signals
from app.tools._common import profile, safe_all, today
from app.tools.registry import CitizenToolArgs, ToolContext, registry

HEALTH_WEIGHT = 3.0
INTEREST_WEIGHT = 2.0
DISTANCE_WEIGHT = 0.8
MAX_HEALTH_REASONS = 2


class EventRecommendArgs(CitizenToolArgs):
    days: int = Field(default=14, ge=1, le=60)
    limit: int = Field(default=5, ge=1, le=20)
    category: str | None = Field(
        default=None, description="只看某一類：" + "、".join(EVENT_CATEGORIES)
    )


def _score(
    event: Any,
    *,
    found: dict[str, signals.Signal],
    interests: list[str],
    distance: float | None,
    now: datetime,
) -> tuple[float, list[str], list[str]]:
    tags = set(event.tags)
    score = 0.0
    reasons: list[str] = []
    basis: list[str] = []
    matched = 0
    for key, signal in found.items():
        tag = signals.EVENT_TAG_OF.get(key)
        if tag and tag in tags and matched < MAX_HEALTH_REASONS:
            score += HEALTH_WEIGHT if key != "gentle" else 1.0
            reasons.append(signal.reason)
            if signal.basis:
                basis.append(signal.basis)
            matched += key != "gentle"
    if "indoor" in found and event.start_at - now < timedelta(hours=48):
        if "outdoor" in tags:
            score -= 2.0
        elif "indoor" in tags:
            score += 0.5
            reasons.append(found["indoor"].reason)
    if event.category in interests:
        score += INTEREST_WEIGHT
        reasons.append(t("rec.reason.interest", category=t(f"events.category.{event.category}")))
    if event.points_reward > 0:
        score += 1.0 + min(event.points_reward, 100) / 100
        reasons.append(t("rec.reason.points", points=event.points_reward))
    if distance is not None:
        score -= distance * DISTANCE_WEIGHT
        reasons.append(t("rec.reason.near", km=f"{distance:.1f}"))
    return score, reasons, basis


@registry.tool(name="event_recommend", schema=EventRecommendArgs, side="citizen")
def event_recommend(ctx: ToolContext, args: EventRecommendArgs) -> dict[str, Any]:
    """推薦本人住家附近、近幾天的社區健康活動（量測、健走、衛教…），附推薦理由、點數與名額。"""
    now = datetime.now(UTC)
    day = today()
    me = profile(ctx.db, args.user_id, day)
    mine = prefs.get_prefs(ctx.db, args.user_id)
    found = signals.health_signals(ctx.db, args.user_id, args.consent_scope, day)
    home = ev.home_point(ctx.db, me.village_code)
    radius = mine.max_distance_km or prefs.DEFAULT_EVENT_DISTANCE_KM

    candidates = [
        e
        for e in ev.upcoming(ctx.db, now=now, days=args.days)
        if args.category is None or e.category == args.category
    ]
    taken = ev.seats_taken(ctx.db, [e.id for e in candidates])
    registered = ev.my_registrations(ctx.db, args.user_id)

    scored: list[tuple[float, dict[str, Any]]] = []
    for event in candidates:
        point = ev.location(ctx.db, event)
        distance = geo.distance_km(home, point) if home and point else None
        if distance is not None and distance > radius:
            continue
        mine_reg = registered.get(event.id)
        seats_left = (
            None if event.capacity is None else max(0, event.capacity - taken.get(event.id, 0))
        )
        if seats_left == 0 and (mine_reg is None or mine_reg.status == "cancelled"):
            continue
        score, reasons, basis = _score(
            event, found=found, interests=mine.event_categories, distance=distance, now=now
        )
        points_line = (
            t("events.push.points", points=event.points_reward) if event.points_reward else ""
        )
        scored.append(
            (
                score,
                {
                    **ev.event_out(event),
                    "distance_km": round(distance, 2) if distance is not None else None,
                    "seats_left": seats_left,
                    "registration": mine_reg.status if mine_reg else None,
                    "reasons": reasons,
                    "basis": basis,
                    "score": round(score, 2),
                    "push_title": t("events.push.title", title=event.title),
                    "push_body": t(
                        "events.push.body",
                        when=ev.when_text(event.start_at, event.end_at),
                        venue=event.venue,
                        points=points_line,
                    ),
                },
            )
        )
    scored.sort(key=lambda pair: (-pair[0], pair[1]["start_at"]))
    picked = [item for _, item in scored[: args.limit]]
    return safe_all(
        {
            "events": picked,
            "considered": len(candidates),
            "signals": [s.key for s in found.values()],
            "radius_km": radius,
            "note": t("rec.note.scope"),
        }
    )
