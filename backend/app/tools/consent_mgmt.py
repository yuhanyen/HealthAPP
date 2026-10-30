"""consent_mgmt（SPEC §7.1）：查詢／說明／撤回本人的授權。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from app.config import get_settings
from app.consent import service as consent_service
from app.consent.schemas import Source
from app.i18n import t
from app.tools.registry import CitizenToolArgs, ToolContext, registry


class ConsentMgmtArgs(CitizenToolArgs):
    action: Literal["get", "explain", "revoke"]
    source: Source | None = None

    @model_validator(mode="after")
    def _source_required(self) -> ConsentMgmtArgs:
        if self.action in ("explain", "revoke") and self.source is None:
            raise ValueError("explain／revoke 需要指定 source")
        return self


@registry.tool(
    name="consent_mgmt",
    schema=ConsentMgmtArgs,
    side="citizen",
    high_risk=lambda args: args.action == "revoke",
    action_type="consent_revoke",
    summary=lambda args: consent_service.revoke_confirmation_text(args.source or ""),
)
def consent_mgmt(ctx: ToolContext, args: ConsentMgmtArgs) -> dict[str, Any]:
    """查詢本人目前授權（get）、白話說明某來源授權會讓誰看到什麼（explain）、撤回授權（revoke）。
    revoke 是高風險動作：系統會先請本人確認；撤回時只說明影響，不得勸留。"""
    if args.action == "get":
        return {"scope": consent_service.effective_scope(ctx.db, args.user_id)}
    assert args.source is not None
    if args.action == "explain":
        return consent_service.explain(args.source, k=get_settings().k_default).model_dump()

    # revoke：只會在本人確認後由狀態機以 confirmed=True 呼叫
    if not args.confirmed:
        raise PermissionError("撤回授權需本人確認")
    consent = consent_service.get_active(ctx.db, args.user_id, args.source)
    if consent is None:
        raise consent_service.ConsentNotFound()
    result = consent_service.revoke(
        ctx.db,
        user_id=args.user_id,
        consent_id=consent.id,
        actor_type="user",
        actor_id=str(args.user_id),
    )
    return {
        "revoked": args.source,
        "message": t("consent.revoke.done"),
        "deletion_due_at": result.job.due_at.isoformat(),
    }
