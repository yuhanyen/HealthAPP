"""EnvAdapter（SPEC §5）：氣象署測站觀測 + 環境部空品，每小時；無金鑰則 Mock。

真實串接需在 .env 設定 CWA_API_KEY、MOENV_API_KEY、ENV_CWA_STATION_ID、ENV_MOENV_SITE。
（解析邏輯有單元測試；一期開發機無金鑰，未以真實金鑰驗證。）
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.adapters.base import FetchContext, RawRecord, mock_notice
from app.adapters.mocks.env import MockEnvClient
from app.config import get_settings

log = logging.getLogger(__name__)

CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001"
MOENV_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_432"


def heat_index_c(temp_c: float, humidity: float) -> float:
    """美國 NWS 熱指數（Rothfusz 迴歸＋低溫簡式），輸入輸出皆為攝氏。"""
    t = temp_c * 9 / 5 + 32
    rh = humidity
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi = simple
    else:
        hi = (
            -42.379
            + 2.04901523 * t
            + 10.14333127 * rh
            - 0.22475541 * t * rh
            - 0.00683783 * t * t
            - 0.05481717 * rh * rh
            + 0.00122874 * t * t * rh
            + 0.00085282 * t * rh * rh
            - 0.00000199 * t * t * rh * rh
        )
        if rh < 13 and 80 <= t <= 112:
            hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return round((hi - 32) * 5 / 9, 1)


def parse_cwa(payload: dict[str, Any], station_id: str) -> dict[str, Any] | None:
    for station in payload.get("records", {}).get("Station", []):
        if station.get("StationId") != station_id:
            continue
        element = station.get("WeatherElement", {})
        return {
            "hour": datetime.fromisoformat(station["ObsTime"]["DateTime"]),
            "temp": float(element["AirTemperature"]),
            "humidity": float(element["RelativeHumidity"]),
            "rain": float(element.get("Now", {}).get("Precipitation", 0) or 0) > 0,
        }
    return None


def parse_moenv(payload: dict[str, Any] | list[Any], site: str) -> int | None:
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    for record in records:
        if record.get("sitename") == site and str(record.get("aqi", "")).strip().isdigit():
            return int(record["aqi"])
    return None


class RealEnvClient:
    def __init__(self, cwa_key: str, moenv_key: str, station: str, site: str) -> None:
        self.cwa_key, self.moenv_key, self.station, self.site = cwa_key, moenv_key, station, site

    def hourly(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with httpx.Client(timeout=15) as client:
            cwa = client.get(
                CWA_URL, params={"Authorization": self.cwa_key, "StationId": self.station}
            ).json()
            moenv = client.get(
                MOENV_URL, params={"api_key": self.moenv_key, "format": "json", "limit": 1000}
            ).json()
        obs = parse_cwa(cwa, self.station)
        aqi = parse_moenv(moenv, self.site)
        if obs is None or aqi is None:
            log.error("環境開放資料解析失敗（測站 %s／空品站 %s）", self.station, self.site)
            return []
        return [{**obs, "aqi": aqi}]


class EnvClient(Protocol):
    def hourly(self, start: datetime, end: datetime) -> list[dict[str, Any]]: ...


def default_env_client() -> EnvClient:
    s = get_settings()
    if s.cwa_api_key and s.moenv_api_key and s.env_cwa_station_id and s.env_moenv_site:
        return RealEnvClient(s.cwa_api_key, s.moenv_api_key, s.env_cwa_station_id, s.env_moenv_site)
    mock_notice("EnvAdapter", "未設定氣象署／環境部金鑰")
    return MockEnvClient()


class EnvAdapter:
    source = "env"

    def __init__(self, client: EnvClient | None = None) -> None:
        self.client = client or default_env_client()

    def schedule(self) -> str:
        return "5 * * * *"

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        until = (ctx.until if ctx and ctx.until else None) or datetime.now(UTC)
        now = datetime.now(UTC)
        return [RawRecord(self.source, r, now) for r in self.client.hourly(since, until)]

    def normalize(self, raw: RawRecord) -> list[dict[str, Any]]:
        p = raw.payload
        hour: datetime = p["hour"].replace(minute=0, second=0, microsecond=0)
        return [
            {
                "hour": hour,
                "temp": Decimal(str(p["temp"])),
                "heat_index": Decimal(str(heat_index_c(float(p["temp"]), float(p["humidity"])))),
                "aqi": int(p["aqi"]),
                "rain": bool(p["rain"]),
            }
        ]
