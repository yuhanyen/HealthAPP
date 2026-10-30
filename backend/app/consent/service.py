"""同意管理（SPEC §5.1）。

撤回流程：寫 revoked_at → 建立刪除任務（24h SLA）→ commit 後發 consent.revoked
→ Adapter 群組立即執行刪除；排程每 10 分鐘補做未完成的任務 → 稽核紀錄保留不刪。
「立即停止拉取」靠 is_source_allowed()：Adapter 每次拉取前都查現行授權，撤回當下即生效。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from app.actions.state_machine import create_pending, register_executor
from app.audit.writer import ActorType, write_audit
from app.config import get_settings
from app.consent.catalog import FHIR_SOURCE_OF, SOURCE_ITEMS
from app.consent.schemas import ConsentScope, ExplainOut
from app.db.models_idn import (
    ChatMessage,
    Consent,
    DailyReport,
    DeletionJob,
    FhirResource,
    PendingAction,
    TwinSnapshot,
    User,
)
from app.events.outbox import enqueue
from app.i18n import t, t_list


class ConsentError(Exception):
    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        super().__init__(t(key, **params))


class ConsentNotFound(ConsentError):
    def __init__(self) -> None:
        super().__init__("consent.error.not_found")


class ConsentNotActive(ConsentError):
    def __init__(self) -> None:
        super().__init__("consent.error.already_revoked")


class InvalidConsentScope(ConsentError):
    def __init__(self, detail: str) -> None:
        super().__init__("consent.error.invalid_scope", detail=detail)


@dataclass(frozen=True)
class RevokeResult:
    consent: Consent
    job: DeletionJob


def _active(user_id: uuid.UUID) -> Any:
    return select(Consent).where(
        Consent.user_id == user_id, Consent.revoked_at.is_(None), Consent.superseded_at.is_(None)
    )


def list_active(session: Session, user_id: uuid.UUID) -> list[Consent]:
    return list(session.scalars(_active(user_id).order_by(Consent.source)))


def get_active(session: Session, user_id: uuid.UUID, source: str) -> Consent | None:
    result: Consent | None = session.scalars(
        _active(user_id).where(Consent.source == source)
    ).first()
    return result


def effective_scope(session: Session, user_id: uuid.UUID) -> dict[str, list[str]]:
    """本人目前實際授權：{source: [資料項目]}。"""
    return {c.source: sorted(c.scope.get("read", [])) for c in list_active(session, user_id)}


def is_source_allowed(
    session: Session, user_id: uuid.UUID, source: str, item: str | None = None
) -> bool:
    """Adapter 拉取前的閘門。撤回後立即回傳 False。"""
    scope = effective_scope(session, user_id)
    if source not in scope:
        return False
    return item is None or item in scope[source]


def _normalize_scope(
    session: Session, user_id: uuid.UUID, source: str, scope: ConsentScope
) -> dict[str, Any]:
    allowed = SOURCE_ITEMS.get(source)
    if allowed is None:
        raise ConsentError("consent.error.invalid_source", source=source)
    unknown = sorted(set(scope.read) - set(allowed))
    if unknown:
        raise InvalidConsentScope(
            f"{source} 沒有 {', '.join(unknown)}（可選：{', '.join(allowed)}）"
        )
    for viewer in scope.share_summary_to:
        if viewer == user_id or session.get(User, viewer) is None:
            raise InvalidConsentScope(f"分享對象 {viewer} 不存在")
    return {
        "read": sorted(set(scope.read)),
        "share_summary_to": [str(v) for v in scope.share_summary_to],
        "allow_proxy_redeem": scope.allow_proxy_redeem,
    }


def grant(
    session: Session,
    *,
    user_id: uuid.UUID,
    source: str,
    scope: ConsentScope,
    actor_type: ActorType,
    actor_id: str,
) -> Consent:
    """建立或更新授權。更新時舊版本標 superseded_at，新版本 version+1（保留同意歷程）。"""
    normalized = _normalize_scope(session, user_id, source, scope)
    current = session.scalars(
        _active(user_id).where(Consent.source == source).with_for_update()
    ).first()
    now = datetime.now(UTC)
    version = 1
    if current is not None:
        current.superseded_at = now
        version = current.version + 1
        session.flush()  # 先讓舊版本離開「現行」的唯一索引
    consent = Consent(
        user_id=user_id, source=source, scope=normalized, granted_at=now, version=version
    )
    session.add(consent)
    session.flush()
    write_audit(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="consent.grant" if version == 1 else "consent.update",
        decision="executed",
        consent_scope={source: normalized["read"]},
        input={"source": source, "scope": normalized},
        output={"consent_id": consent.id, "version": version},
    )
    return consent


def revoke_impacts(source: str) -> list[str]:
    return t_list(f"consent.source.{source}.impacts")


def revoke_confirmation_text(source: str) -> str:
    """撤回流程唯一允許的文案（CLAUDE.md：不得加入任何挽留話術）。"""
    return t("consent.revoke.confirm", impacts="、".join(revoke_impacts(source)))


def explain(source: str, *, k: int) -> ExplainOut:
    if source not in SOURCE_ITEMS:
        raise ConsentError("consent.error.invalid_source", source=source)
    base = f"consent.source.{source}"
    return ExplainOut(
        source=source,
        name=t(f"{base}.name"),
        data=t(f"{base}.data"),
        purpose=t(f"{base}.purpose"),
        items=list(SOURCE_ITEMS[source]),
        who_sees=[
            t("consent.who_sees.self"),
            t("consent.who_sees.family"),
            t("consent.who_sees.agency", k=k),
            t("consent.who_sees.merchant"),
        ],
        impacts_if_revoked=revoke_impacts(source),
    )


def _owned_active(
    session: Session, user_id: uuid.UUID, consent_id: uuid.UUID, *, lock: bool = False
) -> Consent:
    consent = session.get(Consent, consent_id, with_for_update=lock)
    if consent is None or consent.user_id != user_id:
        raise ConsentNotFound()
    if consent.revoked_at is not None or consent.superseded_at is not None:
        raise ConsentNotActive()
    return consent


def request_revoke(
    session: Session, *, user_id: uuid.UUID, consent_id: uuid.UUID, created_by: str = "user"
) -> PendingAction:
    """撤回是高風險動作（C-5）：先建立待確認動作，本人確認後才執行。"""
    consent = _owned_active(session, user_id, consent_id)
    return create_pending(
        session,
        user_id=user_id,
        action_type="consent_revoke",
        payload={"consent_id": str(consent.id), "source": consent.source},
        summary=revoke_confirmation_text(consent.source),
        created_by=created_by,
    )


def revoke(
    session: Session,
    *,
    user_id: uuid.UUID,
    consent_id: uuid.UUID,
    actor_type: ActorType,
    actor_id: str,
    now: datetime | None = None,
) -> RevokeResult:
    consent = _owned_active(session, user_id, consent_id, lock=True)
    now = now or datetime.now(UTC)
    consent.revoked_at = now
    job = DeletionJob(
        user_id=user_id,
        consent_id=consent.id,
        source=consent.source,
        requested_at=now,
        due_at=now + timedelta(hours=get_settings().deletion_sla_hours),
    )
    session.add(job)
    session.flush()
    write_audit(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="consent.revoke",
        decision="executed",
        consent_scope={consent.source: consent.scope.get("read", [])},
        input={"consent_id": consent.id},
        output={"deletion_job_id": job.id, "due_at": job.due_at},
    )
    enqueue(
        session,
        "consent.revoked",
        user_id=str(user_id),
        source=consent.source,
        consent_id=str(consent.id),
        deletion_job_id=str(job.id),
    )
    return RevokeResult(consent=consent, job=job)


@register_executor("consent_revoke")
def _execute_revoke(session: Session, action: PendingAction) -> dict[str, Any]:
    result = revoke(
        session,
        user_id=action.user_id,
        consent_id=uuid.UUID(action.payload["consent_id"]),
        actor_type="user",
        actor_id=str(action.user_id),
    )
    return {
        "consent_id": str(result.consent.id),
        "source": result.consent.source,
        "deletion_job_id": str(result.job.id),
        "deletion_due_at": result.job.due_at.isoformat(),
        "message": t("consent.revoke.done"),
    }


def run_deletion_job(
    session: Session, job_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, int] | None:
    """執行撤回後的資料刪除。已完成或被其他 worker 鎖住的任務回傳 None。"""
    job = session.get(DeletionJob, job_id, with_for_update={"skip_locked": True})
    if job is None or job.status != "pending":
        return None
    counts: dict[str, int] = {}
    fhir_source = FHIR_SOURCE_OF.get(job.source)
    if fhir_source is not None:
        deleted = session.execute(
            delete(FhirResource).where(
                FhirResource.user_id == job.user_id, FhirResource.source == fhir_source
            )
        )
        counts["fhir_resources"] = cast(CursorResult[Any], deleted).rowcount
        # 雙生快照含此來源的衍生資料：整份刪除，下次批次以剩餘授權重建
        snapshots = session.execute(delete(TwinSnapshot).where(TwinSnapshot.user_id == job.user_id))
        counts["twin_snapshots"] = cast(CursorResult[Any], snapshots).rowcount
    # 引用過此來源的每日報告、以及無法判斷引用了什麼的對話紀錄，一併刪除（A1 之後不會再提到）
    reports = session.execute(
        delete(DailyReport).where(
            DailyReport.user_id == job.user_id,
            DailyReport.content["sources_used"].has_key(job.source),
        )
    )
    counts["daily_reports"] = cast(CursorResult[Any], reports).rowcount
    chats = session.execute(delete(ChatMessage).where(ChatMessage.user_id == job.user_id))
    counts["chat_messages"] = cast(CursorResult[Any], chats).rowcount
    job.status = "done"
    job.executed_at = now or datetime.now(UTC)
    job.deleted_counts = counts
    write_audit(
        session,
        actor_type="system",
        actor_id="deletion_job",
        action="consent.data_deleted",
        decision="executed",
        input={"job_id": job.id, "user_id": job.user_id, "source": job.source},
        output=counts,
        reason=f"撤回授權後刪除（SLA {get_settings().deletion_sla_hours}h）",
    )
    return counts


def pending_deletion_jobs(session: Session) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(DeletionJob.id)
            .where(DeletionJob.status == "pending")
            .order_by(DeletionJob.due_at)
        )
    )
