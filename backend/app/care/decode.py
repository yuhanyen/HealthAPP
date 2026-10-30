"""關懷代號解碼（SPEC §7.3 decode_care_list、A5 SKILL 守門規則 1–2）。

- 每次解碼必附任務編號；沒有任務編號一律拒絕。
- 只解碼本據點的代號；範圍外回報公衛端重新分配。
- 成功：idn.decode_audit + sys.audit_log（同一交易）。被拒：另開交易寫稽核，rollback 也留痕。
- 只回傳做事需要的欄位（姓名、電話、地址、任務類型），不含任何健康資料。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.writer import write_audit
from app.db.models_idn import CareTask, DecodeAudit, User
from app.i18n import t

TOOL = "decode_care_list"


class DecodeRejected(Exception):
    def __init__(self, reason_key: str) -> None:
        self.reason_key = reason_key
        super().__init__(t(f"decode.error.{reason_key}"))


@dataclass(frozen=True)
class DecodedTask:
    code: str
    task_type: str
    task_ref: str
    name: str
    phone: str | None
    address: str | None


def _reject(
    staff_account_id: uuid.UUID, code: str, task_ref: str, site_id: str, key: str
) -> DecodeRejected:
    error = DecodeRejected(key)
    write_audit(
        None,
        actor_type="staff",
        actor_id=str(staff_account_id),
        action="care_task.decode",
        tool=TOOL,
        input={"code": code, "task_ref": task_ref, "site_id": site_id},
        decision="rejected",
        reason=str(error),
    )
    return error


def decode_care_task(
    session: Session, *, code: str, task_ref: str, staff_account_id: uuid.UUID, site_id: str
) -> DecodedTask:
    task_ref = task_ref.strip()
    if not task_ref:
        raise _reject(staff_account_id, code, task_ref, site_id, "task_ref_required")
    task = session.scalars(select(CareTask).where(CareTask.code == code)).first()
    if task is None:
        raise _reject(staff_account_id, code, task_ref, site_id, "not_found")
    if task.site_id != site_id:
        raise _reject(staff_account_id, code, task_ref, site_id, "out_of_site")

    user = session.get_one(User, task.user_id)
    session.add(
        DecodeAudit(
            care_task_code=code, decoded_by=staff_account_id, site_id=site_id, task_ref=task_ref
        )
    )
    write_audit(
        session,
        actor_type="staff",
        actor_id=str(staff_account_id),
        action="care_task.decode",
        tool=TOOL,
        input={"code": code, "task_ref": task_ref, "site_id": site_id},
        output={"code": code},
        decision="executed",
        reason=f"task_ref={task_ref}",
    )
    return DecodedTask(
        code=code,
        task_type=task.task_type,
        task_ref=task_ref,
        name=user.name,
        phone=user.phone,
        address=user.address,
    )
