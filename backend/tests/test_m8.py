"""M8（SPEC §17）：近日社區活動、商家推薦、健康儀表板。

每個新工具都測：正常輸入、範圍外 consent_scope（只用得到授權內的資料）、
k 遮蔽（同齡比較、機關端報名數、活動參與率）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app.actions.state_machine import confirm
from app.adapters.events import parse_when
from app.adapters.events_land import sync_events, sync_merchant_ratings
from app.adapters.merchant_rating import Rating
from app.adapters.mocks.events import MockEventClient
from app.agents import a1
from app.agents.runtime import ConfirmationEvent, DoneEvent
from app.auth.security import create_access_token
from app.auth.user_qr import issue_user_qr
from app.db.engines import DbRole, session_scope, user_scoped_session
from app.db.models_anon import CohortWeekly
from app.db.models_idn import EventRegistration, Notification, PendingAction, PointsLedger
from app.db.models_pub import Event, Merchant, SourceWhitelist
from app.deidentify.aggregate import cohort_cells, run_deidentify, week_start
from app.deidentify.suppress import ALL, Cell
from app.i18n import t
from app.services import events as ev
from app.services import prefs
from app.tools.registry import ToolContext, ToolResult, registry
from tests.conftest import Factory
from tests.fixtures import SCOPE, TODAY, Person
from tests.helpers import OTHER_SITE, TEST_SITE, TEST_VILLAGE, auth

API = "/api/v1"
TZ = ZoneInfo("Asia/Taipei")
MakeEvent = Callable[..., str]


def call(user_id: uuid.UUID, tool: str, scope: dict[str, list[str]], **args: Any) -> ToolResult:
    """以 A1 的身分（idn_user_scoped，RLS 只見本人）呼叫民眾側工具。"""
    with user_scoped_session(user_id) as s:
        return registry.call(
            tool,
            {"user_id": str(user_id), "consent_scope": scope, **args},
            ToolContext(agent="A1", db=s, principal_user_id=user_id),
        )


@pytest.fixture
def make_event() -> Iterator[MakeEvent]:
    created: list[str] = []

    def _make(**overrides: Any) -> str:
        event_id = f"EV-T{uuid.uuid4().hex[:8].upper()}"
        start = overrides.pop("start", datetime.now(UTC) + timedelta(days=2))
        values: dict[str, Any] = {
            "id": event_id,
            "title": "社區量測站",
            "category": "measure",
            "organizer": "測試據點",
            "site_id": TEST_SITE,
            "village_code": TEST_VILLAGE,
            "venue": "測試據點",
            "start_at": start,
            "end_at": start + timedelta(hours=2),
            "tags": [],
            "source": "site",
            "status": "published",
            "created_by": "test",
        } | overrides
        with session_scope(DbRole.IDN_APP) as s:
            s.add(Event(**values))
        created.append(event_id)
        return event_id

    yield _make
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(delete(EventRegistration).where(EventRegistration.event_id.in_(created)))
        s.execute(delete(Event).where(Event.id.in_(created)))


def _register(user_id: uuid.UUID, event_id: str, scope: dict[str, list[str]]) -> PendingAction:
    pending = call(user_id, "event_register", scope, event_id=event_id)
    assert pending.status == "confirmation_required" and pending.action_id is not None
    with session_scope(DbRole.IDN_APP) as s:
        action = confirm(s, pending.action_id, user_id=user_id)
        s.expunge(action)
        return action


# --- event_recommend ---


class TestEventRecommend:
    def test_health_reason_comes_from_authorized_data(
        self, wang: Person, make_event: MakeEvent
    ) -> None:
        bp_event = make_event(title="週末量測站", tags=["bp", "indoor", "gentle"], points_reward=20)
        social = make_event(title="手作聯誼", category="social", tags=[])
        data = call(wang.user_id, "event_recommend", SCOPE).data
        assert data is not None
        found = {e["event_id"]: e for e in data["events"]}
        assert bp_event in found and social in found
        top = found[bp_event]
        assert t("rec.signal.bp") in top["reasons"]
        assert any(b.startswith("依據：據點血壓") for b in top["basis"])
        assert t("rec.reason.points", points=20) in top["reasons"]
        assert top["score"] > found[social]["score"]
        # 推播只有活動名稱、時間、地點、點數，沒有健康理由
        for reason in top["reasons"][:1]:
            assert reason not in top["push_title"] and reason not in top["push_body"]
        assert "血壓量測偏高" not in top["push_title"] + top["push_body"]
        assert "參加可得 20 點" in top["push_body"]

    def test_out_of_scope_data_is_never_used(self, wang: Person, make_event: MakeEvent) -> None:
        bp_event = make_event(title="週末量測站", tags=["bp"])
        data = call(wang.user_id, "event_recommend", {"wearable": ["steps"]}).data
        assert data is not None
        assert "bp" not in data["signals"]
        event = next(e for e in data["events"] if e["event_id"] == bp_event)
        assert t("rec.signal.bp") not in event["reasons"]
        assert not any("血壓" in b for b in event["basis"])

    def test_seats_count_others_without_reading_their_registrations(
        self, factory: Factory, wang: Person, make_event: MakeEvent
    ) -> None:
        event_id = make_event(capacity=2)
        other = factory.user(name="別人")
        factory.consent(other.id, "wearable", ["steps"])
        assert _register(other.id, event_id, {"wearable": ["steps"]}).status == "executed"
        data = call(wang.user_id, "event_recommend", SCOPE).data
        assert data is not None
        event = next(e for e in data["events"] if e["event_id"] == event_id)
        assert event["seats_left"] == 1 and event["registration"] is None
        with user_scoped_session(wang.user_id) as s:  # RLS：A1 連線看不到別人的報名
            assert s.scalar(select(func.count()).select_from(EventRegistration)) == 0


# --- event_register ---


class TestEventRegister:
    def test_register_needs_owner_confirmation(self, wang: Person, make_event: MakeEvent) -> None:
        event_id = make_event(title="清晨健走", points_reward=10)
        pending = call(wang.user_id, "event_register", SCOPE, event_id=event_id, confirmed=True)
        assert pending.status == "confirmation_required"  # Agent 自帶 confirmed 不算數
        assert pending.data is not None and "姓名與電話" in pending.data["summary"]
        with session_scope(DbRole.IDN_APP) as s:
            assert (
                s.scalar(select(EventRegistration).where(EventRegistration.user_id == wang.user_id))
                is None
            )
            assert pending.action_id is not None
            action = confirm(s, pending.action_id, user_id=wang.user_id)
            assert action.status == "executed" and action.action_type == "event_register"
            reg = s.scalars(
                select(EventRegistration).where(EventRegistration.user_id == wang.user_id)
            ).one()
            assert reg.event_id == event_id and reg.created_by == "a1"

    def test_full_event_fails_with_plain_reason(
        self, factory: Factory, wang: Person, make_event: MakeEvent
    ) -> None:
        event_id = make_event(capacity=1)
        other = factory.user(name="先報名的人")
        factory.consent(other.id, "wearable", ["steps"])
        _register(other.id, event_id, {"wearable": ["steps"]})
        action = _register(wang.user_id, event_id, SCOPE)
        assert action.status == "failed"
        assert action.result is not None and action.result["message"] == t("events.error.full")

    def test_cancel_runs_on_the_a1_connection(self, wang: Person, make_event: MakeEvent) -> None:
        event_id = make_event()
        _register(wang.user_id, event_id, SCOPE)
        result = call(wang.user_id, "event_register", SCOPE, event_id=event_id, action="cancel")
        assert result.status == "ok" and result.data == {
            "event_id": event_id,
            "status": "cancelled",
        }


# --- merchant_recommend ---


@pytest.fixture
def dining() -> Iterator[dict[str, str]]:
    """三家同類店家（在測試據點旁）：4.5 分少鹽、3.5 分、沒有評分。"""
    category = f"測試餐飲{uuid.uuid4().hex[:4]}"
    ids = {name: f"MT{uuid.uuid4().hex[:6]}" for name in ("good", "low", "unrated")}
    rows: dict[str, tuple[Decimal | None, list[str]]] = {
        "good": (Decimal("4.5"), ["low_salt"]),
        "low": (Decimal("3.5"), []),
        "unrated": (None, []),
    }
    with session_scope(DbRole.IDN_APP) as s:
        for name, mid in ids.items():
            rating, tags = rows[name]
            s.add(
                Merchant(
                    id=mid,
                    name=f"{name}店",
                    category=category,
                    village_code=TEST_VILLAGE,
                    lat=24.8005,
                    lng=120.9705,
                    hours={},
                    api_key_hash="x",
                    rating=rating,
                    rating_count=100 if rating else None,
                    rating_source="mock" if rating else None,
                    tags=tags,
                )
            )
    yield {"category": category, **ids}
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(delete(Merchant).where(Merchant.id.in_(list(ids.values()))))


class TestMerchantRecommend:
    def test_rating_threshold_and_excluded_counts(
        self, wang: Person, dining: dict[str, str]
    ) -> None:
        data = call(wang.user_id, "merchant_recommend", SCOPE, category=dining["category"]).data
        assert data is not None
        assert [m["merchant_id"] for m in data["merchants"]] == [dining["good"]]
        assert data["min_rating"] == 4.0
        assert (data["excluded_low_rating"], data["excluded_unrated"]) == (1, 1)
        assert "4.0" in data["excluded_text"]
        # 血壓量測偏高（site.bp 已授權）→ 少鹽店家加註理由
        assert t("rec.reason.health_low_salt") in data["merchants"][0]["reasons"]

    def test_out_of_scope_health_signal_is_not_used(
        self, wang: Person, dining: dict[str, str]
    ) -> None:
        data = call(
            wang.user_id, "merchant_recommend", {"wearable": ["steps"]}, category=dining["category"]
        ).data
        assert data is not None
        assert t("rec.reason.health_low_salt") not in data["merchants"][0]["reasons"]

    def test_personal_threshold_overrides_default(
        self, wang: Person, dining: dict[str, str]
    ) -> None:
        with session_scope(DbRole.IDN_APP) as s:
            prefs.save_prefs(s, wang.user_id, prefs.Prefs(min_rating=3.0, dietary=["low_salt"]))
        data = call(wang.user_id, "merchant_recommend", SCOPE, category=dining["category"]).data
        assert data is not None
        assert {m["merchant_id"] for m in data["merchants"]} == {dining["good"], dining["low"]}
        good = next(m for m in data["merchants"] if m["merchant_id"] == dining["good"])
        assert t("rec.reason.dietary", items="少鹽") in good["reasons"]


# --- health_summary ---


@pytest.fixture
def peer_week_rows() -> Iterator[None]:
    weeks = [datetime(2100, 1, 4).date(), datetime(2099, 12, 28).date()]
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


class TestHealthSummary:
    def test_week_statistics(self, wang: Person) -> None:
        data = call(wang.user_id, "health_summary", SCOPE, period="week").data
        assert data is not None
        assert set(data["metrics"]) == {"steps", "bp", "weight"}
        assert {"glucose", "heart_rate"} <= set(data["not_authorized"])
        steps = data["metrics"]["steps"]
        assert (steps["avg"], steps["days_with_data"], steps["goal_days"]) == (3100, 7, 0)
        assert steps["prev_avg"] == 3100 and steps["change_pct"] == 0
        bp = data["metrics"]["bp"]
        assert bp["count"] == 3 and bp["high"] == 3 and bp["reference"] == [140, 90]
        assert [r["systolic"] for r in bp["series"]] == [142, 145, 139]  # 舊到新
        assert all("您有" not in line and "高血壓" not in line for line in data["lines"])

    def test_only_authorized_metrics(self, wang: Person) -> None:
        data = call(wang.user_id, "health_summary", {"wearable": ["steps"]}).data
        assert data is not None
        assert set(data["metrics"]) == {"steps"}
        assert "bp" in data["not_authorized"]

    def test_peer_comparison_is_k_masked(self, wang: Person, peer_week_rows: None) -> None:
        masked = call(wang.user_id, "health_summary", {"wearable": ["steps"]}).data
        assert masked is not None and masked["metrics"]["steps"]["peer_avg"] is None
        with session_scope(DbRole.ANON_WRITER) as s:
            s.execute(text("UPDATE anon.cohort_weekly SET n = 12 WHERE week >= '2099-12-28'"))
        shown = call(wang.user_id, "health_summary", {"wearable": ["steps"]}).data
        assert shown is not None and shown["metrics"]["steps"]["peer_avg"] == 5800


# --- 公開公告匯入 ---


@pytest.fixture
def notice_whitelist() -> Iterator[None]:
    patterns = (
        "https://north.district.example.tw/news/*",
        "https://www.facebook.com/hepingli.office/*",
    )
    with session_scope(DbRole.MIGRATOR) as s:
        for pattern in patterns:
            s.add(
                SourceWhitelist(source_class="event_notice", url_pattern=pattern, approved_by="t")
            )
    yield
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(delete(Event).where(Event.source == "crawl"))
        s.execute(delete(SourceWhitelist).where(SourceWhitelist.url_pattern.in_(patterns)))


class TestEventNotices:
    def test_parse_when(self) -> None:
        today = datetime(2026, 10, 30).date()
        start, end = parse_when("日期：11/8（六）上午9:00至11:00", today, TZ) or (None, None)
        assert start == datetime(2026, 11, 8, 9, 0, tzinfo=TZ)
        assert end == datetime(2026, 11, 8, 11, 0, tzinfo=TZ)
        afternoon = parse_when("11/8（六）下午2:00-4:00", today, TZ)
        assert afternoon is not None and (afternoon[0].hour, afternoon[1].hour) == (14, 16)
        two_pm = parse_when("11月8日 下午2點開始", today, TZ)
        assert two_pm is not None and two_pm[0].hour == 14 and two_pm[1].hour == 16
        assert parse_when("這週末有什麼活動嗎？", today, TZ) is None
        assert parse_when("1/5 上午 10:00", today, TZ) == (
            datetime(2027, 1, 5, 10, 0, tzinfo=TZ),
            datetime(2027, 1, 5, 12, 0, tzinfo=TZ),
        )

    def test_sync_lands_whitelisted_notices_as_pending_without_identity(
        self, notice_whitelist: None
    ) -> None:
        summary = sync_events(client=MockEventClient(), use_llm=False)
        assert summary.errors == 0 and summary.written == 3
        with session_scope(DbRole.IDN_APP) as s:
            rows = {e.title: e for e in s.scalars(select(Event).where(Event.source == "crawl"))}
            for e in rows.values():
                s.expunge(e)
        assert all(e.status == "pending_review" for e in rows.values())
        walk = rows["秋季社區健走與量血壓"]
        assert walk.category == "walk" and walk.points_reward == 30 and walk.capacity == 40
        assert {"bp", "steps", "outdoor", "gentle", "free"} <= set(walk.tags)
        assert walk.start_at.astimezone(TZ).strftime("%H:%M") == "08:30"
        assert walk.organizer == "北區區公所"
        talk = rows["控糖飲食與血糖量測講座"]
        assert (talk.points_reward, talk.capacity, talk.venue) == (20, 30, "和平里社區關懷據點")
        assert talk.start_at.astimezone(TZ).hour == 14 and talk.end_at.astimezone(TZ).hour == 16
        blob = " ".join(e.description for e in rows.values())
        # C-8：電話、Email、@ 提及在落地前移除；發文者欄位整個丟掉
        assert "0912" not in blob and "5123456" not in blob and "@和平里長" not in blob
        assert "north.event@example.tw" not in blob and "[電話已移除]" in blob
        assert not any("unknown-blog" in (e.source_url or "") for e in rows.values())
        # 同一則不重複寫入
        assert sync_events(client=MockEventClient(), use_llm=False).written == 0

    def test_merchant_ratings_sync_only_touches_rating_columns(
        self, dining: dict[str, str]
    ) -> None:
        class Fixed:
            def ratings(self, merchant_ids: Any) -> dict[str, Rating]:
                return {dining["unrated"]: Rating(Decimal("4.1"), 9, "mock")}

        summary = sync_merchant_ratings(client=Fixed())
        assert summary.errors == 0 and summary.written == 1
        with session_scope(DbRole.IDN_APP) as s:
            merchant = s.get_one(Merchant, dining["unrated"])
            assert (merchant.rating, merchant.rating_count) == (Decimal("4.1"), 9)
            assert merchant.api_key_hash == "x"


# --- A1 每日報告與推播 ---


class TestA1Events:
    def test_daily_report_and_push_without_health_reason(
        self, wang: Person, make_event: MakeEvent
    ) -> None:
        event_id = make_event(title="週末量測站", tags=["bp", "indoor"], points_reward=20)
        content = a1.run_daily(wang.user_id, TODAY)
        assert content is not None
        assert any(e["event_id"] == event_id for e in content["events"])
        assert any(line.startswith("▸ 這週附近") for line in content["lines"])
        assert a1.deliver(wang.user_id, TODAY)
        with session_scope(DbRole.IDN_APP) as s:
            [note] = s.scalars(
                select(Notification).where(
                    Notification.user_id == wang.user_id, Notification.kind == "event"
                )
            ).all()
            assert note.title == t("events.push.title", title="週末量測站")
            assert "血壓" not in note.title + note.body and "20 點" in note.body
            assert note.payload == {"event_id": event_id}

    def test_weekly_cap_and_opt_out(self, wang: Person, make_event: MakeEvent) -> None:
        events = [
            {"event_id": make_event(), "push_title": f"近日活動：{i}", "push_body": "x"}
            for i in range(3)
        ]
        scope = a1.current_scope(wang.user_id)
        with a1.tool_context(wang.user_id) as ctx:
            pushed = [a1.push_event(ctx, wang.user_id, scope, events) for _ in range(3)]
        assert pushed == [True, True, False]  # 每週上限 2
        with session_scope(DbRole.IDN_APP) as s:
            prefs.save_prefs(s, wang.user_id, prefs.Prefs(event_push=False))
        with a1.tool_context(wang.user_id) as ctx:
            assert not a1.push_event(ctx, wang.user_id, scope, events)

    def test_chat_proposes_events_and_registration_needs_confirmation(
        self, wang: Person, make_event: MakeEvent
    ) -> None:
        make_event(title="週末量測站", tags=["bp"])
        done = [e for e in a1.chat(wang.user_id, "附近有什麼活動？") if isinstance(e, DoneEvent)]
        assert done and "週末量測站" in done[0].text
        events = list(a1.chat(wang.user_id, "第一個"))
        [card] = [e for e in events if isinstance(e, ConfirmationEvent)]
        assert card.tool == "event_register" and "姓名與電話" in card.summary
        with session_scope(DbRole.IDN_APP) as s:
            assert (
                s.scalar(select(EventRegistration).where(EventRegistration.user_id == wang.user_id))
                is None
            )


# --- API：民眾端與工作人員端 ---


def _staff(factory: Factory, role: str = "site_staff", site: str | None = TEST_SITE) -> str:
    return create_access_token(
        factory.account(role, site_id=site if role == "site_staff" else None)
    )


class TestApi:
    def test_citizen_events_register_and_dashboard(
        self, client: TestClient, factory: Factory, wang: Person, make_event: MakeEvent
    ) -> None:
        token = create_access_token(factory.account("citizen", user_id=wang.user_id))
        event_id = make_event(title="週末量測站", tags=["bp"])
        listed = client.get(f"{API}/me/events", headers=auth(token)).json()
        assert any(e["event_id"] == event_id for e in listed["events"])
        first = client.post(f"{API}/me/events/{event_id}/register", json={}, headers=auth(token))
        assert first.status_code == 202 and first.json()["status"] == "confirmation_required"
        done = client.post(
            f"{API}/me/events/{event_id}/register", json={"confirmed": True}, headers=auth(token)
        )
        assert done.json()["status"] == "executed"
        mine = client.get(f"{API}/me/events", headers=auth(token)).json()["mine"]
        assert [m["event_id"] for m in mine] == [event_id]

        health = client.get(f"{API}/me/health?period=week", headers=auth(token)).json()
        assert health["metrics"]["steps"]["avg"] == 3100

        saved = client.put(
            f"{API}/me/prefs/recommend",
            json={
                "event_categories": ["walk", "bogus"],
                "dietary": ["low_salt"],
                "min_rating": 4.5,
            },
            headers=auth(token),
        ).json()
        assert saved["event_categories"] == ["walk"] and saved["rating_threshold"] == 4.5
        assert client.get(f"{API}/me/merchants/recommend", headers=auth(token)).status_code == 200

    def test_site_checkin_grants_points_once(
        self, client: TestClient, factory: Factory, wang: Person, make_event: MakeEvent
    ) -> None:
        staff = _staff(factory)
        event_id = make_event(start=datetime.now(UTC) - timedelta(minutes=30), points_reward=15)
        qr, _ = issue_user_qr(wang.user_id)
        first = client.post(
            f"{API}/events/{event_id}/checkin", json={"user_qr": qr}, headers=auth(staff)
        ).json()
        assert first["points"] == 15 and first["walk_in"] is True and first["status"] == "attended"
        again = client.post(
            f"{API}/events/{event_id}/checkin", json={"user_qr": qr}, headers=auth(staff)
        ).json()
        assert again["points"] == 0
        with session_scope(DbRole.IDN_APP) as s:
            ledger = s.scalars(
                select(PointsLedger).where(
                    PointsLedger.user_id == wang.user_id, PointsLedger.reason == "event.attend"
                )
            ).all()
            assert [(r.delta, r.ref_id) for r in ledger] == [(15, event_id)]
        names = client.get(f"{API}/events/{event_id}/registrations", headers=auth(staff)).json()
        assert names["rows"][0]["person"] != "王測試" and names["rows"][0]["status"] == "attended"

    def test_checkin_outside_window_and_other_site(
        self, client: TestClient, factory: Factory, wang: Person, make_event: MakeEvent
    ) -> None:
        event_id = make_event()  # 兩天後
        qr, _ = issue_user_qr(wang.user_id)
        early = client.post(
            f"{API}/events/{event_id}/checkin", json={"user_qr": qr}, headers=auth(_staff(factory))
        )
        assert early.status_code == 409
        other = _staff(factory, site=OTHER_SITE)
        assert (
            client.get(f"{API}/events/{event_id}/registrations", headers=auth(other)).status_code
            == 403
        )

    def test_agency_sees_masked_counts_and_reviews_notices(
        self,
        client: TestClient,
        factory: Factory,
        wang: Person,
        make_event: MakeEvent,
    ) -> None:
        agency = _staff(factory, role="agency")
        event_id = make_event()
        _register(wang.user_id, event_id, SCOPE)
        rows = client.get(f"{API}/events?status=published", headers=auth(agency)).json()
        row = next(r for r in rows if r["event_id"] == event_id)
        assert row["registered"] is None and row["count_masked"] is True  # 1 人 < k
        assert (
            client.get(f"{API}/events/{event_id}/registrations", headers=auth(agency)).status_code
            == 403
        )
        pending = make_event(status="pending_review", source="crawl", title="待審公告")
        reviewed = client.post(
            f"{API}/events/{pending}/review",
            json={"decision": "approve", "points_reward": 25},
            headers=auth(agency),
        ).json()
        assert reviewed["status"] == "published" and reviewed["points_reward"] == 25
        site = _staff(factory)
        denied = client.post(
            f"{API}/events/{pending}/review", json={"decision": "reject"}, headers=auth(site)
        )
        assert denied.status_code == 403

    def test_staff_create_is_bound_to_own_site(
        self, client: TestClient, factory: Factory, make_event: MakeEvent
    ) -> None:
        start = datetime.now(UTC) + timedelta(days=3)
        created = client.post(
            f"{API}/events",
            json={
                "title": "長青椅子有氧",
                "category": "exercise",
                "site_id": OTHER_SITE,
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=1)).isoformat(),
                "points_reward": 10,
                "tags": ["gentle", "nope"],
            },
            headers=auth(_staff(factory)),
        ).json()
        try:
            assert created["site_id"] == TEST_SITE and created["status"] == "published"
            assert created["tags"] == ["gentle"] and created["source"] == "site"
        finally:
            with session_scope(DbRole.MIGRATOR) as s:
                s.execute(delete(Event).where(Event.id == created["event_id"]))


# --- 群體統計：活動參與率（k 遮蔽） ---


def test_event_attend_rate_is_published_only_when_n_reaches_k(
    factory: Factory, make_event: MakeEvent
) -> None:
    village = factory.village()
    staff = factory.account("site_staff", site_id=TEST_SITE)
    event_id = make_event(start=datetime.now(UTC) - timedelta(minutes=20))
    people = []
    for i in range(6):
        user = factory.user(name=f"參與者{i}", village=village)
        factory.account("citizen", user_id=user.id)
        people.append(user.id)
    with session_scope(DbRole.IDN_APP) as s:
        event = s.get_one(Event, event_id)
        for user_id in people[:4]:
            ev.check_in(s, event=event, user_id=user_id, staff_account_id=staff.id)

    week = week_start(TODAY)

    def cell() -> Cell:
        """遮蔽前的聚合格（里加總列）：分母是有 App 帳號的人，分子是當週有報到的人。"""
        with session_scope(DbRole.IDN_APP) as s:
            cells = cohort_cells(s, [week], TODAY, 6000)
        return next(
            c
            for c in cells
            if c.metric == "event_attend_rate"
            and c.village_code == village
            and (c.age_band, c.sex) == (ALL, ALL)
        )

    def published() -> CohortWeekly | None:
        run_deidentify(weeks=[week], today=TODAY)
        with session_scope(DbRole.ANON_WRITER) as s:
            return s.scalar(
                select(CohortWeekly).where(
                    CohortWeekly.week == week,
                    CohortWeekly.metric == "event_attend_rate",
                    CohortWeekly.village_code == village,
                )
            )

    assert (cell().n, cell().value) == (6, Decimal("0.6667"))
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text("UPDATE sys.accounts SET active = false WHERE user_id = ANY(:ids)"),
            {"ids": people[:2]},
        )
    assert cell().n == 4
    assert published() is None  # 剩 4 位 App 使用者 < k：這個里的任何一格都不進 anon
