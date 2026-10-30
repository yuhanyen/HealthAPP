"""健保署健康存摺 Mock（真實：健保署 SDK／數發部 MyData，對口：健保署資訊組）。

讀 fixtures/nhi_bundle_*.json（FHIR Bundle，TW Core）；以 Patient.identifier（身分證字號）
雜湊後比對使用者。
"""

from __future__ import annotations

import json
from functools import cached_property
from typing import Any

from app.adapters.base import FIXTURES_DIR
from app.db.crypto import hash_national_id

NATIONAL_ID_SYSTEM = "http://www.moi.gov.tw"


def patient_national_id(bundle: dict[str, Any]) -> str | None:
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            for ident in resource.get("identifier", []):
                if ident.get("system") == NATIONAL_ID_SYSTEM:
                    return str(ident["value"])
    return None


class MockNhiClient:
    @cached_property
    def bundles_by_subject(self) -> dict[str, dict[str, Any]]:
        table: dict[str, dict[str, Any]] = {}
        for path in sorted(FIXTURES_DIR.glob("nhi_bundle_*.json")):
            bundle = json.loads(path.read_text(encoding="utf-8"))
            nid = patient_national_id(bundle)
            if nid:
                table[hash_national_id(nid)] = bundle
        return table

    def bundle_for(self, subject: str) -> dict[str, Any] | None:
        return self.bundles_by_subject.get(subject)
