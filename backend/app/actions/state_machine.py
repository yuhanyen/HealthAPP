"""C-5：高風險動作確認狀態機。

    pending ──本人確認──▶ confirmed ──執行成功──▶ executed
       │                       └──執行失敗──▶ failed
       ├──逾 24h（SPEC §13-4）──▶ expired
       └──本人取消──▶ cancelled

只有 confirm() 會觸發執行，而 confirm() 只由本人已登入的 /me/actions/{id}/confirm 呼叫；
Agent 傳入的 confirmed／action_id 參數會被工具層剝除，無法自行確認。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.writer import jsonable, write_audit
from app.config import get_settings
from app.db.engines import DbRole, get_engine
from app.db.models_idn import HIGH_RISK_ACTIONS as _HIGH_RISK
from app.db.models_idn import PendingAction
from app.i18n import t

if TYPE_CHECKING:
    from app.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

HIGH_RISK_ACTIONS: frozenset[str] = frozenset(_HIGH_RISK)

Executor = Callable[[Session, PendingAction], dict[str, Any]]
_EXECUTORS: dict[str, Executor] = {}


class ActionError(Exception):
    key = "action.error.not_found"

    def __init__(self, **params: object) -> None:
        super().__init__(t(self.key, **params))


class ActionNotFound(ActionError):
    key = "action.error.not_found"


class ActionExpired(ActionError):
    key = "action.error.expired"


class InvalidActionState(ActionError):
    key = "action.error.invalid_state"


class NotActionOwner(ActionError):
    key = "action.error.not_owner"


class UnsupportedAction(ActionError):
    key = "action.error.unsupported"


def register_executor(action_type: str) -> Callable[[Executor], Executor]:
    """不經工具層的高風險動作（例如 API 直接撤回授權）在此登記執行函式。"""

    def decorator(fn: Executor) -> Executor:
        _EXECUTORS[action_type] = fn
        return fn

    return decorator


def create_pending(
    session: Session,
    *,
    user_id: uuid.UUID,
    action_type: str,
    payload: dict[str, Any],
    summary: str,
    created_by: str,
    tool: str | None = None,
    now: datetime | None = None,
) -> PendingAction:
    if action_type not in HIGH_RISK_ACTIONS:
        raise UnsupportedAction(action_type=action_type)
    now = now or datetime.now(UTC)
    action = PendingAction(
        user_id=user_id,
        action_type=action_type,
        tool=tool,
        payload=jsonable(payload),
        summary=summary,
        status="pending",
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().confirm_ttl_hours),
    )
    session.add(action)
    session.flush()
    return action


def _load_owned(session: Session, action_id: uuid.UUID, user_id: uuid.UUID) -> PendingAction:
    action = session.get(PendingAction, action_id)
    if action is None:
        raise ActionNotFound()
    if action.user_id != user_id:
        raise NotActionOwner()
    return action


def _mark_expired(action_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """另開交易標記逾時，呼叫端因例外 rollback 也不會還原。"""
    with get_engine(DbRole.IDN_APP).begin() as conn:
        conn.execute(
            update(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.status == "pending")
            .values(status="expired")
        )
    write_audit(
        None,
        actor_type="user",
        actor_id=str(user_id),
        action="action.confirm",
        decision="rejected",
        input={"action_id": action_id},
        reason="逾時作廢",
    )


def confirm(
    session: Session,
    action_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
    registry: ToolRegistry | None = None,
) -> PendingAction:
    """本人確認並執行。重複確認、逾時、非本人一律拒絕；執行恰好一次。"""
    now = now or datetime.now(UTC)
    action = _load_owned(session, action_id, user_id)
    if action.status == "pending" and now > action.expires_at:
        _mark_expired(action_id, user_id)
        raise ActionExpired()

    # 上鎖後再檢查一次狀態，避免兩個請求同時確認而執行兩次
    session.refresh(action, with_for_update=True)
    if action.status != "pending":
        raise InvalidActionState()

    action.status = "confirmed"
    action.confirmed_at = now
    session.flush()
    write_audit(
        session,
        actor_type="user",
        actor_id=str(user_id),
        action="action.confirm",
        decision="executed",
        input={"action_id": action.id, "action_type": action.action_type},
    )

    try:
        with session.begin_nested():
            result = _execute(session, action, registry)
    except Exception as exc:
        log.exception("確認後執行失敗：%s", action.id)
        action.status = "failed"
        action.result = {"error": type(exc).__name__}
        # 業務錯誤（帶 i18n key，例如名額已滿、點數不足）把白話原因交給前端；其他錯誤不外露
        if isinstance(getattr(exc, "key", None), str):
            action.result["message"] = str(exc)
        session.flush()
        return action

    action.status = "executed"
    action.executed_at = datetime.now(UTC)
    action.result = jsonable(result)
    session.flush()
    return action


def _execute(
    session: Session, action: PendingAction, registry: ToolRegistry | None
) -> dict[str, Any]:
    if action.tool is not None:
        if registry is None:
            from app.tools.registry import registry as default_registry

            registry = default_registry
        return registry.execute_confirmed(session, action)
    executor = _EXECUTORS.get(action.action_type)
    if executor is None:
        raise UnsupportedAction(action_type=action.action_type)
    return executor(session, action)


def cancel(session: Session, action_id: uuid.UUID, *, user_id: uuid.UUID) -> PendingAction:
    action = _load_owned(session, action_id, user_id)
    session.refresh(action, with_for_update=True)
    if action.status != "pending":
        raise InvalidActionState()
    action.status = "cancelled"
    session.flush()
    write_audit(
        session,
        actor_type="user",
        actor_id=str(user_id),
        action="action.cancel",
        decision="executed",
        input={"action_id": action.id},
    )
    return action


def list_pending(session: Session, user_id: uuid.UUID) -> list[PendingAction]:
    return list(
        session.scalars(
            select(PendingAction)
            .where(PendingAction.user_id == user_id, PendingAction.status == "pending")
            .order_by(PendingAction.created_at)
        )
    )


def expire_due(session: Session, now: datetime | None = None) -> int:
    result = session.execute(
        update(PendingAction)
        .where(
            PendingAction.status == "pending", PendingAction.expires_at < (now or datetime.now(UTC))
        )
        .values(status="expired")
        .returning(PendingAction.id)
    )
    return len(result.all())
