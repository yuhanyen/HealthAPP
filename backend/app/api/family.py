"""守護圈 API（SPEC §9：GET/POST /me/family、GET /family/{owner_id}/summary）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, model_validator

from app.actions.state_machine import confirm
from app.api.deps import DB
from app.auth.deps import CurrentCitizen
from app.services import family

router = APIRouter(tags=["family 守護圈"])


def _uid(principal: CurrentCitizen) -> uuid.UUID:
    assert principal.user_id is not None
    return principal.user_id


class LinkOut(BaseModel):
    link_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    level: str
    granted_at: datetime


class FamilyOut(BaseModel):
    sharing: list[LinkOut]  # 我分享給誰
    shared_with_me: list[LinkOut]  # 誰分享給我（我可以看他們的摘要）


@router.get("/me/family", response_model=FamilyOut, summary="守護圈設定")
def my_family(principal: CurrentCitizen, db: DB) -> FamilyOut:
    mine, theirs = family.links(db, _uid(principal))
    return FamilyOut(
        sharing=[LinkOut(**vars(v)) for v in mine],
        shared_with_me=[LinkOut(**vars(v)) for v in theirs],
    )


class InviteOut(BaseModel):
    code: str
    expires_at: datetime


@router.get(
    "/me/family/code", response_model=InviteOut, summary="我的守護碼（給家人輸入，30 分鐘）"
)
def my_invite_code(principal: CurrentCitizen) -> InviteOut:
    code, expires = family.issue_invite(_uid(principal))
    return InviteOut(code=code, expires_at=expires)


class ShareIn(BaseModel):
    viewer_code: str | None = None
    viewer_user_id: uuid.UUID | None = None
    level: Literal["summary"] = "summary"
    confirmed: bool = False

    @model_validator(mode="after")
    def _one_viewer(self) -> ShareIn:
        if (self.viewer_code is None) == (self.viewer_user_id is None):
            raise ValueError("viewer_code 與 viewer_user_id 擇一")
        return self


class ShareOut(BaseModel):
    status: Literal["confirmation_required", "executed", "failed"]
    action_id: uuid.UUID
    summary: str
    result: dict[str, Any] | None = None


@router.post(
    "/me/family",
    response_model=ShareOut,
    responses={202: {"description": "需本人確認（回傳 action_id）"}},
    summary="分享摘要給家屬（高風險：需本人確認）",
)
def share(body: ShareIn, principal: CurrentCitizen, db: DB, response: Response) -> ShareOut:
    uid = _uid(principal)
    viewer = body.viewer_user_id or family.read_invite(body.viewer_code or "")
    action = family.request_share(db, owner_id=uid, viewer_id=viewer, level=body.level)
    if not body.confirmed:
        response.status_code = status.HTTP_202_ACCEPTED
        return ShareOut(status="confirmation_required", action_id=action.id, summary=action.summary)
    done = confirm(db, action.id, user_id=uid)
    return ShareOut(
        status="executed" if done.status == "executed" else "failed",
        action_id=done.id,
        summary=done.summary,
        result=done.result,
    )


@router.delete("/me/family/{link_id}", status_code=204, summary="收回分享（立即生效）")
def unshare(link_id: uuid.UUID, principal: CurrentCitizen, db: DB) -> Response:
    family.revoke_link(db, owner_id=_uid(principal), link_id=link_id)
    return Response(status_code=204)


class SummaryOut(BaseModel):
    owner_display_name: str
    as_of: str | None
    status_sentence: str
    risk_level: str | None
    risk_label: str | None
    pending_action: str | None


@router.get(
    "/family/{owner_id}/summary",
    response_model=SummaryOut,
    summary="家屬視角：只回摘要層（狀態一句話、風險等級、是否有待辦），沒有數值",
)
def family_summary(owner_id: uuid.UUID, principal: CurrentCitizen, db: DB) -> SummaryOut:
    return SummaryOut(**family.summary(db, viewer_id=_uid(principal), owner_id=owner_id))
