"""C-8：輿情資料落地前去除身分（SPEC §4.4）。

移除：帳號、顯示名稱、頭像 URL 等身分欄位（整個欄位丟掉），
以及內文裡的 @提及、電話（手機／市話／+886）、Email（換成遮罩字樣）。
寧可多刪：@ 提及一路刪到空白或標點為止。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

IDENTITY_KEYS = frozenset(
    {
        "user",
        "users",
        "user_id",
        "username",
        "user_name",
        "account",
        "account_id",
        "handle",
        "screen_name",
        "display_name",
        "nickname",
        "nick",
        "uid",
        "owner",
        "email",
        "phone",
        "mobile",
        "mentions",
        "from",
        "sender",
    }
)
IDENTITY_PREFIXES = ("author", "reviewer", "avatar", "profile", "commenter", "poster")

EMAIL_MASK = "[Email 已移除]"
PHONE_MASK = "[電話已移除]"
MENTION_MASK = "@[已移除]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MOBILE = re.compile(r"(?<![\d+])(?:\+?886[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")
_LANDLINE = re.compile(
    r"(?<![\d+])(?:\+?886[\s-]?\(?\d{1,2}\)?|\(0\d{1,2}\)|0\d{1,2})[\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"
)
_MENTION = re.compile(r"(?<![\w@])@[^\s@，。！？、,.!?:：；;()（）\[\]]{1,30}")


def scrub_text(value: str) -> str:
    value = _EMAIL.sub(EMAIL_MASK, value)
    value = _MOBILE.sub(PHONE_MASK, value)
    value = _LANDLINE.sub(PHONE_MASK, value)
    # 遮罩字樣的「[」不在 _MENTION 字元集內，重複套用不會再被替換
    return _MENTION.sub(MENTION_MASK, value)


def _is_identity_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in IDENTITY_KEYS or lowered.startswith(IDENTITY_PREFIXES)


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _clean(v) for k, v in value.items() if not _is_identity_key(str(k))}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def strip_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = _clean(record)
    return cleaned
