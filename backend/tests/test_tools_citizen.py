"""民眾側工具（SPEC §7.1）：正常輸入、範圍外 consent_scope、k 遮蔽（CLAUDE.md 測試要求）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text

from app.actions.state_machine import confirm
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_anon import CohortWeekly
from app.db.models_idn import Booking, Notification, PointsLedger, PointsReview, Redemption
from app.db.models_pub import Merchant, Offer
from app.events.bus import EventBus
from app.fhir import builders
from app.fhir.writer import ingest
from app.i18n import t
from app.services import points
from app.tools import CITIZEN_TOOLS
from app.tools.registry import ConsentScopeError, ToolContext, ToolResult, registry
from tests.conftest import Factory
from tests.fixtures import SCOPE, TODAY, TZ, Person, _at
from tests.helpers import TEST_SITE, TEST_VILLAGE


def call(
    user_id: uuid.UUID, tool: str, scope: dict[str, list[str]] | None = None, **args: Any
) -> ToolResult:
    with user_scoped_session(user_id) as s:
        payload: dict[str, Any] = {"user_id": str(user_id), **args}
        if scope is not None:
            payload["consent_scope"] = scope
        return registry.call(
            tool, payload, ToolContext(agent="A1", db=s, principal_user_id=user_id)
        )


MINIMAL_ARGS: dict[str, dict[str, Any]] = {
    "daily_report": {},
    "risk_score": {},
    "exercise_rx": {},
    "site_booking": {},
    "points_engine": {},
    "merchant_lookup": {},
    "consent_mgmt": {"action": "get"},
    "notify": {"title": "測試", "body": "測試內容"},
    "event_recommend": {},
    "event_register": {"event_id": "EV-NONE"},
    "merchant_recommend": {},
    "health_summary": {},
}


@pytest.mark.parametrize("tool", CITIZEN_TOOLS)
def test_every_citizen_tool_rejects_missing_or_forged_scope(wang: Person, tool: str) -> None:
    with pytest.raises(ConsentScopeError) as missing:
        call(wang.user_id, tool, None, **MINIMAL_ARGS[tool])
    assert missing.value.reason_key == "scope_missing"
    with pytest.raises(ConsentScopeError) as forged:
        call(wang.user_id, tool, {**SCOPE, "nhi": ["allergies"]}, **MINIMAL_ARGS[tool])
    assert forged.value.reason_key == "scope_forged"


class TestDailyReportAndRisk:
    def test_risk_levels_follow_scope(self, wang: Person) -> None:
        full = call(wang.user_id, "risk_score", SCOPE).data
        assert full is not None and full["level"] == "L3"
        assert set(full["triggers"]) == {"bp_consecutive_high", "steps_drop", "no_revisit_30d"}
        no_visits = call(wang.user_id, "risk_score", {**SCOPE, "nhi": ["conditions"]}).data
        assert no_visits is not None and no_visits["level"] == "L2"  # 沒授權就醫紀錄就無法判 L3
        steps_only = call(wang.user_id, "risk_score", {"wearable": ["steps"]}).data
        assert steps_only is not None and steps_only["level"] == "L1"

    def test_report_is_grounded_and_free_of_disease_assertions(self, wang: Person) -> None:
        report = call(wang.user_id, "daily_report", SCOPE).data
        assert report is not None
        assert report["status_sentence"] == t("report.status.L3")
        metrics = {m["metric"]: m for m in report["metrics"]}
        assert metrics["steps"]["value"] == 3100
        assert metrics["bp"]["value"] == [139, 90]
        assert all("依據" in m["text"] for m in report["metrics"])
        assert "測試據點" in metrics["bp"]["text"]
        blob = str(report)
        assert "高血壓" not in blob and "您有" not in blob

    def test_report_only_reads_items_in_scope(self, wang: Person) -> None:
        report = call(wang.user_id, "daily_report", {"wearable": ["steps"]}).data
        assert report is not None
        assert [m["metric"] for m in report["metrics"]] == ["steps"]


@pytest.fixture
def peer_rows() -> Iterator[None]:
    weeks = [date(2099, 1, 5), date(2098, 12, 29)]
    with session_scope(DbRole.ANON_WRITER) as s:
        for week in weeks:
            s.add(
                CohortWeekly(
                    week=week,
                    village_code="ALL",
                    age_band="70-79",
                    sex="ALL",
                    metric="steps_avg",
                    value=5800,
                    n=3,
                )
            )
    yield
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(delete(CohortWeekly).where(CohortWeekly.week.in_(weeks)))


def test_peer_comparison_is_k_masked(wang: Person, peer_rows: None) -> None:
    masked = call(wang.user_id, "daily_report", {"wearable": ["steps"]}).data
    assert masked is not None and masked["metrics"][0]["peer_avg"] is None
    with session_scope(DbRole.ANON_WRITER) as s:
        s.execute(text("UPDATE anon.cohort_weekly SET n = 12 WHERE week >= '2098-12-29'"))
    shown = call(wang.user_id, "daily_report", {"wearable": ["steps"]}).data
    assert shown is not None and shown["metrics"][0]["peer_avg"] == 5800


class TestExerciseRx:
    def test_conservative_and_indoor_when_air_is_bad(self, wang: Person) -> None:
        rx = call(wang.user_id, "exercise_rx", SCOPE, env={"aqi": 150, "heat_index": 28}).data
        assert rx is not None
        assert (rx["intensity"], rx["venue"], rx["minutes"]) == ("low", "indoor", 15)
        assert any("AQI 150" in reason for reason in rx["basis"])
        assert 3000 <= rx["step_goal"] <= 6000

    def test_young_healthy_person_gets_moderate_outdoor(self, factory: Factory) -> None:
        user = factory.user(birth_date=date(TODAY.year - 30, 1, 1))
        factory.consent(user.id, "wearable", ["steps"])
        rx = call(
            user.id,
            "exercise_rx",
            {"wearable": ["steps"]},
            env={"aqi": 40, "heat_index": 26, "rain": False},
        ).data
        assert rx is not None and (rx["intensity"], rx["venue"]) == ("moderate", "outdoor")


@pytest.fixture
def open_site() -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text("UPDATE pub.sites SET hours = :h WHERE id = :id"),
            {
                "h": '{"mon":"08:00-20:00","tue":"08:00-20:00","wed":"08:00-20:00",'
                '"thu":"08:00-20:00","fri":"08:00-20:00","sat":"08:00-20:00","sun":"08:00-20:00"}',
                "id": TEST_SITE,
            },
        )


class TestSiteBooking:
    def test_query_then_book_after_confirmation(self, wang: Person, open_site: None) -> None:
        query = call(wang.user_id, "site_booking", SCOPE, site_id=TEST_SITE).data
        assert query is not None and query["slots"]
        slot = query["slots"][0]
        pending = call(
            wang.user_id, "site_booking", SCOPE, mode="book", site_id=TEST_SITE, slot_at=slot
        )
        assert pending.status == "confirmation_required"
        assert pending.data is not None and "測試據點" in pending.data["summary"]
        with session_scope(DbRole.IDN_APP) as s:
            assert (
                s.scalar(
                    select(func.count()).select_from(Booking).where(Booking.user_id == wang.user_id)
                )
                == 0
            )
            action = confirm(s, pending.action_id, user_id=wang.user_id)  # type: ignore[arg-type]
            assert action.status == "executed"
        with session_scope(DbRole.IDN_APP) as s:
            booked = s.scalars(select(Booking).where(Booking.user_id == wang.user_id)).one()
            assert booked.created_by == "a1"

    def test_full_slot_is_not_offered(
        self, factory: Factory, wang: Person, open_site: None
    ) -> None:
        offered = call(wang.user_id, "site_booking", SCOPE, site_id=TEST_SITE).data
        assert offered is not None
        slot = datetime.fromisoformat(offered["slots"][0])
        with session_scope(DbRole.IDN_APP) as s:
            for _ in range(4):
                other = factory.user()
                s.add(
                    Booking(
                        user_id=other.id,
                        site_id=TEST_SITE,
                        slot_at=slot,
                        purpose="x",
                        created_by="user",
                    )
                )
        again = call(wang.user_id, "site_booking", SCOPE, site_id=TEST_SITE).data
        assert again is not None and slot.isoformat() not in again["slots"]


class TestPoints:
    def test_goal_grants_once_and_emits_event(self, factory: Factory, bus: EventBus) -> None:
        user = factory.user()
        factory.consent(user.id, "wearable", ["steps"])
        day = TODAY - timedelta(days=1)
        with session_scope(DbRole.IDN_APP) as s:
            ingest(
                s,
                user_id=user.id,
                source="wearable",
                emit_events=False,
                resources=[builders.steps_hourly(_at(day, h), 700) for h in range(8, 18)],
            )
        first = call(user.id, "points_engine", {"wearable": ["steps"]}, action="evaluate").data
        again = call(user.id, "points_engine", {"wearable": ["steps"]}, action="evaluate").data
        assert first is not None and (first["granted"], first["balance"]) == (10, 10)
        assert again is not None and again["already_granted"] is True
        assert any(d.event["type"] == "goal.achieved" for d in bus.recent(10))

    def test_cheating_hour_is_flagged_not_paid(self, factory: Factory) -> None:
        user = factory.user()
        factory.consent(user.id, "wearable", ["steps"])
        day = TODAY - timedelta(days=1)
        with session_scope(DbRole.IDN_APP) as s:
            ingest(
                s,
                user_id=user.id,
                source="wearable",
                emit_events=False,
                resources=[
                    builders.steps_hourly(_at(day, 14), 5200),
                    builders.steps_hourly(_at(day, 15), 1500),
                ],
            )
        result = call(user.id, "points_engine", {"wearable": ["steps"]}, action="evaluate").data
        assert result is not None and (result["granted"], result["flagged"]) == (0, "hourly_peak")
        with session_scope(DbRole.IDN_APP) as s:
            review = s.scalars(select(PointsReview).where(PointsReview.user_id == user.id)).one()
            assert (review.status, review.detail) == (
                "pending",
                {"peak_hour_steps": 5200, "total": 6700},
            )

    def test_evaluate_requires_steps_scope(self, factory: Factory) -> None:
        user = factory.user()
        factory.consent(user.id, "wearable", ["heart_rate"])
        with pytest.raises(ConsentScopeError) as exc:
            call(user.id, "points_engine", {"wearable": ["heart_rate"]}, action="evaluate")
        assert exc.value.reason_key == "scope_rejected"

    def test_expiry_is_first_in_first_out(self, factory: Factory) -> None:
        user = factory.user()
        old = datetime.now(TZ) - timedelta(days=170)
        with session_scope(DbRole.IDN_APP) as s:
            s.add_all(
                [
                    PointsLedger(
                        user_id=user.id, delta=50, reason="goal", balance_after=50, created_at=old
                    ),
                    PointsLedger(user_id=user.id, delta=30, reason="goal", balance_after=80),
                    PointsLedger(user_id=user.id, delta=-20, reason="redeem", balance_after=60),
                ]
            )
        with session_scope(DbRole.IDN_APP) as s:
            soon = points.expiring(s, user.id, TODAY)
        assert soon == [
            {"expires_on": (old.date() + timedelta(days=180)).isoformat(), "points": 30}
        ]

    def test_redeem_needs_confirmation_then_debits(self, factory: Factory) -> None:
        user = factory.user()
        factory.consent(user.id, "merchant", ["redemptions"])
        with session_scope(DbRole.MIGRATOR) as s:
            s.add(
                Merchant(
                    id="MP1",
                    name="測試店",
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
                    id="MP1-O1",
                    merchant_id="MP1",
                    title="豆漿",
                    points_cost=40,
                    stock=2,
                    valid_from=TODAY - timedelta(days=1),
                    valid_to=TODAY + timedelta(days=30),
                )
            )
        with session_scope(DbRole.IDN_APP) as s:
            points.append(s, user.id, 100, "goal", "date", "seed")
        pending = call(
            user.id,
            "points_engine",
            {"merchant": ["redemptions"]},
            action="redeem",
            offer_id="MP1-O1",
        )
        assert pending.status == "confirmation_required"
        assert pending.data is not None and "40 點" in pending.data["summary"]
        with session_scope(DbRole.IDN_APP) as s:
            action = confirm(s, pending.action_id, user_id=user.id)  # type: ignore[arg-type]
            assert action.status == "executed" and action.result is not None
            assert action.result["balance"] == 60
        with session_scope(DbRole.IDN_APP) as s:
            assert (
                s.scalars(select(Redemption).where(Redemption.user_id == user.id)).one().points
                == 40
            )
            assert s.get_one(Offer, "MP1-O1").stock == 1


def test_merchant_lookup_filters_by_points(wang: Person) -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        s.add(
            Merchant(
                id="ML1",
                name="近的店",
                category="蔬果行",
                village_code=TEST_VILLAGE,
                lat=24.8001,
                lng=120.9701,
                hours={},
                api_key_hash="x",
            )
        )
        s.flush()
        s.add_all(
            [
                Offer(
                    id="ML1-O1",
                    merchant_id="ML1",
                    title="便宜",
                    points_cost=30,
                    stock=5,
                    valid_from=TODAY,
                    valid_to=TODAY,
                ),
                Offer(
                    id="ML1-O2",
                    merchant_id="ML1",
                    title="貴",
                    points_cost=900,
                    stock=5,
                    valid_from=TODAY,
                    valid_to=TODAY,
                ),
            ]
        )
    found = call(
        wang.user_id, "merchant_lookup", SCOPE, lat=24.8, lng=120.97, radius_km=0.5, points=100
    ).data
    assert found is not None
    near = next(m for m in found["merchants"] if m["merchant_id"] == "ML1")
    assert [o["offer_id"] for o in near["offers"]] == ["ML1-O1"]


def test_notify_filters_disease_assertions(wang: Person) -> None:
    result = call(wang.user_id, "notify", SCOPE, title="提醒", body="您可能有高血壓，請注意")
    assert result.data is not None and result.data["status"] == "sent"
    with session_scope(DbRole.IDN_APP) as s:
        note = s.scalars(select(Notification).where(Notification.user_id == wang.user_id)).one()
        assert "高血壓" not in note.body and note.body == t("output_filter.fallback")
