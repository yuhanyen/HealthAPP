"""A1 每日批次、07:00 推送、異常事件、撤回後清除（SPEC §8.2、§8.4、§14 M4 驗收）。"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text

from app.agents import a1
from app.consent import service as consent_service
from app.db.engines import DbRole, session_scope
from app.db.models_idn import ChatMessage, DailyReport, Notification, TwinSnapshot
from app.fhir import builders
from app.fhir.writer import ingest
from tests.conftest import Factory
from tests.fixtures import SCOPE, TODAY, Person, _at
from tests.helpers import audit_rows


def _walker(factory: Factory, steps: int = 7000) -> uuid.UUID:
    """只授權穿戴步數的一般民眾，近兩週每天固定步數。"""
    user = factory.user()
    factory.consent(user.id, "wearable", ["steps"])
    with session_scope(DbRole.IDN_APP) as s:
        ingest(
            s,
            user_id=user.id,
            source="wearable",
            emit_events=False,
            resources=[
                builders.steps_daily(_at(TODAY - timedelta(days=d), 23), steps)
                for d in range(1, 15)
            ],
        )
    return user.id


def test_report_follows_skill_format_for_high_risk(wang: Person, open_sites: None) -> None:
    content = a1.run_daily(wang.user_id)
    assert content is not None
    assert content["risk"]["level"] in ("L2", "L3") and content["priority"] == "safety"
    assert content["title"].startswith("【今日健康】")
    # SKILL 輸出格式：關鍵指標／今日建議／待你決定／點數，每個建議附「依據」
    assert any(line.startswith("▸ 關鍵指標") for line in content["lines"])
    assert any(line.startswith("▸ 今日建議") and "依據：" in line for line in content["lines"])
    assert content["decision"] and content["decision"]["slots"]
    assert any(line.startswith("▸ 待你決定") for line in content["lines"])
    assert any(line.startswith("▸ 點數") for line in content["lines"])
    # 有高血壓就醫紀錄 → 運動處方走保守版本；只查時段、不預約
    assert content["recommendation"]["intensity"] == "low"
    with session_scope(DbRole.IDN_APP) as s:
        assert (
            s.scalar(
                text("SELECT count(*) FROM idn.bookings WHERE user_id = :u"), {"u": wang.user_id}
            )
            == 0
        )
    assert content["sources_used"] == sorted(SCOPE)


def test_twin_snapshot_matches_spec_shape(wang: Person, open_sites: None) -> None:
    a1.run_daily(wang.user_id)
    twin = a1.latest_twin(wang.user_id)
    assert twin is not None and twin.as_of == TODAY
    state = twin.state
    for key in ("as_of", "vitals", "conditions", "medications", "risk", "prefs", "consent_scope"):
        assert key in state
    assert state["vitals"]["bp_last3"] == [[139, 90], [145, 93], [142, 91]]
    assert state["vitals"]["steps_14d_avg"] < state["vitals"]["steps_prev14d_avg"]
    assert state["conditions"] == ["I10"]
    assert "medications" not in SCOPE["nhi"] and state["medications"] == []
    # 同一天重跑：覆寫，不新增
    a1.run_daily(wang.user_id)
    with session_scope(DbRole.IDN_APP) as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(TwinSnapshot)
                .where(TwinSnapshot.user_id == wang.user_id)
            )
            == 1
        )


def test_report_only_uses_consented_sources(factory: Factory) -> None:
    user_id = _walker(factory)
    content = a1.run_daily(user_id)
    assert content is not None
    assert [m["metric"] for m in content["metrics"]] == ["steps"]
    assert content["decision"] is None and content["sources_used"] == ["wearable"]
    assert "依據" in content["lines"][0]


def test_no_consent_no_report(factory: Factory) -> None:
    user = factory.user()
    assert a1.run_daily(user.id) is None


def test_ten_users_receive_report_at_seven(factory: Factory) -> None:
    """M4 驗收：10 位 Mock 使用者 07:00 收到報告。"""
    users = [_walker(factory, steps=5000 + 300 * i) for i in range(10)]
    summary = a1.run_daily_batch(TODAY, user_ids=users, parallel=4)
    assert (summary.reports, summary.failed) == (10, 0)
    assert a1.deliver_reports(TODAY) >= 10
    with session_scope(DbRole.IDN_APP) as s:
        notes = s.scalars(
            select(Notification).where(
                Notification.user_id.in_(users), Notification.kind == "daily_report"
            )
        ).all()
        assert len(notes) == 10 and all(n.status == "sent" for n in notes)
        assert all(n.payload == {"report_date": TODAY.isoformat()} for n in notes)
        delivered = s.scalars(
            select(DailyReport.delivered_at).where(DailyReport.user_id.in_(users))
        ).all()
        assert all(delivered)
    # 推送也經工具層，留稽核
    assert len(audit_rows(tool="notify", decision="executed")) >= 10
    # 已送出的不會重送
    assert a1.deliver_reports(TODAY) == 0


def test_measurement_alert_once_per_day(wang: Person, open_sites: None) -> None:
    assert a1.on_measurement(wang.user_id) is True
    assert a1.on_measurement(wang.user_id) is False
    with session_scope(DbRole.IDN_APP) as s:
        alerts = s.scalars(
            select(Notification).where(
                Notification.user_id == wang.user_id, Notification.kind == "alert"
            )
        ).all()
    assert len(alerts) == 1 and "複測" in alerts[0].body


def test_normal_measurement_does_not_alert(factory: Factory) -> None:
    user_id = _walker(factory)
    factory.consent(user_id, "site", ["bp"])
    factory.fhir(user_id, "site")
    assert a1.on_measurement(user_id) is False


def test_revoke_deletes_reports_citing_source_and_chats(wang: Person, open_sites: None) -> None:
    a1.run_daily(wang.user_id, TODAY)
    with session_scope(DbRole.IDN_APP) as s:
        s.add(ChatMessage(user_id=wang.user_id, role="user", content="我最近好嗎？"))
        old = DailyReport(
            user_id=wang.user_id,
            report_date=TODAY - timedelta(days=30),
            content={"sources_used": ["wearable"], "status_sentence": "穩定"},
        )
        s.add(old)
    with session_scope(DbRole.IDN_APP) as s:
        consent = consent_service.get_active(s, wang.user_id, "nhi")
        assert consent is not None
        result = consent_service.revoke(
            s,
            user_id=wang.user_id,
            consent_id=consent.id,
            actor_type="user",
            actor_id=str(wang.user_id),
        )
        counts = consent_service.run_deletion_job(s, result.job.id)
    assert counts is not None
    assert counts["daily_reports"] == 1 and counts["chat_messages"] == 1
    with session_scope(DbRole.IDN_APP) as s:
        left = s.scalars(
            select(DailyReport.report_date).where(DailyReport.user_id == wang.user_id)
        ).all()
    assert left == [TODAY - timedelta(days=30)]  # 沒引用健保的舊報告保留
    # 之後的報告不再引用就醫資料
    content = a1.run_daily(wang.user_id, TODAY)
    assert content is not None and "nhi" not in content["sources_used"]
    assert content["recommendation"]["intensity"] == "low"  # 仍因 L2 風險走保守版


def test_batch_isolates_failures(factory: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    good = _walker(factory)
    bad = _walker(factory)
    real = a1.run_daily

    def flaky(user_id: uuid.UUID, day: date | None = None) -> object:
        if user_id == bad:
            raise RuntimeError("boom")
        return real(user_id, day)

    monkeypatch.setattr(a1, "run_daily", flaky)
    summary = a1.run_daily_batch(TODAY, user_ids=[good, bad], parallel=2)
    assert (summary.reports, summary.failed) == (1, 1)
