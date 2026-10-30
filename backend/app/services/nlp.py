"""輿情 NLP（SPEC §7.2 nlp_sentiment／nlp_topic／nlp_entity）。

一期預設以規則（關鍵字）分類；設定 ANTHROPIC_API_KEY 時改由 LLM 批次分類（app/llm）。
輸入文字已在落地前去識別（C-8），這裡只處理內容。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

log = logging.getLogger(__name__)

NEGATIVE = (
    "排隊",
    "等太久",
    "等了",
    "很久",
    "沒開",
    "沒人接",
    "危險",
    "壞了",
    "抱怨",
    "放棄",
    "很差",
    "不方便",
    "太遠",
    "擁擠",
    "關閉",
    "取消",
    "不滿",
    "生氣",
)
POSITIVE = ("親切", "推薦", "方便", "很好", "謝謝", "感謝", "滿意", "乾淨", "專業", "成長")
TOPICS: dict[str, tuple[str, ...]] = {
    "排隊等候": ("排隊", "等太久", "等了", "很久", "預約制", "人很多", "擁擠"),
    "據點開放": ("沒開", "開放", "週三", "時段", "關閉", "休息", "沒人接"),
    "環境設施": ("路燈", "步道", "危險", "壞了", "設備", "場地"),
    "服務態度": ("志工", "親切", "態度", "服務"),
    "健康活動": ("量血壓", "義診", "運動", "活動", "講座", "篩檢"),
}


def classify_sentiment(text: str) -> tuple[str, float]:
    neg = sum(text.count(w) for w in NEGATIVE)
    pos = sum(text.count(w) for w in POSITIVE)
    if neg > pos:
        return "negative", min(1.0, 0.4 + 0.2 * (neg - pos))
    if pos > neg:
        return "positive", min(1.0, 0.4 + 0.2 * (pos - neg))
    return "neutral", 0.1


def classify_topic(text: str) -> str:
    scores = {topic: sum(text.count(w) for w in words) for topic, words in TOPICS.items()}
    best, score = max(scores.items(), key=lambda kv: kv[1])
    return best if score > 0 else "其他"


def extract_entities(text: str, gazetteer: Sequence[str]) -> list[str]:
    return [name for name in gazetteer if name and name in text]


def batch(texts: Sequence[str], *, gazetteer: Sequence[str] = ()) -> list[dict[str, Any]]:
    from app.llm.client import llm_available, nlp_batch

    if llm_available():
        try:
            return nlp_batch(list(texts), list(gazetteer))
        except Exception:
            log.exception("LLM 分類失敗，改用規則分類")
    out = []
    for text in texts:
        sentiment, intensity = classify_sentiment(text)
        out.append(
            {
                "sentiment": sentiment,
                "intensity": round(intensity, 2),
                "topic": classify_topic(text),
                "entities": extract_entities(text, gazetteer),
            }
        )
    return out
