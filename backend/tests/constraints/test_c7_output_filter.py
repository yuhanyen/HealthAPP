"""C-7：A1 輸出不得含疾病斷言詞（黑名單）；用詞停在健康促進。

SPEC §13-8 黑名單初始值：「您有」「您患有」「診斷為」「罹患」「確診」＋疾病名組合；
允許「偏高」「建議複測」「建議諮詢」。違規句子以固定的安全句替換，其餘句子原樣保留。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.output_filter import DEFAULT_BLACKLIST, StreamingFilter, filter_output
from app.i18n import t
from tests.conftest import Factory

pytestmark = pytest.mark.constraint

FALLBACK = t("output_filter.fallback")

BLOCKED = [
    "您有高血壓。",
    "您可能有糖尿病喔！",
    "你患有慢性腎臟病",
    "經檢查後診斷為心臟病。",
    "您罹患了第二型糖尿病",
    "根據紀錄，您已確診高血脂。",
    "看起來您是高血壓患者",
    "你得了痛風",
    "您　可能有　高血壓",  # 全形空白
    "您 有 中 風 的 風 險",  # 半形空白拆字
]

ALLOWED = [
    "血壓連續三天偏高，建議到據點複測。",
    "建議諮詢醫師，或到社區據點複測。",
    "您有一筆預約在明天早上 9:00。",
    "您有 120 點即將在月底到期。",
    "今天空品不好，建議室內伸展 15 分鐘。",
    "這兩週步數比前兩週少了約四成（依據：手機步數 10/15–10/28）。",
]


@pytest.mark.parametrize("sentence", BLOCKED)
def test_disease_assertions_are_blocked(sentence: str) -> None:
    result = filter_output(sentence)
    assert result.blocked
    assert result.violations
    assert FALLBACK in result.text
    for term in ("高血壓", "糖尿病", "腎臟病", "心臟病", "高血脂", "痛風", "中風", "中 風"):
        assert term not in result.text


@pytest.mark.parametrize("sentence", ALLOWED)
def test_health_promotion_wording_passes_unchanged(sentence: str) -> None:
    result = filter_output(sentence)
    assert not result.blocked
    assert result.text == sentence
    assert result.violations == []


def test_only_offending_sentences_are_replaced() -> None:
    text = "血壓連續三天偏高。您可能有高血壓。建議這幾天到據點複測一次。"
    result = filter_output(text)
    assert result.text == f"血壓連續三天偏高。{FALLBACK}建議這幾天到據點複測一次。"
    assert len(result.violations) == 1


def test_streaming_filter_catches_assertion_split_across_chunks() -> None:
    """A1 對話是串流：斷言被切在兩個片段之間也要擋下，且不會先送出半句。"""
    gate = StreamingFilter()
    out = gate.feed("血壓連續三天偏高。您")
    assert out == ["血壓連續三天偏高。"]  # 「您」還在緩衝，沒有送出
    out += gate.feed("可能有高")
    out += gate.feed("血壓，")
    out += gate.feed("要多注意。建議複測")
    out += gate.flush()
    text = "".join(out)
    assert "高血壓" not in text and FALLBACK in text
    assert text.endswith("建議複測") and gate.violations


def test_a1_runtime_output_is_filtered(factory: Factory) -> None:
    """不論 planner 是 Claude 或 mock，A1 送給民眾的文字都經 runtime 逐句過濾。"""
    from app.agents import a1, runtime

    user = factory.user()
    factory.consent(user.id, "wearable", ["steps"])

    class Talker:
        name = "talker"

        def step(self, **kwargs: Any) -> Any:
            yield runtime.Delta("您罹患")
            yield runtime.Delta("糖尿病。今天多走路。")
            yield runtime.StepResult([], [], "end_turn")

    rc = runtime.RunContext(
        agent=runtime.AGENTS["A1"],
        tool_context=lambda: a1.tool_context(user.id),
        context_text="",
        actor_id=f"A1:{user.id}",
        inject={"user_id": str(user.id), "consent_scope": a1.current_scope(user.id)},
    )
    events = list(runtime.run(rc, history=[], user_message="我怎麼了", planner=Talker()))
    text = "".join(e.text for e in events if isinstance(e, runtime.TextEvent))
    assert "糖尿病" not in text and text == f"{FALLBACK}今天多走路。"
    done = events[-1]
    assert isinstance(done, runtime.DoneEvent) and done.violations


def test_default_blacklist_contains_spec_prefixes() -> None:
    for prefix in ("您有", "您患有", "診斷為", "罹患", "確診"):
        assert prefix in DEFAULT_BLACKLIST.assertion_prefixes
    for allowed in ("偏高", "建議複測", "建議諮詢"):
        assert allowed not in DEFAULT_BLACKLIST.assertion_prefixes
