"""C-6：寫入 sys.audit_log（append-only，DB trigger 禁止改刪）。

輸入輸出只存 SHA-256 摘要，不存原始值——稽核本身不能變成另一份個資。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.db.engines import DbRole, get_engine
from app.db.models_sys import AuditLog

ActorType = Literal["user", "staff", "agent", "admin", "system"]


def jsonable(obj: Any) -> Any:
    """轉成可穩定序列化的 JSON 結構（set 排序、UUID／日期轉字串）。"""
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, set | frozenset):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, uuid.UUID | datetime | date):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "model_dump"):
        return jsonable(obj.model_dump(mode="json"))
    return obj


def digest(obj: object) -> str | None:
    if obj is None:
        return None
    payload = json.dumps(jsonable(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_audit(
    target: Session | Connection | None,
    *,
    actor_type: ActorType,
    actor_id: str,
    action: str,
    decision: str,
    tool: str | None = None,
    consent_scope: Mapping[str, Any] | None = None,
    input: object = None,
    output: object = None,
    reason: str | None = None,
) -> int:
    """寫一筆稽核並回傳 id。

    target=None：用 audit_writer 角色另開交易立即 commit（呼叫端 rollback 也不會消失，
    用於「被拒絕」等必須留痕的情境）；傳入 Session／Connection 則與呼叫端同一交易。
    """
    stmt = (
        insert(AuditLog)
        .values(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            tool=tool,
            consent_scope=jsonable(consent_scope) if consent_scope is not None else None,
            input_digest=digest(input),
            output_digest=digest(output),
            decision=decision,
            reason=reason,
        )
        .returning(AuditLog.id)
    )
    if target is None:
        with get_engine(DbRole.AUDIT_WRITER).begin() as conn:
            return int(conn.execute(stmt).scalar_one())
    return int(target.execute(stmt).scalar_one())
