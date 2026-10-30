"""同意管理 API（SPEC §5.1）：建立／更新（版本化）、查詢、白話說明、權限。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from tests.conftest import Factory
from tests.helpers import audit_count, auth


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/consents").status_code == 401


def test_only_citizens_manage_consents(client: TestClient, factory: Factory) -> None:
    agency = factory.account("agency")
    assert (
        client.get("/api/v1/consents", headers=auth(create_access_token(agency))).status_code == 403
    )


def test_grant_then_update_keeps_version_history(client: TestClient, factory: Factory) -> None:
    user, _, token = factory.citizen()

    first = client.post(
        "/api/v1/consents",
        headers=auth(token),
        json={"source": "wearable", "scope": {"read": ["steps"]}},
    )
    assert first.status_code == 201
    assert first.json()["version"] == 1

    second = client.post(
        "/api/v1/consents",
        headers=auth(token),
        json={"source": "wearable", "scope": {"read": ["steps", "sleep", "steps"]}},
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2
    assert second.json()["scope"]["read"] == ["sleep", "steps"]

    active = client.get("/api/v1/consents", headers=auth(token)).json()
    assert [(c["source"], c["version"]) for c in active] == [("wearable", 2)]
    history = client.get("/api/v1/consents?include_history=true", headers=auth(token)).json()
    assert [c["version"] for c in history] == [1, 2]

    assert audit_count(actor_id=str(user.id), action="consent.grant") == 1
    assert audit_count(actor_id=str(user.id), action="consent.update") == 1


def test_unknown_items_are_rejected(client: TestClient, factory: Factory) -> None:
    _, _, token = factory.citizen()
    response = client.post(
        "/api/v1/consents",
        headers=auth(token),
        json={"source": "site", "scope": {"read": ["bp", "dna"]}},
    )
    assert response.status_code == 422
    assert "dna" in response.json()["detail"]


def test_empty_or_extra_scope_fields_are_rejected(client: TestClient, factory: Factory) -> None:
    _, _, token = factory.citizen()
    scopes: list[dict[str, object]] = [{"read": []}, {"read": ["bp"], "share_all": True}]
    for scope in scopes:
        response = client.post(
            "/api/v1/consents", headers=auth(token), json={"source": "site", "scope": scope}
        )
        assert response.status_code == 422


def test_share_target_must_exist(client: TestClient, factory: Factory) -> None:
    _, _, token = factory.citizen()
    response = client.post(
        "/api/v1/consents",
        headers=auth(token),
        json={"source": "site", "scope": {"read": ["bp"], "share_summary_to": [str(uuid.uuid4())]}},
    )
    assert response.status_code == 422


def test_cannot_revoke_someone_elses_consent(client: TestClient, factory: Factory) -> None:
    owner = factory.user()
    consent = factory.consent(owner.id, "nhi", ["visits"])
    _, _, intruder_token = factory.citizen()
    assert (
        client.delete(f"/api/v1/consents/{consent.id}", headers=auth(intruder_token)).status_code
        == 404
    )


def test_explain_says_who_sees_what(client: TestClient, factory: Factory) -> None:
    _, _, token = factory.citizen()
    body = client.get("/api/v1/consents/explain/nhi", headers=auth(token)).json()
    assert body["name"] == "健保署健康存摺就醫紀錄"
    assert "visits" in body["items"]
    assert any("每一格至少 5 人" in line for line in body["who_sees"])
    assert any("家屬" in line and "看不到任何數值" in line for line in body["who_sees"])
    assert body["impacts_if_revoked"]


def test_pending_actions_listing_and_cancel(client: TestClient, factory: Factory) -> None:
    user, _, token = factory.citizen()
    consent = factory.consent(user.id, "env", ["district"])
    action_id = client.delete(f"/api/v1/consents/{consent.id}", headers=auth(token)).json()[
        "action_id"
    ]

    pending = client.get("/api/v1/me/actions", headers=auth(token)).json()
    assert [a["id"] for a in pending] == [action_id]

    cancelled = client.post(f"/api/v1/me/actions/{action_id}/cancel", headers=auth(token))
    assert cancelled.json()["status"] == "cancelled"
    assert client.get("/api/v1/me/actions", headers=auth(token)).json() == []
    assert (
        client.post(f"/api/v1/me/actions/{action_id}/confirm", headers=auth(token)).status_code
        == 409
    )
    assert [c["source"] for c in client.get("/api/v1/consents", headers=auth(token)).json()] == [
        "env"
    ]
