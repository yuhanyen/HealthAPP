"""NhiAdapter（SPEC §5）：健保署健康存摺，每日 05:00 前同步；撤回即停止並刪除。

一期 Mock（fixtures/nhi_bundle_*.json）。
真實串接：健保署 SDK／數發部 MyData（TODO，對口：健保署資訊組）。
Patient 資源不落地——身分只存在 idn.users，FHIR 庫只存臨床資源。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.adapters.base import FetchContext, FhirResource, RawRecord, mock_notice
from app.adapters.mocks.nhi import MockNhiClient


class NhiClient(Protocol):
    def bundle_for(self, subject: str) -> dict[str, Any] | None: ...


CLINICAL_TYPES = {
    "Encounter",
    "Condition",
    "MedicationStatement",
    "Observation",
    "Immunization",
    "AllergyIntolerance",
}


class NhiAdapter:
    source = "nhi"

    def __init__(self, client: NhiClient | None = None) -> None:
        if client is None:
            mock_notice("NhiAdapter", "健保署 SDK／MyData 尚未串接")
            client = MockNhiClient()
        self.client = client

    def schedule(self) -> str:
        return "45 4 * * *"

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        if ctx is None or ctx.subject_ref is None:
            raise ValueError("NhiAdapter.fetch 需要 subject_ref")
        bundle = self.client.bundle_for(ctx.subject_ref)
        if bundle is None:
            return []
        return [RawRecord(source=self.source, payload=bundle, fetched_at=datetime.now(UTC))]

    def normalize(self, raw: RawRecord) -> list[FhirResource]:
        return [
            entry["resource"]
            for entry in raw.payload.get("entry", [])
            if entry.get("resource", {}).get("resourceType") in CLINICAL_TYPES
        ]
