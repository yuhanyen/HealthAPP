"""M2 API：民眾 QR、據點量測上傳、商家優惠、機關群體統計。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.merchant import hash_api_key
from app.auth.security import create_access_token
from app.auth.user_qr import issue_user_qr
from app.db.engines import DbRole, session_scope
from app.db.models_sys import Account
from app.events.bus import EventBus
from app.services import artifacts
from tests.conftest import Factory
from tests.helpers import OTHER_SITE, TEST_SITE, audit_rows, auth


def _staff_token(factory: Factory, site: str = TEST_SITE) -> tuple[Account, str]:
    staff = factory.account("site_staff", site_id=site)
    return staff, create_access_token(staff)


def test_citizen_gets_short_lived_qr(client: TestClient, factory: Factory) -> None:
    _, _, token = factory.citizen()
    body = client.get("/api/v1/me/qr", headers=auth(token)).json()
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=9) < expires - datetime.now(UTC) <= timedelta(minutes=10)


def test_staff_uploads_measurement(client: TestClient, factory: Factory, bus: EventBus) -> None:
    user = factory.user(name="陳美玲")
    factory.consent(user.id, "site", ["bp", "weight"])
    staff, token = _staff_token(factory)
    qr, _ = issue_user_qr(user.id)
    r = client.post(
        f"/api/v1/sites/{TEST_SITE}/measurements",
        headers=auth(token),
        json={"user_qr": qr, "items": {"bp": [142, 91], "weight": 58.4, "glucose": 101}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["person"] == "陳○玲"
    assert (body["written"], body["rejected_items"]) == (2, 1)  # 血糖不在授權範圍
    assert [d.event["type"] for d in bus.recent(5)].count("measurement.created") == 2
    assert [a.decision for a in audit_rows(actor_id=str(staff.id))] == ["executed"]


def test_upload_rejected_without_consent(client: TestClient, factory: Factory) -> None:
    user = factory.user()
    staff, token = _staff_token(factory)
    qr, _ = issue_user_qr(user.id)
    r = client.post(
        f"/api/v1/sites/{TEST_SITE}/measurements",
        headers=auth(token),
        json={"user_qr": qr, "items": {"bp": [130, 85]}},
    )
    assert r.status_code == 403
    assert [a.decision for a in audit_rows(actor_id=str(staff.id))] == ["rejected"]


def test_staff_limited_to_own_site_and_valid_qr(client: TestClient, factory: Factory) -> None:
    user = factory.user()
    factory.consent(user.id, "site", ["bp"])
    _, token = _staff_token(factory, site=OTHER_SITE)
    qr, _ = issue_user_qr(user.id)
    body = {"user_qr": qr, "items": {"bp": [130, 85]}}
    assert (
        client.post(
            f"/api/v1/sites/{TEST_SITE}/measurements", headers=auth(token), json=body
        ).status_code
        == 403
    )
    _, own = _staff_token(factory)
    expired, _ = issue_user_qr(user.id, now=datetime.now(UTC) - timedelta(minutes=30))
    assert (
        client.post(
            f"/api/v1/sites/{TEST_SITE}/measurements",
            headers=auth(own),
            json={**body, "user_qr": expired},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/sites/{TEST_SITE}/measurements",
            headers=auth(own),
            json={**body, "items": {"bp": [400, 85]}},
        ).status_code
        == 422
    )


def test_merchant_offer_via_api_key_or_account(client: TestClient, factory: Factory) -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        for mid in ("MA1", "MA2"):
            s.execute(
                text(
                    "INSERT INTO pub.merchants (id, name, category, village_code, lat, lng, hours, "
                    "api_key_hash) VALUES (:id, '測試', '早餐店', 'T01', 0, 0, '{}', :h)"
                ),
                {"id": mid, "h": hash_api_key(f"key-{mid}")},
            )
    offer = {
        "offer_id": "MA1-O1",
        "title": "無糖豆漿",
        "points_cost": 50,
        "stock": 20,
        "valid_from": "2026-10-01",
        "valid_to": "2026-12-31",
    }
    r = client.post(
        "/api/v1/merchants/MA1/offers", json=offer, headers={"X-Merchant-Key": "key-MA1"}
    )
    assert r.status_code == 200 and r.json()["title"] == "無糖豆漿"
    assert (
        client.post(
            "/api/v1/merchants/MA1/offers", json=offer, headers={"X-Merchant-Key": "key-MA2"}
        ).status_code
        == 401
    )
    # MA2 不能用同一個 offer_id 改掉 MA1 的優惠
    assert (
        client.post(
            "/api/v1/merchants/MA2/offers", json=offer, headers={"X-Merchant-Key": "key-MA2"}
        ).status_code
        == 403
    )
    with session_scope(DbRole.IDN_APP) as s:
        account = Account(
            username="merchant-ma1",
            password_hash="x",
            role="merchant",
            display_name="MA1",
            merchant_id="MA1",
        )
        s.add(account)
        s.flush()
        s.expunge(account)
    r = client.post(
        "/api/v1/merchants/MA1/offers",
        headers=auth(create_access_token(account)),
        json={**offer, "stock": 5},
    )
    assert r.json()["stock"] == 5
    assert [o["id"] for o in client.get("/api/v1/merchants/MA1/offers").json()] == ["MA1-O1"]


def test_agency_cohort_goes_through_k_anonymity(client: TestClient, factory: Factory) -> None:
    _, _, citizen = factory.citizen()
    assert client.get("/api/v1/agency/cohort", headers=auth(citizen)).status_code == 403
    token = create_access_token(factory.account("agency"))
    body = client.get("/api/v1/agency/cohort?metric=steps_avg&weeks=52", headers=auth(token)).json()
    assert body["k"] == 5
    assert all(row["n"] is None or row["n"] >= 5 for row in body["rows"])


def test_agency_artifact_download_is_sandboxed(client: TestClient, factory: Factory) -> None:
    name = artifacts.save("<html><script>alert(1)</script></html>", "html")
    url = artifacts.url_of(name)
    _, _, citizen = factory.citizen()
    assert client.get(url, headers=auth(citizen)).status_code == 403
    token = create_access_token(factory.account("agency"))
    resp = client.get(url, headers=auth(token))
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    assert resp.headers["content-security-policy"].startswith("sandbox")
    assert resp.headers["x-content-type-options"] == "nosniff"
    for bad in ("A-20260101-deadbeef.html", "..%2F..%2F.env", "notes.txt"):
        assert client.get(f"/api/v1/agency/artifacts/{bad}", headers=auth(token)).status_code == 404
