"""C-8：輿情採集於落地前去除帳號、名稱、頭像（SPEC §4.4 另含 @提及、電話、Email）。

驗證 strip_identity() 本身，以及 SentimentAdapter.normalize() 產出的 AnonRecord 已乾淨。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.adapters.base import RawRecord
from app.adapters.sentiment import SentimentAdapter
from app.db.engines import DbRole, session_scope
from app.deidentify.strip_identity import scrub_text, strip_identity

pytestmark = pytest.mark.constraint

GOOGLE_REVIEW: dict[str, Any] = {
    "author_name": "陳小明",
    "author_url": "https://www.google.com/maps/contrib/1234567890",
    "profile_photo_url": "https://lh3.googleusercontent.com/a/ACg8ocK",
    "rating": 1,
    "language": "zh-TW",
    "relative_time_description": "2 天前",
    "text": (
        "北區衛生所量血壓排隊好久 @林大華 你也是吧？"
        "有問題打 0912-345-678 或寫信 abc.def@example.com"
    ),
    "time": 1761700000,
}

IDENTITY_VALUES = (
    "陳小明",
    "contrib/1234567890",
    "googleusercontent",
    "林大華",
    "0912-345-678",
    "abc.def@example.com",
)


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def test_identity_fields_are_removed() -> None:
    cleaned = strip_identity(GOOGLE_REVIEW)
    for key in ("author_name", "author_url", "profile_photo_url"):
        assert key not in cleaned
    for value in IDENTITY_VALUES:
        assert value not in _dump(cleaned)


def test_content_and_non_identity_fields_are_kept() -> None:
    cleaned = strip_identity(GOOGLE_REVIEW)
    assert cleaned["rating"] == 1
    assert cleaned["language"] == "zh-TW"
    assert "北區衛生所量血壓排隊好久" in cleaned["text"]


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("聯絡 0912345678", "0912345678"),
        ("手機 +886 912 345 678", "912 345 678"),
        ("市話 (03)571-2121 轉 3", "571-2121"),
        ("市話 03-5712121", "5712121"),
        ("寄到 someone+tag@mail.example.org", "someone+tag@mail.example.org"),
        ("@王小美 @john_doe 你們看", "王小美"),
        ("@王小美 @john_doe 你們看", "john_doe"),
    ],
)
def test_scrub_text_removes_contact_details(raw: str, leak: str) -> None:
    assert leak not in scrub_text(raw)


def test_dates_and_numbers_are_not_mistaken_for_phones() -> None:
    raw = "2026-10-29 量了 3 次，平均 142/91，排了 45 分鐘"
    assert scrub_text(raw) == raw


def test_nested_structures_are_cleaned() -> None:
    post = {
        "post": {
            "user": {"name": "張三", "avatar": "https://cdn.example/a.png"},
            "message": "里長 @李四 說下週有義診",
            "comments": [
                {"author": "王五", "author_id": "u-889", "text": "電話是 02-2345-6789 嗎？"},
                {"screen_name": "neighbor88", "text": "好"},
            ],
        }
    }
    dumped = _dump(strip_identity(post))
    for leak in ("張三", "a.png", "李四", "王五", "u-889", "2345-6789", "neighbor88"):
        assert leak not in dumped
    assert "下週有義診" in dumped


def test_sentiment_adapter_strips_before_landing() -> None:
    adapter = SentimentAdapter(whitelist=["https://maps.google.com/*"])
    raw = RawRecord(
        source="sentiment",
        payload={
            **GOOGLE_REVIEW,
            "source_class": "google_places",
            "source_url": "https://maps.google.com/?cid=42",
            "village_code": "N01",
        },
        fetched_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    records = adapter.normalize(raw)

    assert len(records) == 1
    record = records[0]
    assert set(record) <= {
        "collected_at",
        "source_class",
        "source_url",
        "village_code",
        "topic",
        "sentiment",
        "intensity",
        "summary",
    }
    assert record["source_url"] == "https://maps.google.com/?cid=42"
    for value in IDENTITY_VALUES:
        assert value not in _dump(record)


def test_sentiment_adapter_rejects_non_whitelisted_sources() -> None:
    adapter = SentimentAdapter(whitelist=["https://maps.google.com/*"])
    raw = RawRecord(
        source="sentiment",
        payload={"text": "x", "source_class": "forum", "source_url": "https://evil.example/t/1"},
        fetched_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    assert adapter.normalize(raw) == []


def test_mock_feed_comes_out_clean() -> None:
    adapter = SentimentAdapter(whitelist=["*"])
    records = [
        r for raw in adapter.fetch(datetime(2000, 1, 1, tzinfo=UTC)) for r in adapter.normalize(raw)
    ]
    assert records, "fixtures/sentiment_mock.json 應有資料"
    dumped = _dump([{k: str(v) for k, v in r.items()} for r in records])
    assert "@" not in dumped.replace("@[", "")  # 只允許遮罩後的 @[已移除]
    assert "author" not in dumped


def test_anon_sentiment_table_has_no_identity_columns() -> None:
    with session_scope(DbRole.MIGRATOR) as s:
        columns = set(
            s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'anon' AND table_name = 'sentiment_items'"
                )
            ).scalars()
        )
    assert not columns & {
        "author",
        "author_name",
        "account",
        "display_name",
        "avatar_url",
        "user_id",
    }
