"""nlp_entity（SPEC §7.2）：抽出文中提到的里名與據點名（只認 pub 內的名稱，不抽人名）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models_pub import Site, Village
from app.services import nlp
from app.tools.nlp_sentiment import NlpArgs
from app.tools.registry import ToolContext, registry


@registry.tool(name="nlp_entity", schema=NlpArgs, side="agency")
def nlp_entity(ctx: ToolContext, args: NlpArgs) -> dict[str, Any]:
    """逐則找出提到的里名或據點名。"""
    gazetteer = list(ctx.db.scalars(select(Village.name))) + list(ctx.db.scalars(select(Site.name)))
    return {"labels": [r["entities"] for r in nlp.batch(args.texts, gazetteer=gazetteer)]}
