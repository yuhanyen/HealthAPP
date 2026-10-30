"""M1 驗收台：開發環境才開放、文案鍵都存在於 i18n、約束報告端點。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.config import get_settings
from app.consent.catalog import SOURCE_ITEMS
from app.i18n import catalog
from tests.conftest import Factory
from tests.helpers import auth

PAGE = Path(__file__).resolve().parents[1] / "app" / "console" / "index.html"


def _has_key(key: str) -> bool:
    node: object = catalog()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def test_console_served_outside_prod(client: TestClient) -> None:
    response = client.get("/console")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_console_hidden_in_prod(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    assert client.get("/console").status_code == 404


def test_every_ui_string_key_exists_in_i18n() -> None:
    html = PAGE.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n="([\w.\-]+)"', html))
    keys |= set(re.findall(r'\bT\("([\w.\-]+)"', html))
    keys |= {f"console.constraints.C-{i}" for i in range(1, 9)}
    keys |= {f"console.admin.cols.{c}" for c in re.findall(r'\bcol\("(\w+)"\)', html)}
    assert keys, "應該抓得到文案鍵"
    assert sorted(k for k in keys if not _has_key(k)) == []


def test_every_consent_source_and_item_has_a_label() -> None:
    missing = [
        key
        for source, items in SOURCE_ITEMS.items()
        for key in (f"consent.source.{source}.name", *(f"consent.items.{i}" for i in items))
        if not _has_key(key)
    ]
    assert missing == []


def test_i18n_endpoint_serves_catalog(client: TestClient) -> None:
    body = client.get("/api/v1/i18n").json()
    assert body["consent"]["revoke"]["confirm"].startswith("確定要撤回嗎？")


def test_constraints_report_is_admin_only(client: TestClient, factory: Factory) -> None:
    _, _, citizen_token = factory.citizen()
    assert client.get("/api/v1/admin/constraints", headers=auth(citizen_token)).status_code == 403
    admin_token = create_access_token(factory.account("admin"))
    body = client.get("/api/v1/admin/constraints", headers=auth(admin_token)).json()
    assert set(body) == {"generated_at", "constraints"}
