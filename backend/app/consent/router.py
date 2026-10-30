"""同意管理 API（SPEC §5.1）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.actions.state_machine import confirm
from app.api.deps import DB
from app.auth.deps import CurrentCitizen, CurrentPrincipal
from app.consent import service
from app.consent.schemas import ConsentCreate, ConsentOut, ExplainOut, RevokeOut, Source
from app.db.models_idn import Consent
from app.i18n import t
from app.sysconfig import get_k

router = APIRouter(prefix="/consents", tags=["consents 同意管理"])


@router.get("", response_model=list[ConsentOut], summary="本人目前授權")
def list_consents(
    principal: CurrentCitizen, db: DB, include_history: bool = False
) -> list[Consent]:
    assert principal.user_id is not None
    if not include_history:
        return service.list_active(db, principal.user_id)
    return list(
        db.scalars(
            select(Consent)
            .where(Consent.user_id == principal.user_id)
            .order_by(Consent.source, Consent.version)
        )
    )


@router.post(
    "", response_model=ConsentOut, status_code=status.HTTP_201_CREATED, summary="建立／更新授權"
)
def grant_consent(body: ConsentCreate, principal: CurrentCitizen, db: DB) -> Consent:
    assert principal.user_id is not None
    return service.grant(
        db,
        user_id=principal.user_id,
        source=body.source,
        scope=body.scope,
        actor_type="user",
        actor_id=str(principal.user_id),
    )


@router.delete(
    "/{consent_id}",
    response_model=RevokeOut,
    responses={202: {"description": "需本人確認（回傳 action_id）", "model": RevokeOut}},
    summary="撤回授權（高風險動作，需本人確認）",
)
def revoke_consent(
    consent_id: uuid.UUID,
    principal: CurrentCitizen,
    db: DB,
    response: Response,
    confirmed: bool = False,
) -> RevokeOut:
    """不帶 confirmed：回 202 與待確認動作（文案只有「確定要撤回嗎？撤回後您將失去…」）。
    App 已向本人顯示該確認文案時可直接帶 confirmed=true，同樣經確認狀態機執行並留稽核。"""
    assert principal.user_id is not None
    action = service.request_revoke(db, user_id=principal.user_id, consent_id=consent_id)
    if not confirmed:
        response.status_code = status.HTTP_202_ACCEPTED
        return RevokeOut(
            status="confirmation_required", message=action.summary, action_id=action.id
        )

    done = confirm(db, action.id, user_id=principal.user_id)
    if done.status != "executed" or done.result is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "撤回失敗，請稍後再試")
    consent = db.get_one(Consent, consent_id)
    return RevokeOut(
        status="revoked",
        message=t("consent.revoke.done"),
        action_id=action.id,
        consent=ConsentOut.model_validate(consent),
        deletion_job_id=uuid.UUID(done.result["deletion_job_id"]),
        deletion_due_at=done.result["deletion_due_at"],
    )


@router.get("/explain/{source}", response_model=ExplainOut, summary="白話說明授權會讓誰看到什麼")
def explain_consent(source: Source, principal: CurrentPrincipal, db: DB) -> ExplainOut:
    return service.explain(source, k=get_k(db))
