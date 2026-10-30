"""idn 欄位加密（SPEC §11）與據點角色（A5）的 RLS。"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.db.crypto import hash_national_id
from app.db.engines import DbRole, session_scope, site_scoped_session
from app.db.models_idn import CareTask, User
from tests.conftest import Factory
from tests.helpers import TEST_SITE


def test_sensitive_columns_are_encrypted_at_rest(factory: Factory) -> None:
    user = factory.user(name="林加密", phone="0911222333")
    with session_scope(DbRole.MIGRATOR) as s:
        raw = s.execute(
            text("SELECT name, phone, address FROM idn.users WHERE id = :id"), {"id": user.id}
        ).one()
    assert all(value.startswith("v1.") for value in raw)
    assert "林加密" not in raw.name and "0911222333" not in raw.phone
    with session_scope(DbRole.IDN_APP) as s:
        loaded = s.get_one(User, user.id)
        assert (loaded.name, loaded.phone) == ("林加密", "0911222333")


def test_national_id_hash_is_keyed_and_normalized() -> None:
    assert hash_national_id(" a123456789 ") == hash_national_id("A123456789")
    assert hash_national_id("A123456789") != hash_national_id("A123456780")
    assert len(hash_national_id("A123456789")) == 64


def test_site_role_sees_only_its_own_tasks_and_minimal_columns(factory: Factory) -> None:
    mine = factory.care_task(factory.user(name="本據點民眾").id)
    with session_scope(DbRole.MIGRATOR) as s:
        s.execute(
            text(
                "INSERT INTO pub.sites (id, name, type, village_code, lat, lng, hours, equipment) "
                "VALUES ('S-OTHER', '他據點', 'care_point', 'T01', 0, 0, '{}', '[]') "
                "ON CONFLICT DO NOTHING"
            )
        )
    other = factory.care_task(factory.user(name="他據點民眾").id, site_id="S-OTHER")

    with site_scoped_session(TEST_SITE) as s:
        codes = set(s.scalars(select(CareTask.code)))
        assert mine.code in codes and other.code not in codes
        names = set(s.execute(text("SELECT id FROM idn.users")).scalars())
        assert mine.user_id in names and other.user_id not in names

    with site_scoped_session(TEST_SITE) as s, pytest.raises(ProgrammingError) as exc:
        s.execute(text("SELECT birth_date FROM idn.users"))
    assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)
