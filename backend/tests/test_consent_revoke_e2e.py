"""M1 驗收：撤回流程 e2e（SPEC §5.1、§15 情境 3）。

寫 revoked_at → 發 consent.revoked → Adapter 停止 → 刪除任務 24h 內執行 → 稽核保留 → 無挽留文案。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.consent import service as consent_service
from app.consent.catalog import SOURCE_ITEMS
from app.db.engines import DbRole, session_scope
from app.db.models_idn import DeletionJob, FhirResource
from app.events.bus import EventBus
from app.events.consumer import consume_once
from app.scheduler import jobs
from tests.conftest import Factory
from tests.helpers import audit_count, auth

RETENTION_PHRASES = (
    "再想想",
    "建議保留",
    "可惜",
    "真的要",
    "確定不",
    "後悔",
    "損失",
    "加碼",
    "優惠",
    "挽留",
)


def _fhir_count(user_id: Any, source: str) -> int:
    with session_scope(DbRole.IDN_APP) as s:
        return int(
            s.execute(
                select(func.count())
                .select_from(FhirResource)
                .where(FhirResource.user_id == user_id, FhirResource.source == source)
            ).scalar_one()
        )


def _job_for(consent_id: Any) -> DeletionJob:
    with session_scope(DbRole.IDN_APP) as s:
        job = s.scalars(select(DeletionJob).where(DeletionJob.consent_id == consent_id)).one()
        s.expunge(job)
        return job


def test_revoke_flow_end_to_end(client: TestClient, factory: Factory, bus: EventBus) -> None:
    user, _, token = factory.citizen()
    nhi = factory.consent(user.id, "nhi", ["visits", "medications"])
    factory.consent(user.id, "site", ["bp"])
    for days_ago in range(3):
        factory.fhir(user.id, "nhi", days_ago=days_ago)
        factory.fhir(user.id, "site", days_ago=days_ago)

    # 1. 申請撤回：只回確認文案，尚未生效
    requested = client.delete(f"/api/v1/consents/{nhi.id}", headers=auth(token))
    assert requested.status_code == 202
    body = requested.json()
    assert body["message"].startswith("確定要撤回嗎？撤回後您將失去：")
    assert not any(p in body["message"] for p in RETENTION_PHRASES)
    with session_scope(DbRole.IDN_APP) as s:
        assert consent_service.is_source_allowed(s, user.id, "nhi")

    # 2. 本人確認
    confirmed = client.post(f"/api/v1/me/actions/{body['action_id']}/confirm", headers=auth(token))
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "executed"

    # 3. 立即停止該來源拉取；其他來源不受影響
    with session_scope(DbRole.IDN_APP) as s:
        assert not consent_service.is_source_allowed(s, user.id, "nhi")
        assert consent_service.is_source_allowed(s, user.id, "site", "bp")
    listed = client.get("/api/v1/consents", headers=auth(token)).json()
    assert [c["source"] for c in listed] == ["site"]

    # 4. commit 後發出 consent.revoked（SPEC §6 訊息格式）
    revoked = [d.event for d in bus.recent(20) if d.event["type"] == "consent.revoked"]
    assert len(revoked) == 1
    assert revoked[0] | {"at": "x"} == {
        "type": "consent.revoked",
        "user_id": str(user.id),
        "source": "nhi",
        "consent_id": str(nhi.id),
        "deletion_job_id": revoked[0]["deletion_job_id"],
        "at": "x",
    }

    # 5. Adapter 群組消費 → 刪除該來源資料（24h SLA）；site 資料保留
    assert _job_for(nhi.id).status == "pending"
    assert consume_once(bus, "adapters") == 1
    job = _job_for(nhi.id)
    assert job.status == "done"
    assert job.due_at - job.requested_at == timedelta(hours=24)
    assert job.executed_at is not None and job.executed_at <= job.due_at
    assert job.deleted_counts == {
        "fhir_resources": 3,
        "twin_snapshots": 0,
        "daily_reports": 0,
        "chat_messages": 0,
    }
    assert _fhir_count(user.id, "nhi") == 0
    assert _fhir_count(user.id, "site") == 3

    # 6. A1 群組也收到（後續報告不再引用就醫資料）
    assert consume_once(bus, "a1") == 1

    # 7. 稽核保留：撤回與刪除都有紀錄
    assert audit_count(actor_id=str(user.id), action="consent.revoke") == 1
    assert audit_count(actor_id=str(user.id), action="action.confirm", decision="executed") == 1

    # 8. 重複撤回 → 409
    again = client.delete(f"/api/v1/consents/{nhi.id}", headers=auth(token))
    assert again.status_code == 409


def test_revoke_with_confirmed_flag_executes_in_one_call(
    client: TestClient, factory: Factory
) -> None:
    user, _, token = factory.citizen()
    consent = factory.consent(user.id, "wearable", ["steps"])
    response = client.delete(f"/api/v1/consents/{consent.id}?confirmed=true", headers=auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "revoked"
    assert body["message"] == "已撤回。"
    assert body["deletion_job_id"]


def test_sweep_deletes_even_if_event_is_never_consumed(factory: Factory, bus: EventBus) -> None:
    user = factory.user()
    consent = factory.consent(user.id, "wearable", ["steps"])
    factory.fhir(user.id, "wearable")
    with session_scope(DbRole.IDN_APP) as s:
        consent_service.revoke(
            s, user_id=user.id, consent_id=consent.id, actor_type="user", actor_id=str(user.id)
        )

    assert jobs.sweep_deletion_jobs() >= 1
    assert _job_for(consent.id).status == "done"
    assert _fhir_count(user.id, "wearable") == 0


def test_failed_deletion_is_redelivered(
    factory: Factory, bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = factory.user()
    consent = factory.consent(user.id, "site", ["bp"])
    factory.fhir(user.id, "site")
    with session_scope(DbRole.IDN_APP) as s:
        consent_service.revoke(
            s, user_id=user.id, consent_id=consent.id, actor_type="user", actor_id=str(user.id)
        )

    import app.events.handlers as handlers

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("DB 暫時無法連線")

    monkeypatch.setattr(handlers, "run_deletion_job", boom)
    assert consume_once(bus, "adapters") == 0
    assert _job_for(consent.id).status == "pending"

    monkeypatch.undo()
    assert consume_once(bus, "adapters") == 1  # pending 訊息重送
    assert _job_for(consent.id).status == "done"


@pytest.mark.parametrize("source", sorted(SOURCE_ITEMS))
def test_revoke_copy_is_only_the_permitted_template(source: str) -> None:
    text = consent_service.revoke_confirmation_text(source)
    assert text.startswith("確定要撤回嗎？撤回後您將失去：")
    assert not any(p in text for p in RETENTION_PHRASES)
