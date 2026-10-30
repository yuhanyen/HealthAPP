"""里與據點的簡易地理計算（pub 非個資；里中心點取 GeoJSON 多邊形頂點平均）。"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_pub import Site, Village


def centroid(geom: dict[str, Any] | None) -> tuple[float, float] | None:
    """(lat, lng)。"""
    if not geom or geom.get("type") != "Polygon":
        return None
    ring = geom["coordinates"][0][:-1] or geom["coordinates"][0]
    lng = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    return lat, lng


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


def village_centroids(session: Session) -> dict[str, tuple[float, float]]:
    out = {}
    for code, geom in session.execute(select(Village.code, Village.geom)):
        point = centroid(geom)
        if point:
            out[code] = point
    return out


def active_sites(session: Session) -> list[tuple[str, str, tuple[float, float]]]:
    rows = session.execute(select(Site.id, Site.name, Site.lat, Site.lng).where(Site.active))
    return [(sid, name, (lat, lng)) for sid, name, lat, lng in rows]


def nearest_site(session: Session, village_code: str) -> str | None:
    centre = village_centroids(session).get(village_code)
    sites = active_sites(session)
    if centre is None or not sites:
        return sites[0][0] if sites else None
    return min(sites, key=lambda s: distance_km(centre, s[2]))[0]
