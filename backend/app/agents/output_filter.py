"""C-7：A1 輸出過濾。疾病斷言（斷言詞＋疾病名）整句換成安全句，其餘原樣保留。

黑名單初始值依 SPEC §13-8；之後可由 sys.config 的 output_blacklist 覆寫。
允許「偏高」「建議複測」「建議諮詢」等健康促進用語——它們不是斷言詞，本來就不會命中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property

from app.i18n import t

_WHITESPACE = re.compile(r"[\s　]+")
_SENTENCE_END = re.compile(r"(?<=[。！？!?\n])")
# 斷言詞與疾病名之間允許的字數；不跨越標點，避免把兩個子句硬湊在一起
_GAP = r"[^。！？!?，,；;、\n]{0,6}?"


@dataclass(frozen=True)
class Blacklist:
    assertion_prefixes: tuple[str, ...]
    diseases: tuple[str, ...]
    patient_suffixes: tuple[str, ...] = ("患者", "病人", "病患")

    @cached_property
    def _assertion(self) -> re.Pattern[str]:
        prefixes = "|".join(sorted(map(re.escape, self.assertion_prefixes), key=len, reverse=True))
        diseases = "|".join(sorted(map(re.escape, self.diseases), key=len, reverse=True))
        suffixes = "|".join(map(re.escape, self.patient_suffixes))
        return re.compile(
            rf"(?:(?:{prefixes}){_GAP}(?:{diseases}))|(?:(?:{diseases})(?:{suffixes}))"
        )

    def find(self, sentence: str) -> list[str]:
        compact = _WHITESPACE.sub("", sentence)
        return [m.group(0) for m in self._assertion.finditer(compact)]


DEFAULT_BLACKLIST = Blacklist(
    assertion_prefixes=(
        "您有",
        "你有",
        "您患有",
        "你患有",
        "患有",
        "您可能有",
        "你可能有",
        "可能有",
        "診斷為",
        "診斷出",
        "被診斷",
        "罹患",
        "確診",
        "得了",
        "疑似",
    ),
    diseases=(
        "高血壓",
        "低血壓",
        "糖尿病",
        "高血脂",
        "高血糖",
        "心臟病",
        "冠心病",
        "心衰竭",
        "心血管疾病",
        "心律不整",
        "中風",
        "腦中風",
        "腎臟病",
        "腎病",
        "慢性腎病",
        "洗腎",
        "肝炎",
        "脂肪肝",
        "肝硬化",
        "癌症",
        "腫瘤",
        "失智症",
        "失智",
        "憂鬱症",
        "焦慮症",
        "痛風",
        "骨質疏鬆",
        "氣喘",
        "慢性阻塞性肺病",
        "肺炎",
        "甲狀腺",
        "貧血",
        "代謝症候群",
    ),
)


@dataclass(frozen=True)
class FilterResult:
    text: str
    violations: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.violations)


class StreamingFilter:
    """串流版：逐句放行。句子完整（遇到句號、問號、換行）才過濾並送出，半句先留在緩衝。"""

    def __init__(self, blacklist: Blacklist = DEFAULT_BLACKLIST) -> None:
        self.blacklist = blacklist
        self.violations: list[str] = []
        self._buffer = ""
        self._last = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        *complete, self._buffer = _SENTENCE_END.split(self._buffer)
        return self._gate(complete)

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer, ""
        return self._gate([rest])

    def _gate(self, sentences: list[str]) -> list[str]:
        out = []
        for sentence in sentences:
            if not sentence:
                continue
            result = filter_output(sentence, self.blacklist)
            self.violations.extend(result.violations)
            if result.blocked and result.text == self._last:
                continue  # 連續違規句只放一次安全句
            out.append(result.text)
            self._last = result.text
        return out


def filter_output(text: str, blacklist: Blacklist = DEFAULT_BLACKLIST) -> FilterResult:
    fallback = t("output_filter.fallback")
    out: list[str] = []
    violations: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if not sentence:
            continue
        hits = blacklist.find(sentence)
        if not hits:
            out.append(sentence)
            continue
        violations.extend(hits)
        if not out or out[-1] != fallback:  # 連續違規句只放一次安全句
            out.append(fallback)
    return FilterResult(text="".join(out), violations=violations)
