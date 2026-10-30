"""各資料來源可授權的資料項目（consent scope 的 read 清單只能從這裡挑）。"""

from __future__ import annotations

from app.db.models_idn import CONSENT_SOURCES

SOURCE_ITEMS: dict[str, tuple[str, ...]] = {
    "wearable": ("steps", "heart_rate", "sleep"),
    "site": ("bp", "weight", "height", "body_fat", "glucose"),
    "nhi": ("visits", "conditions", "medications", "labs", "immunizations", "allergies"),
    "env": ("district",),
    "merchant": ("redemptions",),
}

# 撤回後要刪除的 idn.fhir_resources.source 值；env／merchant 沒有落地的健康資料
FHIR_SOURCE_OF: dict[str, str] = {"wearable": "wearable", "site": "site", "nhi": "nhi"}

assert set(SOURCE_ITEMS) == set(CONSENT_SOURCES)
