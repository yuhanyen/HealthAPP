"""M7（SPEC §11、§14）：App 使用指標的去識別化聚合、KPI 報表、API 速率限制。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import create_access_token
from app.config import get_settings
from app.db.engines import DbRole, session_scope
from app.db.models_anon import AppMetricsDaily
from app.db.models_idn import DailyReport
from app.deidentify.aggregate import run_deidentify
from app.services import artifacts
from tests.conftest import Factory
from tests.fixtures import TODAY, TZ
from tests.helpers import auth

API = "/api/v1"
YESTERDAY = TODAY - timedelta(days=1)


def _active_users(factory: Factory, n: int) -> None:
    """n 位 App 使用者昨天打開了每日報告。"""
    for i in range(n):
        user, _, _ = factory.citizen()
        with session_scope(DbRole.IDN_APP) as s:
            s.add(
                DailyReport(
                    user_id=user.id,
                    report_date=YESTERDAY - timedelta(days=i % 2),
                    content={},
                    opened_at=datetime.combine(YESTERDAY, time(8, i), TZ),
                )
            )


def test_app_metrics_are_aggregated_only_above_k(factory: Factory) -> None:
    _active_users(factory, 6)
    run_deidentify(weeks=[], today=TODAY)
    with session_scope(DbRole.ANON_READER) as s:
        row = s.get(AppMetricsDaily, YESTERDAY)
        assert row is not None and row.dau >= 6 and row.mau >= row.dau
        assert 0 <= row.elder_mode_rate <= 1 and 0 <= row.push_open_rate <= 1
        columns = set(AppMetricsDaily.__table__.columns.keys())
    assert columns == {
        "day",
        "dau",
        "mau",
        "retention_7",
        "retention_30",
        "push_open_rate",
        "elder_mode_rate",
    }  # 只有總數與比例，沒有任何人的欄位


def test_kpi_has_targets_status_and_report(client: TestClient, factory: Factory) -> None:
    token = create_access_token(factory.account("agency"))
    data = client.get(f"{API}/agency/kpi?period=month", headers=auth(token)).json()
    for item in data["items"]:
        assert item["status"] in ("met", "missed", "watch", "unknown") and "target" in item
    assert isinstance(data["app"], list)
    built = client.post(f"{API}/agency/kpi/report?period=month", headers=auth(token)).json()
    path = artifacts.path_of(built["artifact"])
    assert path is not None
    html = path.read_text(encoding="utf-8")
    assert "計畫 KPI 月報" in html and "anon.cohort_weekly" in html and "k" in html


def test_login_is_rate_limited(client: TestClient, factory: Factory) -> None:
    account = factory.account("citizen")
    form = {"username": account.username, "password": "wrong-password"}
    codes = [client.post(f"{API}/auth/token", data=form).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # 其他帳號不受影響
    other = factory.account("citizen")
    ok = client.post(
        f"{API}/auth/token", data={"username": other.username, "password": "pw-test-1234"}
    )
    assert ok.status_code == 200


def test_chat_is_rate_limited(
    client: TestClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_chat_per_min", 2)
    _, _, token = factory.citizen()
    codes = [
        client.post(f"{API}/me/chat", json={"message": "嗨"}, headers=auth(token)).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_app_metrics_table_never_has_small_population(factory: Factory) -> None:
    """月活躍 < k 的日子不發布（這裡檢查已發布的每一列）。"""
    with session_scope(DbRole.ANON_READER) as s:
        rows = s.scalars(select(AppMetricsDaily)).all()
        assert all(r.mau >= 5 for r in rows)
