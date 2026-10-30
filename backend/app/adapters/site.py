"""SiteAdapter（SPEC §5）：據點人員表單／模擬藍牙上傳，即時推送。

入口：POST /sites/{id}/measurements（api/sites.py）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.adapters.base import FetchContext, FhirResource, RawRecord
from app.fhir import builders


class SiteAdapter:
    source = "site"

    def schedule(self) -> str:
        return "realtime"

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]:
        return []  # 推送式：資料由 API 進來，不主動拉

    def normalize(self, raw: RawRecord) -> list[FhirResource]:
        """payload：{site_id, measured_at, device_id?, items}；
        items：{bp: [收縮, 舒張], weight, height, body_fat, glucose}（皆可省略）。"""
        p: dict[str, Any] = raw.payload
        at: datetime = p["measured_at"]
        site: str = p["site_id"]
        items: dict[str, Any] = p["items"]
        out: list[FhirResource] = []
        if items.get("bp") is not None:
            systolic, diastolic = items["bp"]
            out.append(
                builders.blood_pressure(
                    at, int(systolic), int(diastolic), site=site, device=p.get("device_id")
                )
            )
        if items.get("weight") is not None:
            out.append(builders.body_weight(at, float(items["weight"]), site=site))
        if items.get("height") is not None:
            out.append(builders.body_height(at, float(items["height"]), site=site))
        if items.get("body_fat") is not None:
            out.append(builders.body_fat(at, float(items["body_fat"]), site=site))
        if items.get("glucose") is not None:
            out.append(builders.glucose(at, float(items["glucose"]), site=site))
        return out
