"""資料來源 Adapter 介面（SPEC §5）。外部 API 一律經 Adapter；無金鑰時用 Mock 並在日誌標 [MOCK]。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"

FhirResource = dict[str, Any]
AnonRecord = dict[str, Any]

log = logging.getLogger("app.adapters")
_announced: set[str] = set()


def mock_notice(adapter: str, reason: str) -> None:
    """每個 Adapter 每個行程只提示一次 [MOCK]，避免洗版。"""
    if adapter not in _announced:
        _announced.add(adapter)
        log.warning("[MOCK] %s 使用 adapters/mocks（%s）", adapter, reason)


@dataclass
class SyncSummary:
    adapter: str
    users: int = 0
    written: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class RawRecord:
    source: str
    payload: dict[str, Any]
    fetched_at: datetime


@dataclass(frozen=True)
class FetchContext:
    user_id: uuid.UUID | None = None
    # 外部系統辨識本人的鍵（身分證字號雜湊）；真實串接時由 MyData／裝置綁定提供
    subject_ref: str | None = None
    until: datetime | None = None
    consent_scope: dict[str, list[str]] | None = None


class SourceAdapter(Protocol):
    source: str  # wearable | site | nhi | env | merchant | sentiment

    def fetch(self, since: datetime, ctx: FetchContext | None = None) -> list[RawRecord]: ...

    def normalize(self, raw: RawRecord) -> list[FhirResource] | list[AnonRecord]: ...

    def schedule(self) -> str: ...  # cron 表達式
