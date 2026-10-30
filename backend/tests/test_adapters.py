"""六個 Adapter（M2）：Mock 行為、正規化、同步流程。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text

from app.adapters import runner
from app.adapters.base import FetchContext, RawRecord
from app.adapters.env import EnvAdapter, heat_index_c, parse_cwa, parse_moenv
from app.adapters.merchant import MerchantAdapter, hash_api_key, verify_api_key
from app.adapters.mocks.env import MockEnvClient
from app.adapters.mocks.wearable import MockWearableClient
from app.adapters.nhi import NhiAdapter
from app.adapters.sentiment import SentimentAdapter, parse_place
from app.adapters.sentiment_land import sync_sentiment
from app.adapters.site import SiteAdapter
from app.adapters.wearable import WearableAdapter
from app.db.crypto import hash_national_id
from app.db.engines import DbRole, session_scope
from app.db.models_anon import SentimentItem
from app.db.models_idn import FhirResource
from app.db.models_pub import EnvHourly, Merchant
from app.events.bus import EventBus
from app.fhir import codes
from tests.conftest import Factory

TODAY = date(2026, 10, 30)


@pytest.fixture
def wearable_mock() -> MockWearableClient:
    return MockWearableClient(today=TODAY)


class TestWearable:
    def test_hourly_is_deterministic_and_sums_to_daily_total(
        self, wearable_mock: MockWearableClient
    ) -> None:
        subject = hash_national_id("ANYONE")
        day = TODAY - timedelta(days=3)
        assert wearable_mock.hourly_steps(subject, day) == wearable_mock.hourly_steps(subject, day)
        assert sum(wearable_mock.hourly_steps(subject, day)) == wearable_mock.daily_total(
            subject, day
        )
        assert wearable_mock.hourly_steps(subject, day)[2] == 0  # 凌晨沒有步數

    def test_persona_steps_drop_about_forty_percent(
        self, wearable_mock: MockWearableClient
    ) -> None:
        wang = hash_national_id("DEMO000001")
        before = (
            sum(wearable_mock.daily_total(wang, TODAY - timedelta(days=d)) for d in range(15, 29))
            / 14
        )
        after = (
            sum(wearable_mock.daily_total(wang, TODAY - timedelta(days=d)) for d in range(1, 15))
            / 14
        )
        assert 0.3 < 1 - after / before < 0.5

    def test_cheat_fixture_has_impossible_hours(self, wearable_mock: MockWearableClient) -> None:
        cheat = hash_national_id("DEMO000010")
        hours = wearable_mock.hourly_steps(cheat, TODAY - timedelta(days=1))
        assert hours[14] > 3000 and hours[15] > 3000
        assert wearable_mock.daily_total(cheat, TODAY - timedelta(days=2)) > 40000

    def test_fetch_and_normalize(self, wearable_mock: MockWearableClient) -> None:
        adapter = WearableAdapter(wearable_mock)
        start = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
        raws = adapter.fetch(
            start, FetchContext(subject_ref=hash_national_id("X"), until=start + timedelta(hours=3))
        )
        assert len(raws) == 3
        resources = adapter.normalize(raws[0])
        assert [r["code"]["coding"][0]["code"] for r in resources] == [
            codes.STEPS_HOURLY,
            codes.HEART_RATE,
        ]
        assert datetime.fromisoformat(resources[0]["effectivePeriod"]["start"]) == start


class TestNhi:
    def test_bundle_matched_by_hashed_national_id_and_patient_dropped(self) -> None:
        adapter = NhiAdapter()
        raws = adapter.fetch(
            datetime(2000, 1, 1, tzinfo=UTC),
            FetchContext(subject_ref=hash_national_id("DEMO000001")),
        )
        assert len(raws) == 1
        types = [r["resourceType"] for r in adapter.normalize(raws[0])]
        assert "Patient" not in types
        assert {"Encounter", "Condition", "MedicationStatement", "Immunization"} <= set(types)

    def test_unknown_subject_returns_nothing(self) -> None:
        raws = NhiAdapter().fetch(
            datetime(2000, 1, 1, tzinfo=UTC), FetchContext(subject_ref=hash_national_id("NOBODY"))
        )
        assert raws == []

    def test_sync_nhi_respects_consent_and_scope(self, factory: Factory, bus: EventBus) -> None:
        nid_hash = hash_national_id("DEMO000007")
        user = factory.user(national_id_hash=nid_hash)
        factory.consent(user.id, "nhi", ["visits", "conditions"])  # 不含用藥、過敏
        summary = runner.sync_nhi(user_ids={user.id})
        assert summary.users == 1 and summary.errors == 0
        with session_scope(DbRole.IDN_APP) as s:
            types = set(
                s.scalars(select(FhirResource.resource_type).where(FhirResource.user_id == user.id))
            )
        assert types == {"Encounter", "Condition"}
        assert any(d.event["type"] == "nhi.updated" for d in bus.recent(10))


class TestSite:
    def test_normalize_all_items(self) -> None:
        at = datetime(2026, 10, 30, 9, tzinfo=UTC)
        raw = RawRecord(
            "site",
            {
                "site_id": "S01",
                "measured_at": at,
                "device_id": "BT-7",
                "items": {
                    "bp": [138, 88],
                    "weight": 60.2,
                    "height": 158,
                    "body_fat": 31.5,
                    "glucose": 105,
                },
            },
            at,
        )
        resources = SiteAdapter().normalize(raw)
        assert [r["code"]["coding"][0]["code"] for r in resources] == [
            codes.BP_PANEL,
            codes.BODY_WEIGHT,
            codes.BODY_HEIGHT,
            codes.BODY_FAT,
            codes.GLUCOSE,
        ]
        assert resources[0]["performer"][0]["identifier"]["value"] == "S01"
        assert resources[0]["device"]["identifier"]["value"] == "BT-7"


class TestEnv:
    @pytest.mark.parametrize(
        ("temp", "rh", "low", "high"), [(32, 70, 40, 42.5), (25, 50, 24, 26), (35, 60, 44, 47)]
    )
    def test_heat_index(self, temp: float, rh: float, low: float, high: float) -> None:
        assert low <= heat_index_c(temp, rh) <= high

    def test_parse_open_data_payloads(self) -> None:
        cwa = {
            "records": {
                "Station": [
                    {
                        "StationId": "C0X999",
                        "ObsTime": {"DateTime": "2026-10-30T10:00:00+08:00"},
                        "WeatherElement": {
                            "AirTemperature": 27.4,
                            "RelativeHumidity": 72,
                            "Now": {"Precipitation": 0.5},
                        },
                    }
                ]
            }
        }
        assert parse_cwa(cwa, "C0X999") == {
            "hour": datetime.fromisoformat("2026-10-30T10:00:00+08:00"),
            "temp": 27.4,
            "humidity": 72.0,
            "rain": True,
        }
        assert parse_cwa(cwa, "OTHER") is None
        moenv = {"records": [{"sitename": "竹東", "aqi": "48"}, {"sitename": "新竹", "aqi": "55"}]}
        assert parse_moenv(moenv, "新竹") == 55
        assert parse_moenv([{"sitename": "新竹", "aqi": ""}], "新竹") is None

    def test_sync_env_is_incremental(self) -> None:
        now = datetime(2001, 2, 3, 12, 30, tzinfo=UTC)
        adapter = EnvAdapter(MockEnvClient())
        first = runner.sync_env(now=now, adapter=adapter)
        again = runner.sync_env(now=now, adapter=adapter)
        assert first.written >= 24 and again.written == 0
        with session_scope(DbRole.IDN_APP) as s:
            row = s.scalars(select(EnvHourly).order_by(EnvHourly.hour.desc())).first()
            assert row is not None and row.heat_index is not None


class TestSentiment:
    def test_places_payload_is_stripped_on_normalize(self) -> None:
        payload = {
            "googleMapsUri": "https://maps.google.com/?cid=99",
            "reviews": [
                {
                    "rating": 2,
                    "publishTime": "2026-10-29T03:00:00Z",
                    "text": {"text": "排隊太久 @某人 打 0912-111-222"},
                    "authorAttribution": {
                        "displayName": "陳大文",
                        "uri": "https://x/contrib/1",
                        "photoUri": "https://lh3/a.png",
                    },
                }
            ],
        }
        [item] = parse_place(payload, "N01")
        adapter = SentimentAdapter(["https://maps.google.com/*"])
        [record] = adapter.normalize(RawRecord("sentiment", item, datetime.now(UTC)))
        dumped = json.dumps(record, ensure_ascii=False, default=str)
        for leak in ("陳大文", "contrib", "a.png", "某人", "0912-111-222"):
            assert leak not in dumped
        assert record["village_code"] == "N01"

    def test_sync_lands_whitelisted_items_without_identity(self) -> None:
        with session_scope(DbRole.MIGRATOR) as s:
            s.execute(delete(SentimentItem))
            for pattern in ("https://maps.google.com/*", "https://forum.example.tw/*"):
                s.execute(
                    text(
                        "INSERT INTO pub.source_whitelist (source_class, url_pattern, approved_by) "
                        "VALUES ('test', :p, '測試')"
                    ),
                    {"p": pattern},
                )
        summary = sync_sentiment()
        assert summary.errors == 0 and summary.written >= 4
        assert summary.skipped >= 1  # news.example.tw 不在本測試的白名單
        assert sync_sentiment().written == 0  # 游標：不重複落地
        with session_scope(DbRole.ANON_READER) as s:
            summaries = " ".join(s.scalars(select(SentimentItem.summary)))
        for leak in ("林○○", "0912-000-111", "mock.resident@example.com", "里長辦公室"):
            assert leak not in summaries


class TestMerchant:
    def test_api_key_hash_check(self, factory: Factory) -> None:
        with session_scope(DbRole.MIGRATOR) as s:
            s.add(
                Merchant(
                    id="MT1",
                    name="測試商家",
                    category="早餐店",
                    village_code="T01",
                    lat=0,
                    lng=0,
                    hours={},
                    api_key_hash=hash_api_key("secret-key"),
                )
            )
        with session_scope(DbRole.IDN_APP) as s:
            assert verify_api_key(s, "MT1", "secret-key")
            assert not verify_api_key(s, "MT1", "wrong")
            offer = MerchantAdapter().upsert_offer(
                s,
                {
                    "merchant_id": "MT1",
                    "offer_id": "MT1-O1",
                    "title": " 豆漿 ",
                    "points_cost": 50,
                    "stock": 10,
                    "valid_from": "2026-10-01",
                    "valid_to": "2026-12-31",
                },
            )
            assert offer.title == "豆漿"


def test_sync_wearable_end_to_end_is_incremental(factory: Factory, bus: EventBus) -> None:
    user = factory.user(national_id_hash=hash_national_id("SYNCTEST01"))
    factory.consent(user.id, "wearable", ["steps"])  # 沒授權心率
    now = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
    adapter = WearableAdapter(MockWearableClient(today=TODAY))
    first = runner.sync_wearable(now=now, adapter=adapter, user_ids={user.id}, lookback_hours=6)
    second = runner.sync_wearable(now=now + timedelta(hours=2), adapter=adapter, user_ids={user.id})
    assert (first.written, first.skipped) == (6, 6)  # 6 小時步數；心率不在授權範圍
    assert second.written == 2
    with session_scope(DbRole.IDN_APP) as s:
        codes_written = set(
            s.scalars(select(FhirResource.code).where(FhirResource.user_id == user.id))
        )
        total = s.scalar(
            select(func.count()).select_from(FhirResource).where(FhirResource.user_id == user.id)
        )
    assert codes_written == {codes.STEPS_HOURLY} and total == 8
