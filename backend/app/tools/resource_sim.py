"""resource_sim（SPEC §7.2）：新增候選據點後的覆蓋率變化（以里中心點與人口估算，只用 pub）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.models_pub import Village
from app.services import geo
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    lat: float
    lng: float


class ResourceSimArgs(AgencyToolArgs):
    candidate_sites: list[Candidate] = Field(min_length=1)
    radius_km: float = Field(default=0.8, gt=0, le=10)


@registry.tool(name="resource_sim", schema=ResourceSimArgs, side="agency")
def resource_sim(ctx: ToolContext, args: ResourceSimArgs) -> dict[str, Any]:
    """模擬新增據點後，步行半徑內涵蓋的人口比例變化（里人口以里中心點近似）。"""
    villages = ctx.db.execute(
        select(Village.code, Village.name, Village.population, Village.geom)
    ).all()
    sites = [s[2] for s in geo.active_sites(ctx.db)]
    candidates = [(c.lat, c.lng) for c in args.candidate_sites]
    total = sum(v.population for v in villages) or 1

    def covered(points: list[tuple[float, float]]) -> set[str]:
        out = set()
        for v in villages:
            centre = geo.centroid(v.geom)
            if centre and any(geo.distance_km(centre, p) <= args.radius_km for p in points):
                out.add(v.code)
        return out

    before, after = covered(sites), covered(sites + candidates)
    pop = {v.code: v.population for v in villages}
    names = {v.code: v.name for v in villages}
    return {
        "radius_km": args.radius_km,
        "coverage_before": round(sum(pop[c] for c in before) / total, 4),
        "coverage_after": round(sum(pop[c] for c in after) / total, 4),
        "newly_covered": [names[c] for c in sorted(after - before)],
        "assumptions": [
            "里人口以里中心點代表，未考慮里內分布",
            f"以直線距離 {args.radius_km} 公里作為步行可及範圍",
            "未納入據點容量、開放時段與交通條件",
        ],
    }
