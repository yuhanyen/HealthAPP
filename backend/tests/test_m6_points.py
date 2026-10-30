"""M6 商家與點數（SPEC §14 M6、§15 情境 7）：達標發點 → 兌換（需確認）→ 商家核銷 → 對帳平衡 →
月成效無個人；防作弊標記 → 人工審核。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import create_access_token
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_idn import PointsLedger, PointsReview, Redemption
from app.db.models_pub import Merchant, Offer
from app.fhir import builders
from app.fhir.writer import ingest
from app.services import points
from app.services.reconcile import reconcile
from app.tools.registry import ToolContext, registry
from tests.conftest import Factory
from tests.fixtures import TODAY, _at
from tests.helpers import TEST_VILLAGE, audit_rows, auth

API = "/api/v1"
YESTERDAY = TODAY - timedelta(days=1)
IDENTITY_KEYS = {"user_id", "name", "display_name", "phone", "address", "national_id_hash"}


def walker(factory: Factory, *, spike: int | None = None) -> uuid.UUID:
    """昨天每小時步數（07–18 點各 700 步，共 8,400）；spike 指定時 14 點改為該步數。"""
    user = factory.user(name="點數測試")
    factory.consent(user.id, "wearable", ["steps"])
    factory.consent(user.id, "merchant", ["redemptions"])
    with session_scope(DbRole.IDN_APP) as s:
        ingest(
            s,
            user_id=user.id,
            source="wearable",
            emit_events=False,
            resources=[
                builders.steps_hourly(_at(YESTERDAY, h), spike if spike and h == 14 else 700)
                for h in range(7, 19)
            ],
        )
    return user.id


def evaluate(user_id: uuid.UUID) -> dict[str, Any]:
    with user_scoped_session(user_id) as s:
        result = registry.call(
            "points_engine",
            {
                "user_id": str(user_id),
                "consent_scope": {"wearable": ["steps"], "merchant": ["redemptions"]},
                "action": "evaluate",
                "day": YESTERDAY.isoformat(),
            },
            ToolContext(agent="A1", db=s, principal_user_id=user_id),
        )
    assert result.data is not None
    return result.data


@pytest.fixture
def shop() -> tuple[str, str, str]:
    """(本店, 本店優惠, 另一家店)。"""
    merchant_id, other_id = f"MS{uuid.uuid4().hex[:5]}", f"MO{uuid.uuid4().hex[:5]}"
    with session_scope(DbRole.MIGRATOR) as s:
        for mid, name in ((merchant_id, "核銷測試店"), (other_id, "隔壁店")):
            s.add(
                Merchant(
                    id=mid,
                    name=name,
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
                title="豆漿",
                points_cost=30,
                stock=5,
                valid_from=TODAY - timedelta(days=1),
                valid_to=TODAY + timedelta(days=30),
            )
        )
    return merchant_id, f"{merchant_id}-O1", other_id


def _no_identity(value: Any) -> None:
    if isinstance(value, dict):
        assert not IDENTITY_KEYS & set(value), value
        for item in value.values():
            _no_identity(item)
    elif isinstance(value, list):
        for item in value:
            _no_identity(item)


def test_points_to_redemption_to_verification_balances(
    client: TestClient, factory: Factory, shop: tuple[str, str, str]
) -> None:
    """情境 7：達標發點 → 商家端核銷 → 帳本平衡 → 月成效報告無個人。"""
    merchant_id, offer_id, other_id = shop
    user_id = walker(factory)
    granted = evaluate(user_id)
    assert granted["granted"] == points.GOAL_POINTS and granted["steps"] == 8400
    assert evaluate(user_id)["already_granted"] is True  # 同一天不重複發點
    with session_scope(DbRole.IDN_APP) as s:
        points.append(s, user_id, 25, "goal.steps", "date", (TODAY - timedelta(days=9)).isoformat())

    citizen = create_access_token(factory.account("citizen", user_id=user_id))
    pending = client.post(f"{API}/me/redeem", json={"offer_id": offer_id}, headers=auth(citizen))
    assert pending.status_code == 202  # 需本人確認
    done = client.post(
        f"{API}/me/actions/{pending.json()['action_id']}/confirm", headers=auth(citizen)
    ).json()
    token = done["result"]["qr_token"]
    assert done["status"] == "executed" and done["result"]["balance"] == 5

    shopkeeper = create_access_token(factory.account("merchant", merchant_id=merchant_id))
    verified = client.post(
        f"{API}/merchants/{merchant_id}/verify", json={"qr_token": token}, headers=auth(shopkeeper)
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["offer"] == "豆漿" and body["points"] == 30
    _no_identity(body)
    again = client.post(
        f"{API}/merchants/{merchant_id}/verify", json={"qr_token": token}, headers=auth(shopkeeper)
    )
    assert again.status_code == 409
    other = create_access_token(factory.account("merchant", merchant_id=other_id))
    # 別家店：不能冒用本店路徑（401），用自己的路徑核銷本店的券也不行（403）
    for path, expected in ((merchant_id, 401), (other_id, 403)):
        resp = client.post(
            f"{API}/merchants/{path}/verify", json={"qr_token": token}, headers=auth(other)
        )
        assert resp.status_code == expected
    unknown = client.post(
        f"{API}/merchants/{merchant_id}/verify",
        json={"qr_token": "RD-nope"},
        headers=auth(shopkeeper),
    )
    assert unknown.status_code == 404
    assert audit_rows(action="merchant.verify", decision="rejected")

    with session_scope(DbRole.IDN_APP) as s:
        result = reconcile(s, [user_id])
    assert result["ok"], result["problems"]
    assert (result["granted"], result["spent"], result["outstanding"]) == (35, 30, 5)

    report = client.get(f"{API}/merchants/{merchant_id}/report", headers=auth(shopkeeper)).json()
    assert (
        report["redemptions"] == 1
        and report["verified"] == 1
        and report["offers"][0]["title"] == "豆漿"
    )
    assert report["age_masked"] == 1 and all(r["_masked"] for r in report["age_bands"])  # 1 人 < k
    _no_identity(report)


def test_expired_coupon_is_rejected(
    client: TestClient, factory: Factory, shop: tuple[str, str, str]
) -> None:
    merchant_id, offer_id, _ = shop
    user = factory.user()
    with session_scope(DbRole.IDN_APP) as s:
        s.add(
            Redemption(
                user_id=user.id,
                merchant_id=merchant_id,
                offer_id=offer_id,
                points=30,
                qr_token="RD-old-coupon",
                redeemed_at=datetime.now(UTC) - timedelta(days=40),
            )
        )
    shopkeeper = create_access_token(factory.account("merchant", merchant_id=merchant_id))
    resp = client.post(
        f"{API}/merchants/{merchant_id}/verify",
        json={"qr_token": "RD-old-coupon"},
        headers=auth(shopkeeper),
    )
    assert resp.status_code == 410


def _review_id(user_id: uuid.UUID) -> int:
    with session_scope(DbRole.IDN_APP) as s:
        review_id = s.scalar(select(PointsReview.id).where(PointsReview.user_id == user_id))
    assert review_id is not None
    return int(review_id)


def test_cheat_is_flagged_and_reviewed(client: TestClient, factory: Factory) -> None:
    cheat = walker(factory, spike=4800)
    result = evaluate(cheat)
    assert result["flagged"] == "hourly_peak" and result["granted"] == 0
    admin = create_access_token(factory.account("admin"))
    pending = client.get(f"{API}/admin/points/reviews", headers=auth(admin)).json()
    [mine] = [r for r in pending if r["id"] == _review_id(cheat)]
    assert mine["reason"] == "hourly_peak" and mine["display_name"] == "點○○試"  # 姓名遮罩
    assert mine["detail"] == {"peak_hour_steps": 4800, "total": 12500}
    cleared = client.post(
        f"{API}/admin/points/reviews/{mine['id']}",
        json={"decision": "cleared"},
        headers=auth(admin),
    ).json()
    assert cleared == {"id": mine["id"], "status": "cleared", "granted": points.GOAL_POINTS}
    assert (
        client.post(
            f"{API}/admin/points/reviews/{mine['id']}",
            json={"decision": "rejected"},
            headers=auth(admin),
        ).status_code
        == 409
    )
    # 駁回：不發點
    other = walker(factory, spike=5200)
    evaluate(other)
    rejected = client.post(
        f"{API}/admin/points/reviews/{_review_id(other)}",
        json={"decision": "rejected"},
        headers=auth(admin),
    ).json()
    assert rejected["granted"] == 0
    with session_scope(DbRole.IDN_APP) as s:
        assert points.balance(s, other) == 0 and points.balance(s, cheat) == points.GOAL_POINTS
    assert audit_rows(action="points.review")


def test_reconcile_detects_inconsistent_ledger(factory: Factory) -> None:
    user = factory.user()
    with session_scope(DbRole.IDN_APP) as s:
        points.append(s, user.id, 10, "goal.steps", "date", "2099-01-01")
        s.add(PointsLedger(user_id=user.id, delta=5, reason="goal.steps", balance_after=999))
        s.add(
            PointsLedger(
                user_id=user.id,
                delta=-7,
                reason="redeem",
                ref_type="redemption",
                ref_id=str(uuid.uuid4()),
                balance_after=992,
            )
        )
    with session_scope(DbRole.IDN_APP) as s:
        result = reconcile(s, [user.id])
    kinds = {p["type"] for p in result["problems"]}
    assert not result["ok"] and {"balance_mismatch", "debit_without_coupon"} <= kinds
    _no_identity(result)
