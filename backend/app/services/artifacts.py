"""分析產物（圖表 SVG、報告 HTML、沙箱輸出）存放與讀取。只含去識別化的聚合結果。"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from app.config import PROJECT_ROOT

ARTIFACT_DIR = PROJECT_ROOT / "backend" / "var" / "artifacts"
_NAME = re.compile(r"^A-\d{8}-[0-9a-f]{8}\.(svg|html|csv|json|txt|png)$")
MEDIA = {
    "svg": "image/svg+xml",
    "html": "text/html; charset=utf-8",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "png": "image/png",
}


def save(content: bytes | str, ext: str) -> str:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"A-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4)}.{ext}"
    data = content.encode("utf-8") if isinstance(content, str) else content
    (ARTIFACT_DIR / name).write_bytes(data)
    return name


def path_of(name: str) -> Path | None:
    if not _NAME.match(name):
        return None
    path = ARTIFACT_DIR / name
    return path if path.exists() else None


def url_of(name: str) -> str:
    return f"/api/v1/agency/artifacts/{name}"
