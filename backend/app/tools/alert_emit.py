"""alert_emit（SPEC §8.2 A4）：寫入輿情警訊並發 sentiment.alert（給 A2）。

寫入用 anon_writer 服務連線（A4 本身的讀取帳號是 anon_reader）；事件不含任何可識別欄位。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentAlert
from app.events.outbox import enqueue
from app.tools._common import AgencyToolArgs
from app.tools.registry import ToolContext, registry


class AlertArgs(AgencyToolArgs):
    village_code: str
    topic: str
    category: str = Field(default="complaint_spike")
    count: int = Field(ge=0)
    baseline: float = Field(ge=0)
    ratio: float = Field(ge=0)


@registry.tool(name="alert_emit", schema=AlertArgs, side="agency")
def alert_emit(ctx: ToolContext, args: AlertArgs) -> dict[str, Any]:
    """發出輿情警訊（寫入 anon.sentiment_alerts，並通知 A2）。"""
    with session_scope(DbRole.ANON_WRITER) as s:
        alert = SentimentAlert(
            village_code=args.village_code,
            topic=args.topic,
            category=args.category,
            count=args.count,
            baseline=Decimal(str(args.baseline)),
            ratio=Decimal(str(args.ratio)),
            status="open",
        )
        s.add(alert)
        s.flush()
        enqueue(
            s,
            "sentiment.alert",
            alert_id=alert.id,
            village_code=args.village_code,
            topic=args.topic,
        )
        return {"alert_id": alert.id}
