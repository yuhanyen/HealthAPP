"""SentimentAdapter（SPEC §5）：公開輿情 → anon.sentiment_items，每 6 小時。

normalize() 第一步就是 strip_identity()（C-8），之後的程式碼只拿得到去識別後的資料；
來源網址必須符合 pub.source_whitelist。Mock 讀 fixtures/sentiment_*.json；
設定 GOOGLE_PLACES_API_KEY 與 PLACES_VILLAGES 時改抓 Google Places 評論（未以真實金鑰驗證）。
NLP 分類（topic／sentiment）由 A4 的 nlp_* 工具在落地後補上。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatch
from typing import Any, Protocol

import httpx

from app.adapters.base import AnonRecord, FetchContext, RawRecord, mock_notice
from app.adapters.mocks.sentiment import MockSentimentClient
from app.config import get_settings
from app.deidentify.strip_identity import strip_identity

log = logging.getLogger(__name__)

SUMMARY_MAX = 280
PLACES_URL = "https://places.googleapis.com/v1/places/{place_id}"


class SentimentClient(Protocol):
    def items(self, since: datetime) -> list[dict[str, Any]]: ...


def parse_place(payload: dict[str, Any], village_code: str | None) -> list[dict[str, Any]]:
    """Places API (New) 的 place 詳細資料 → 輿情原始項目（仍含作者欄位，交給 strip_identity）。"""
    items = []
    for review in payload.get("reviews", []):
        items.append(
            {
                "source_class": "google_places",
                "source_url": payload.get("googleMapsUri", ""),
                "village_code": village_code,
                "collected_at": review.get("publishTime"),
                "rating": review.get("rating"),
                "text": (review.get("text") or {}).get("text", ""),
                "authorAttribution": review.get("authorAttribution", {}),
            }
        )
    return items


class GooglePlacesClient:
    def __init__(self, api_key: str, villages: dict[str, str]) -> None:
        self.api_key, self.villages = api_key, villages

    def items(self, since: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "reviews,googleMapsUri"}
        with httpx.Client(timeout=20, headers=headers) as client:
            for place_id, village in self.villages.items():
                payload = client.get(PLACES_URL.format(place_id=place_id)).json()
                out += [
                    i
                    for i in parse_place(payload, village)
                    if i["collected_at"]
                    and datetime.fromisoformat(i["collected_at"].replace("Z", "+00:00")) > since
                ]
        return out


def default_sentiment_client() -> SentimentClient:
    s = get_settings()
    if s.google_places_api_key and s.places_villages:
        return GooglePlacesClient(s.google_places_api_key, json.loads(s.places_villages))
    mock_notice("SentimentAdapter", "未設定 Google Places 金鑰")
    return MockSentimentClient()


class SentimentAdapter:
    source = "sentiment"

    def __init__(self, whitelist: Sequence[str], client: SentimentClient | None = None) -> None:
        self.whitelist = list(whitelist)
        self._client = client

    @property
    def client(self) -> SentimentClient:
        if self._client is None:
            self._client = default_sentiment_client()
        return self._client

    def schedule(self) -> str:
        return "0 */6 * * *"

    def allowed(self, url: str) -> bool:
        return any(fnmatch(url, pattern) for pattern in self.whitelist)

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        now = datetime.now(UTC)
        return [RawRecord(self.source, item, now) for item in self.client.items(since)]

    def normalize(self, raw: RawRecord) -> list[AnonRecord]:
        clean = strip_identity(raw.payload)
        url = str(clean.get("source_url") or "")
        if not url or not self.allowed(url):
            log.warning("輿情來源不在白名單，略過：%s", url or "（無網址）")
            return []
        collected = clean.get("collected_at")
        text = str(clean.get("text") or clean.get("message") or "")
        return [
            {
                "collected_at": (
                    datetime.fromisoformat(str(collected).replace("Z", "+00:00"))
                    if collected
                    else raw.fetched_at
                ),
                "source_class": str(clean.get("source_class") or "unknown"),
                "source_url": url,
                "village_code": clean.get("village_code"),
                "topic": str(clean.get("topic") or "未分類"),
                "sentiment": str(clean.get("sentiment") or "unclassified"),
                "intensity": Decimal(str(clean.get("intensity") or "0")),
                "summary": text[:SUMMARY_MAX],
            }
        ]
