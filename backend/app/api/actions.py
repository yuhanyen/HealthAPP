"""高風險動作確認（SPEC §9：POST /me/actions/{action_id}/confirm）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.actions import state_machine
from app.api.deps import DB
from app.auth.deps import CurrentCitizen
from app.db.models_idn import PendingAction

router = APIRouter(prefix="/me/actions", tags=["me 民眾"])


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type: str
    summary: str
    status: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    executed_at: datetime | None
    result: dict[str, Any] | None


@router.get("", response_model=list[ActionOut], summary="待我確認的動作")
def list_pending(principal: CurrentCitizen, db: DB) -> list[PendingAction]:
    assert principal.user_id is not None
    return state_machine.list_pending(db, principal.user_id)


@router.post("/{action_id}/confirm", response_model=ActionOut, summary="確認並執行高風險動作")
def confirm_action(action_id: uuid.UUID, principal: CurrentCitizen, db: DB) -> PendingAction:
    assert principal.user_id is not None
    return state_machine.confirm(db, action_id, user_id=principal.user_id)


@router.post("/{action_id}/cancel", response_model=ActionOut, summary="取消（不執行）")
def cancel_action(action_id: uuid.UUID, principal: CurrentCitizen, db: DB) -> PendingAction:
    assert principal.user_id is not None
    return state_machine.cancel(db, action_id, user_id=principal.user_id)
