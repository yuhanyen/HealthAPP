"""使用者可見文字一律來自 i18n/zh-TW.json（CLAUDE.md 程式慣例）。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import get_settings


@lru_cache
def _catalog(lang: str = "zh-TW") -> dict[str, Any]:
    path = get_settings().i18n_dir / f"{lang}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def catalog() -> dict[str, Any]:
    """整份文案（給前端用；只含介面文字，沒有任何資料）。"""
    return _catalog()


def _lookup(key: str) -> Any:
    node: Any = _catalog()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"i18n 缺少鍵：{key}")
        node = node[part]
    return node


def t(key: str, **params: object) -> str:
    value = _lookup(key)
    if not isinstance(value, str):
        raise TypeError(f"i18n 鍵 {key} 不是字串")
    return value.format(**params) if params else value


def t_list(key: str) -> list[str]:
    value = _lookup(key)
    if not isinstance(value, list):
        raise TypeError(f"i18n 鍵 {key} 不是清單")
    return [str(v) for v in value]
