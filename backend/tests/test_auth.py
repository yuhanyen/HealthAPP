"""自建簡版身分驗證：登入、角色、管理端點。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from tests.conftest import Factory
from tests.helpers import audit_count, auth


def _login(client: TestClient, username: str, password: str) -> tuple[int, dict[str, str]]:
    response = client.post("/api/v1/auth/token", data={"username": username, "password": password})
    return response.status_code, response.json()


def test_login_and_me(client: TestClient, factory: Factory) -> None:
    user = factory.user()
    account = factory.account("citizen", user_id=user.id, password="correct-horse")
    status, body = _login(client, account.username, "correct-horse")
    assert status == 200
    assert body["role"] == "citizen"
    me = client.get("/api/v1/auth/me", headers=auth(body["access_token"])).json()
    assert me["user_id"] == str(user.id)
    assert audit_count(actor_id=str(account.id), action="auth.login", decision="executed") == 1


def test_wrong_password_is_rejected_and_audited(client: TestClient, factory: Factory) -> None:
    account = factory.account("agency", password="right")
    status, body = _login(client, account.username, "wrong")
    assert status == 401
    assert body["detail"] == "帳號或密碼錯誤"
    assert audit_count(actor_id=f"username:{account.username}", decision="rejected") == 1


def test_garbage_token_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me", headers=auth("not-a-jwt")).status_code == 401


def test_admin_endpoints_require_admin(client: TestClient, factory: Factory) -> None:
    _, _, citizen_token = factory.citizen()
    admin_token = create_access_token(factory.account("admin"))
    assert client.get("/api/v1/admin/audit", headers=auth(citizen_token)).status_code == 403
    rows = client.get("/api/v1/admin/audit?limit=5", headers=auth(admin_token)).json()
    assert len(rows) == 5
    assert {"input_digest", "decision", "consent_scope"} <= rows[0].keys()


def test_admin_config_change_is_validated_and_audited(client: TestClient, factory: Factory) -> None:
    admin = factory.account("admin")
    token = create_access_token(admin)
    assert (
        client.put(
            "/api/v1/admin/config", headers=auth(token), json={"key": "k", "value": 1}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/admin/config", headers=auth(token), json={"key": "root", "value": 1}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/admin/config", headers=auth(token), json={"key": "k", "value": 5}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/admin/config", headers=auth(token)).json()["k"] == 5
    assert audit_count(actor_id=str(admin.id), action="config.update") == 1


def test_health(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["database"]["ok"] is True
    assert body["event_bus"]["ok"] is True
