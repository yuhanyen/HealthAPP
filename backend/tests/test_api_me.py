"""民眾 API（SPEC §9 民眾）：報告、雙生、對話 SSE、點數兌換、守護圈、偏好、通知。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents import a1
from app.auth.security import create_access_token
from app.db.engines import DbRole, session_scope
from app.db.models_pub import Merchant, Offer
from app.services import points
from tests.conftest import Factory
from tests.fixtures import TODAY, Person
from tests.helpers import TEST_VILLAGE, audit_rows, auth

API = "/api/v1"


def token_for(factory: Factory, user_id: uuid.UUID) -> str:
    return create_access_token(factory.account("citizen", user_id=user_id))


def sse(client: TestClient, token: str, message: str) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST", f"{API}/me/chat", json={"message": message}, headers=auth(token)
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    events = []
    for chunk in body.strip().split("\n\n"):
        kind = re.search(r"^event: (.+)$", chunk, re.M)
        data = re.search(r"^data: (.+)$", chunk, re.M)
        assert kind and data
        events.append((kind.group(1), json.loads(data.group(1))))
    return events


def test_report_and_twin(client: TestClient, factory: Factory, wang: Person) -> None:
    token = token_for(factory, wang.user_id)
    assert client.get(f"{API}/me/reports", headers=auth(token)).status_code == 404
    assert client.get(f"{API}/me/twin", headers=auth(token)).json()["state"] is None
    a1.run_daily(wang.user_id)
    body = client.get(f"{API}/me/reports", headers=auth(token)).json()
    assert body["date"] == TODAY.isoformat() and body["opened_at"]
    assert body["content"]["lines"]
    history = client.get(f"{API}/me/reports/history", headers=auth(token)).json()
    assert history[0]["risk_level"] in ("L2", "L3")
    twin = client.get(f"{API}/me/twin", headers=auth(token)).json()
    assert twin["as_of"] == TODAY.isoformat() and twin["state"]["risk"]["level"]


def test_chat_sse_streams_text_tools_and_done(
    client: TestClient, factory: Factory, wang: Person, open_sites: None
) -> None:
    token = token_for(factory, wang.user_id)
    events = sse(client, token, "我最近是不是變差了？")
    kinds = [k for k, _ in events]
    assert kinds[-1] == "done" and "text" in kinds and kinds.count("tool") == 3
    text = "".join(d["text"] for k, d in events if k == "text")
    assert "依據：" in text
    events = sse(client, token, "好")
    [card] = [d for k, d in events if k == "confirmation"]
    assert card["tool"] == "site_booking"
    # 確認卡片 → 本人確認 → 預約成立
    done = client.post(f"{API}/me/actions/{card['action_id']}/confirm", headers=auth(token))
    assert done.json()["status"] == "executed"
    bookings = client.get(f"{API}/me/bookings", headers=auth(token)).json()
    assert len(bookings) == 1 and bookings[0]["site_name"]
    history = client.get(f"{API}/me/chat", headers=auth(token)).json()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_book_from_report_decision(
    client: TestClient, factory: Factory, wang: Person, open_sites: None
) -> None:
    token = token_for(factory, wang.user_id)
    content = a1.run_daily(wang.user_id)
    assert content is not None and content["decision"]
    decision = content["decision"]
    body = {"site_id": decision["site_id"], "slot_at": decision["slots"][0]}
    pending = client.post(f"{API}/me/bookings", json=body, headers=auth(token))
    assert pending.status_code == 202
    assert client.get(f"{API}/me/bookings", headers=auth(token)).json() == []
    done = client.post(
        f"{API}/me/bookings", json={**body, "confirmed": True}, headers=auth(token)
    ).json()
    assert done["status"] == "executed" and done["result"]["booking_id"]
    assert len(client.get(f"{API}/me/bookings", headers=auth(token)).json()) == 1


def test_chat_requires_citizen(client: TestClient, factory: Factory) -> None:
    staff = create_access_token(factory.account("agency"))
    resp = client.post(f"{API}/me/chat", json={"message": "hi"}, headers=auth(staff))
    assert resp.status_code == 403


@pytest.fixture
def offer(factory: Factory) -> str:
    merchant_id = f"MA{uuid.uuid4().hex[:5]}"
    with session_scope(DbRole.MIGRATOR) as s:
        s.add(
            Merchant(
                id=merchant_id,
                name="API 測試店",
                category="早餐店",
                village_code=TEST_VILLAGE,
                lat=24.8,
                lng=120.97,
                hours={},
                api_key_hash="x",
            )
        )
        s.flush()
        s.add(
            Offer(
                id=f"{merchant_id}-O1",
                merchant_id=merchant_id,
                title="鮮奶茶",
                points_cost=30,
                stock=3,
                valid_from=TODAY - timedelta(days=1),
                valid_to=TODAY + timedelta(days=30),
            )
        )
    return f"{merchant_id}-O1"


def test_points_and_redeem(client: TestClient, factory: Factory, wang: Person, offer: str) -> None:
    token = token_for(factory, wang.user_id)
    with session_scope(DbRole.IDN_APP) as s:
        points.append(s, wang.user_id, 50, "goal.steps", "date", "api-test")
    body = client.get(f"{API}/me/points", headers=auth(token)).json()
    assert body["balance"] == 50 and body["ledger"][0]["reason"] == "步數達標"

    pending = client.post(f"{API}/me/redeem", json={"offer_id": offer}, headers=auth(token))
    assert pending.status_code == 202 and pending.json()["status"] == "confirmation_required"
    assert client.get(f"{API}/me/points", headers=auth(token)).json()["balance"] == 50

    done = client.post(
        f"{API}/me/redeem", json={"offer_id": offer, "confirmed": True}, headers=auth(token)
    ).json()
    assert done["status"] == "executed" and done["result"]["qr_token"].startswith("RD-")
    after = client.get(f"{API}/me/points", headers=auth(token)).json()
    assert after["balance"] == 20 and after["redemptions"][0]["qr_token"]
    offers = client.get(f"{API}/me/offers?lat=24.8&lng=120.97", headers=auth(token)).json()
    assert any(m["name"] == "API 測試店" for m in offers["merchants"])


def test_family_share_by_code_and_summary_has_no_numbers(
    client: TestClient, factory: Factory, wang: Person
) -> None:
    owner = token_for(factory, wang.user_id)
    daughter_user = factory.user(name="王小女")
    daughter = token_for(factory, daughter_user.id)
    stranger = token_for(factory, factory.user().id)
    a1.run_daily(wang.user_id)

    code = client.get(f"{API}/me/family/code", headers=auth(daughter)).json()["code"]
    pending = client.post(f"{API}/me/family", json={"viewer_code": code}, headers=auth(owner))
    assert pending.status_code == 202 and "王○女" in pending.json()["summary"]
    # 尚未確認：家屬看不到
    url = f"{API}/family/{wang.user_id}/summary"
    assert client.get(url, headers=auth(daughter)).status_code == 403
    confirmed = client.post(
        f"{API}/me/actions/{pending.json()['action_id']}/confirm", headers=auth(owner)
    )
    assert confirmed.json()["status"] == "executed"

    summary = client.get(url, headers=auth(daughter)).json()
    assert set(summary) == {
        "owner_display_name",
        "as_of",
        "status_sentence",
        "risk_level",
        "risk_label",
        "pending_action",
    }
    assert summary["owner_display_name"] == "王○試"
    assert summary["risk_level"] in ("L2", "L3") and summary["pending_action"]
    assert not re.search(r"\d", summary["status_sentence"] + (summary["pending_action"] or ""))
    assert client.get(url, headers=auth(stranger)).status_code == 403
    assert audit_rows(action="family.summary.read", decision="rejected")

    links = client.get(f"{API}/me/family", headers=auth(owner)).json()
    [link] = links["sharing"]
    assert link["display_name"] == "王○女"
    assert client.get(f"{API}/me/family", headers=auth(daughter)).json()["shared_with_me"]
    # 本人收回：立即生效
    assert (
        client.delete(f"{API}/me/family/{link['link_id']}", headers=auth(owner)).status_code == 204
    )
    assert client.get(url, headers=auth(daughter)).status_code == 403


def test_family_rejects_bad_code_and_self(
    client: TestClient, factory: Factory, wang: Person
) -> None:
    owner = token_for(factory, wang.user_id)
    bad = client.post(f"{API}/me/family", json={"viewer_code": "x.y.z"}, headers=auth(owner))
    assert bad.status_code == 403
    me = client.post(
        f"{API}/me/family", json={"viewer_user_id": str(wang.user_id)}, headers=auth(owner)
    )
    assert me.status_code == 403


def test_prefs_and_notifications(client: TestClient, factory: Factory, wang: Person) -> None:
    token = token_for(factory, wang.user_id)
    profile = client.get(f"{API}/me/profile", headers=auth(token)).json()
    assert profile["name"] == "王測試" and profile["elder_mode"] is False
    updated = client.put(f"{API}/me/prefs", json={"elder_mode": True}, headers=auth(token))
    assert updated.json()["elder_mode"] is True

    a1.run_daily(wang.user_id)
    assert a1.deliver(wang.user_id, TODAY)
    [note] = client.get(f"{API}/me/notifications", headers=auth(token)).json()
    assert note["kind"] == "daily_report" and note["payload"]["report_date"] == TODAY.isoformat()
    assert (
        client.post(f"{API}/me/notifications/{note['id']}/read", headers=auth(token)).status_code
        == 204
    )
    assert client.get(f"{API}/me/notifications", headers=auth(token)).json()[0]["read_at"]
