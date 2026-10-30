"""MerchantRatingAdapter（SPEC §17.2）：商家公開評分。

設定 GOOGLE_PLACES_API_KEY 與 PLACES_MERCHANTS（JSON：{"M01": "place_id", ...}）時讀 Google Places
（rating、userRatingCount；未以真實金鑰驗證）；否則讀 fixtures/merchant_ratings.json（[MOCK]）。
只有公開的店家評分，不含任何評論者資料。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from app.adapters.base import FIXTURES_DIR, mock_notice
from app.config import get_settings

log = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places/{place_id}"


@dataclass(frozen=True)
class Rating:
    rating: Decimal
    count: int
    source: str


class RatingClient(Protocol):
    def ratings(self, merchant_ids: Sequence[str]) -> dict[str, Rating]: ...


class MockRatingClient:
    def ratings(self, merchant_ids: Sequence[str]) -> dict[str, Rating]:
        data = json.loads((FIXTURES_DIR / "merchant_ratings.json").read_text(encoding="utf-8"))
        return {
            mid: Rating(Decimal(str(row["rating"])), int(row["count"]), "mock")
            for mid, row in data.items()
            if mid in merchant_ids
        }


class GooglePlacesRatingClient:
    def __init__(self, api_key: str, places: dict[str, str]) -> None:
        self.api_key, self.places = api_key, places

    def ratings(self, merchant_ids: Sequence[str]) -> dict[str, Rating]:
        out: dict[str, Rating] = {}
        headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "rating,userRatingCount"}
        with httpx.Client(timeout=20, headers=headers) as client:
            for mid in merchant_ids:
                place_id = self.places.get(mid)
                if not place_id:
                    continue
                payload = client.get(PLACES_URL.format(place_id=place_id)).json()
                if payload.get("rating") is None:
                    continue
                out[mid] = Rating(
                    Decimal(str(round(float(payload["rating"]), 1))),
                    int(payload.get("userRatingCount") or 0),
                    "google_places",
                )
        return out


def default_rating_client() -> RatingClient:
    s = get_settings()
    if s.google_places_api_key and s.places_merchants:
        return GooglePlacesRatingClient(s.google_places_api_key, json.loads(s.places_merchants))
    mock_notice("MerchantRatingAdapter", "未設定 Google Places 金鑰或 PLACES_MERCHANTS")
    return MockRatingClient()
