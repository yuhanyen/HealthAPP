"""測試共用設定：每次測試 session 建一個全新的 healthapp_test 資料庫並跑完所有 migrations。

約束測試必須打真的 PostgreSQL（角色、RLS、trigger 都是 DB 層行為），連不到就直接失敗、不略過。
"""

from __future__ import annotations

import os

os.environ["PG_DB"] = os.environ.get("HEALTHAPP_TEST_DB", "healthapp_test")
os.environ["APP_ENV"] = "test"
os.environ["EVENT_BUS"] = "memory"
os.environ["BCRYPT_ROUNDS"] = "4"  # 測試加速；正式環境預設 12
os.environ["ANTHROPIC_API_KEY"] = ""  # 測試一律走規則式（不呼叫外部 LLM）

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import fakeredis
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import text

from app.auth.security import create_access_token, hash_password
from app.config import get_settings
from app.consent import service as consent_service
from app.consent.schemas import ConsentScope
from app.db.engines import DbRole, dispose_engines, session_scope
from app.db.models_idn import CareTask, Consent, FhirResource, User
from app.db.models_sys import Account
from app.events.bus import EventBus, set_bus
from app.fhir import builders
from app.fhir.writer import primary_code
from tests.helpers import OTHER_SITE, TEST_SITE, TEST_VILLAGE

BACKEND = Path(__file__).resolve().parents[1]
pytest_plugins = ["tests.fixtures"]  # 跨檔共用 fixture（wang、open_sites）


def _admin_conninfo(dbname: str = "postgres") -> str:
    s = get_settings()
    return (
        f"host={s.pg_host} port={s.pg_port} dbname={dbname} "
        f"user={s.pg_superuser} password={s.pg_superpass}"
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    name = get_settings().pg_db
    try:
        with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"連不到 PostgreSQL（{exc}）。請先執行 make infra（或 docker compose up -d db）。",
            returncode=3,
        )
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app" / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text(
                "INSERT INTO pub.villages (code, name, district, population) "
                "VALUES (:c, '測試里', '測試區', 1000) ON CONFLICT DO NOTHING"
            ),
            {"c": TEST_VILLAGE},
        )
        s.execute(
            text(
                "INSERT INTO pub.sites (id, name, type, village_code, lat, lng, hours, equipment) "
                "VALUES (:id, '測試據點', 'health_station', :v, 24.8, 120.97, '{}', '[]'), "
                "(:other, '他據點', 'care_point', :v, 24.81, 120.96, '{}', '[]') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": TEST_SITE, "other": OTHER_SITE, "v": TEST_VILLAGE},
        )
    yield
    dispose_engines()


@pytest.fixture
def bus() -> Iterator[EventBus]:
    """每個測試一個獨立的 fakeredis，事件互不干擾。"""
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    event_bus = EventBus(client, stream=get_settings().event_stream)
    previous = set_bus(event_bus)
    yield event_bus
    set_bus(previous)


@pytest.fixture
def client(bus: EventBus) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@dataclass
class Factory:
    """建立測試資料；每筆都 commit，讓其他角色的連線（RLS、guard、稽核）看得到。"""

    def user(
        self,
        *,
        name: str = "測試民眾",
        phone: str | None = "0912000000",
        village: str = TEST_VILLAGE,
        sex: str = "F",
        birth_date: date = date(1955, 3, 1),
        national_id_hash: str | None = None,
    ) -> User:
        with session_scope(DbRole.IDN_APP) as s:
            user = User(
                national_id_hash=national_id_hash or uuid.uuid4().hex,
                name=name,
                birth_date=birth_date,
                sex=sex,
                phone=phone,
                address="測試區測試路 1 號",
                village_code=village,
            )
            s.add(user)
            s.flush()
            s.expunge(user)
            return user

    def consent(self, user_id: uuid.UUID, source: str, read: list[str]) -> Consent:
        with session_scope(DbRole.IDN_APP) as s:
            consent = consent_service.grant(
                s,
                user_id=user_id,
                source=source,
                scope=ConsentScope(read=read),
                actor_type="user",
                actor_id=str(user_id),
            )
            s.expunge(consent)
            return consent

    def fhir(self, user_id: uuid.UUID, source: str, *, days_ago: int = 0) -> None:
        """直接寫一筆該來源的典型資源（不經授權檢查，用來佈置刪除／RLS 測試）。"""
        at = datetime.now(UTC) - timedelta(days=days_ago, seconds=1)
        resource = {
            "wearable": lambda: builders.steps_daily(at, 4321),
            "site": lambda: builders.blood_pressure(at, 128, 82, site=TEST_SITE),
            "nhi": lambda: builders.encounter(at, "內科"),
        }[source]()
        with session_scope(DbRole.IDN_APP) as s:
            s.add(
                FhirResource(
                    user_id=user_id,
                    resource_type=resource["resourceType"],
                    resource=resource,
                    source=source,
                    code=primary_code(resource),
                    effective_at=at,
                )
            )

    def account(
        self,
        role: str,
        *,
        user_id: uuid.UUID | None = None,
        site_id: str | None = None,
        merchant_id: str | None = None,
        password: str = "pw-test-1234",
    ) -> Account:
        with session_scope(DbRole.IDN_APP) as s:
            account = Account(
                username=f"{role}-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password(password),
                role=role,
                display_name=f"測試{role}",
                user_id=user_id,
                site_id=site_id,
                merchant_id=merchant_id,
            )
            s.add(account)
            s.flush()
            s.expunge(account)
            return account

    def citizen(self) -> tuple[User, Account, str]:
        user = self.user()
        account = self.account("citizen", user_id=user.id)
        return user, account, create_access_token(account)

    def care_task(self, user_id: uuid.UUID, *, site_id: str = TEST_SITE) -> CareTask:
        with session_scope(DbRole.IDN_APP) as s:
            task = CareTask(
                code=f"N-{uuid.uuid4().hex[:6]}",
                user_id=user_id,
                site_id=site_id,
                task_type="phone_followup",
                rule_id="R-BP-01",
            )
            s.add(task)
            s.flush()
            s.expunge(task)
            return task

    def village(self) -> str:
        """一個全新的里（隔離群體統計測試，不受其他測試的使用者影響）。"""
        code = f"Z{uuid.uuid4().hex[:6]}"
        with session_scope(DbRole.MIGRATOR) as s:
            s.execute(
                text(
                    "INSERT INTO pub.villages (code, name, district, population) "
                    "VALUES (:c, '隔離測試里', '測試區', 100)"
                ),
                {"c": code},
            )
        return code


@pytest.fixture
def factory() -> Factory:
    return Factory()
