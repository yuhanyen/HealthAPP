"""WearableAdapter（SPEC §5）：每小時步數與心率。

一期 Mock；真實為 HealthKit／Health Connect（二期）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.adapters.base import FetchContext, FhirResource, RawRecord, mock_notice
from app.adapters.mocks.wearable import HourRecord, MockWearableClient
from app.fhir import builders


class WearableClient(Protocol):
    def hourly(self, subject: str, start: datetime, end: datetime) -> list[HourRecord]: ...


class WearableAdapter:
    source = "wearable"

    def __init__(self, client: WearableClient | None = None) -> None:
        if client is None:
            mock_notice("WearableAdapter", "HealthKit／Health Connect 於二期串接")
            client = MockWearableClient()
        self.client = client

    def schedule(self) -> str:
        return "5 * * * *"

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        if ctx is None or ctx.subject_ref is None:
            raise ValueError("WearableAdapter.fetch 需要 subject_ref")
        until = ctx.until or datetime.now(UTC)
        now = datetime.now(UTC)
        return [
            RawRecord(source=self.source, payload=record, fetched_at=now)
            for record in self.client.hourly(ctx.subject_ref, since, until)
        ]

    def normalize(self, raw: RawRecord) -> list[FhirResource]:
        hour: datetime = raw.payload["hour"]
        resources = [builders.steps_hourly(hour, int(raw.payload["steps"]))]
        if raw.payload.get("heart_rate"):
            resources.append(builders.heart_rate(hour, int(raw.payload["heart_rate"])))
        return resources
