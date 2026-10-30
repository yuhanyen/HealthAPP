"""A2 規則式 planner（無 ANTHROPIC_API_KEY 時，[MOCK]）。與 Claude 走同一條 runtime。

依 SKILL A2「承辦提問」：判斷是否個人層級 → 結構化（對象 × 指標 × 時間 × 決策）→
委派 A3（數字，附查詢編號）與 A4（事件，附來源）→ 核對證據 → 合成「觀察／外部事件／可能原因／
建議／不確定之處」，最後一行固定「以上為分析建議，需承辦核定後執行。」
談參：多一步 briefing_pack；據點加開：cohort_analysis 熱區 → resource_sim。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select

from app.adapters.base import mock_notice
from app.agents.runtime import Delta, StepResult, ToolCall
from app.db.engines import DbRole, session_scope
from app.db.models_pub import Village
from app.i18n import t
from app.services import geo

INDIVIDUAL = re.compile(r"某位|這位|那位|阿伯|阿嬤|身分證|電話|地址|姓名|叫什麼|是誰|個人資料")
BRIEFING = re.compile(r"談參|質詢|議會|記者會|簡報|說明會|里民大會|開會")
RESOURCE = re.compile(r"加開|增設|巡迴|覆蓋|新據點|設點|據點位置")
CARE = re.compile(r"關懷|清單|代號")
ANALYSIS = re.compile(r"為什麼|上升|下降|原因|趨勢|分析|變化|比較|怎麼了|多少")
METRIC_WORDS = (
    ("bp_uncontrolled_rate", ("未控制",)),
    ("bp_high_rate", ("血壓偏高", "高血壓比例")),
    ("site_usage", ("使用率", "據點使用", "到據點")),
    ("measure_freq", ("量測頻率", "量測次數")),
    ("steps_avg", ("步數", "走路", "活動量")),
    ("redeem_rate", ("兌換",)),
)
SUPPORT = ("measure_freq", "site_usage")
OCCASIONS = ("議會", "記者會", "里民大會", "跨局處會議", "商家說明會")
ACCESS_TOPICS = ("據點開放", "排隊等候")


def source_label(source_class: str) -> str:
    try:
        return t(f"a2.source.{source_class}")
    except KeyError:
        return t("a2.source.unknown")


def villages() -> dict[str, str]:
    """里名稱 → 代碼（pub 非個資）。"""
    with session_scope(DbRole.ANON_READER) as s:
        return {name: code for code, name in s.execute(select(Village.code, Village.name))}


def structure(question: str) -> dict[str, Any]:
    """承辦原話 → 對象 × 指標 × 時間。"""
    names = villages()
    codes = [code for name, code in names.items() if name in question]
    primary = next(
        (m for m, words in METRIC_WORDS if any(w in question for w in words)),
        "bp_uncontrolled_rate",
    )
    weeks = 12
    if match := re.search(r"(\d+)\s*週", question):
        weeks = max(4, min(52, int(match.group(1))))
    elif "半年" in question:
        weeks = 26
    occasion = next((o for o in OCCASIONS if o in question), "業務會議")
    return {
        "village_codes": codes,
        "village_names": [n for n, c in names.items() if c in codes],
        "primary": primary,
        "metrics": [primary, *[m for m in SUPPORT if m != primary]],
        "weeks": weeks,
        "occasion": occasion,
    }


class MockA2Planner:
    name = "mock"

    def __init__(self) -> None:
        mock_notice("A2", "未設定 ANTHROPIC_API_KEY，A2 以規則拆題與合成")
        self.intent = "help"
        self.plan: dict[str, Any] = {}
        self.question = ""
        self.results: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self.meta: dict[str, Any] = {}

    # --- planner 介面 ---

    def step(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[Any]
    ) -> Iterator[Delta | StepResult]:
        last = messages[-1]["content"]
        if isinstance(last, str):
            self.question = last
            calls = self._first(last)
        else:
            self._collect(last)
            calls = self._next()
        if calls:
            content = [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
            ]
            yield StepResult(content, calls, "tool_use")
            return
        text = self._reply()
        for part in re.split(r"(?<=\n)", text):
            if part:
                yield Delta(part)
        yield StepResult([{"type": "text", "text": text}], [], "end_turn", {**self.meta})

    def _call(self, name: str, alias: str | None = None, **args: Any) -> ToolCall:
        call = ToolCall(f"mock_{uuid.uuid4().hex[:12]}", name, args)
        self._aliases[call.id] = alias or name
        return call

    def _collect(self, blocks: list[dict[str, Any]]) -> None:
        for block in blocks:
            name = self._aliases.get(block["tool_use_id"], "?")
            if block.get("is_error"):
                self.results[name] = {"status": "error", "message": block["content"]}
            else:
                self.results[name] = json.loads(block["content"]).get("data") or {}

    # --- 拆題 ---

    def _delegations(self) -> list[ToolCall]:
        p = self.plan
        return [
            self._call(
                "delegate_a3",
                question=self.question[:300],
                village_codes=p["village_codes"],
                metrics=p["metrics"],
                weeks=p["weeks"],
            ),
            self._call(
                "delegate_a4",
                question=self.question[:300],
                village_codes=p["village_codes"],
                days=min(120, p["weeks"] * 7),
            ),
        ]

    def _first(self, question: str) -> list[ToolCall]:
        if INDIVIDUAL.search(question):
            self.intent = "individual"
            return []
        self.plan = structure(question)
        if BRIEFING.search(question):
            self.intent = "briefing"
            return self._delegations()
        if RESOURCE.search(question):
            self.intent = "resource"
            return [self._call("cohort_analysis", metrics=[self.plan["primary"]], weeks=4)]
        if CARE.search(question):
            self.intent = "care"
            return []
        if ANALYSIS.search(question) or self.plan["village_codes"]:
            self.intent = "analysis"
            return self._delegations()
        return []

    def _next(self) -> list[ToolCall]:
        if self.intent == "briefing" and "briefing_pack" not in self.results:
            facts = self._facts()
            if facts:
                return [
                    self._call(
                        "briefing_pack",
                        topic=self.question[:80],
                        occasion=self.plan["occasion"],
                        facts=facts[:6],
                        stance=self._suggestion(),
                    )
                ]
        if self.intent == "resource" and "resource_sim" not in self.results:
            candidates = self._candidates()
            if candidates:
                return [self._call("resource_sim", candidate_sites=candidates, radius_km=0.8)]
        return []

    # --- 證據與合成 ---

    def _accepted(self, name: str) -> dict[str, Any] | None:
        result = self.results.get(name)
        return result if result and result.get("status") == "accepted" else None

    def _rising(self) -> list[str]:
        a3 = self._accepted("delegate_a3") or {}
        return [
            f["village_code"]
            for f in a3.get("findings", [])
            if f.get("village_code") and "metric" not in f
        ]

    def _access_events(self, villages: list[str]) -> list[dict[str, Any]]:
        a4 = self._accepted("delegate_a4") or {}
        return [
            e
            for e in a4.get("timeline", [])
            if e["topic"] in ACCESS_TOPICS
            and (not villages or e["village_code"] in (*villages, None))
        ]

    def _measure_down(self, village: str) -> bool:
        a3 = self._accepted("delegate_a3") or {}
        return any(
            f.get("metric") in SUPPORT
            and f.get("direction") == "down"
            and f.get("village_code") == village
            for f in a3.get("findings", [])
        )

    def _explained(self) -> tuple[list[str], list[str]]:
        """（數字與在地事件吻合的里, 只有數字變化、找不到對應事件的里）——逐里對照，不以偏概全。"""
        supported: list[str] = []
        unexplained: list[str] = []
        for village in self._rising():
            local = [e for e in self._access_events([village]) if e["village_code"] == village]
            (supported if local and self._measure_down(village) else unexplained).append(village)
        return supported, unexplained

    def _cause(self) -> str:
        supported, unexplained = self._explained()
        parts = []
        if supported:
            n = len([e for e in self._access_events(supported) if e["village_code"] in supported])
            parts.append(t("a2.cause.access", villages="、".join(supported), n=n))
        if unexplained:
            parts.append(t("a2.cause.no_events", villages="、".join(unexplained)))
        if not parts and self._access_events([]):
            parts.append(t("a2.cause.mismatch"))
        return "\n".join(parts) or t("a2.cause.unclear")

    def _suggestion(self) -> str:
        supported, unexplained = self._explained()
        parts = []
        if supported:
            parts.append(t("a2.suggest.access", villages="、".join(supported)))
        if unexplained:
            parts.append(t("a2.suggest.investigate", villages="、".join(unexplained)))
        return "\n".join(parts) or t("a2.suggest.monitor")

    def _facts(self) -> list[dict[str, str]]:
        a3 = self._accepted("delegate_a3") or {}
        a4 = self._accepted("delegate_a4") or {}
        facts = [
            {"claim": f["claim"], "evidence": f["evidence"]} for f in a3.get("findings", [])[:4]
        ]
        facts += [
            {"claim": f"{e['date']} {e['event'][:60]}", "evidence": e["source_url"]}
            for e in a4.get("timeline", [])[-2:]
        ]
        return facts

    def _candidates(self) -> list[dict[str, Any]]:
        spots: dict[str, Any] = self.results.get("cohort_analysis", {}).get("metrics", {})
        first: dict[str, Any] = next(iter(spots.values()), {})
        features = first.get("hotspots", {}).get("features", [])
        ranked = sorted(
            (
                f
                for f in features
                if not f["properties"]["masked"] and f["properties"]["value"] is not None
            ),
            key=lambda f: f["properties"]["value"],
            reverse=self.plan["primary"] != "steps_avg",
        )[:2]
        out = []
        for feature in ranked:
            point = geo.centroid(feature["geometry"])
            if point:
                out.append(
                    {
                        "name": t("a2.candidate", village=feature["properties"]["name"]),
                        "lat": point[0],
                        "lng": point[1],
                    }
                )
        return out

    def _reply(self) -> str:
        handler = getattr(self, f"_say_{self.intent}", self._say_help)
        text: str = handler()
        return text

    def _say_help(self) -> str:
        return t("a2.chat.help")

    def _say_individual(self) -> str:
        return t("a2.chat.individual")

    def _say_care(self) -> str:
        return t("a2.chat.care")

    def _rejections(self) -> list[str]:
        out = []
        for name, label in (("delegate_a3", "A3"), ("delegate_a4", "A4")):
            result = self.results.get(name)
            if result is None:
                continue
            if result.get("status") == "error":
                out.append(t("a2.rejected", agent=label, problems=result.get("message", "")))
            elif result.get("status") != "accepted":
                out.append(
                    t("a2.rejected", agent=label, problems="；".join(result.get("problems", [])))
                )
        return out

    def _say_analysis(self) -> str:
        p = self.plan
        a3 = self._accepted("delegate_a3") or {}
        a4 = self._accepted("delegate_a4") or {}
        self.meta = {
            "report": a3.get("report"),
            "charts": a3.get("charts", []),
            "queries": a3.get("queries", []),
        }
        target = "、".join(p["village_names"]) or t("a2.district")
        observations = [
            f"・{f['claim']}（依據：{f['evidence']}）" for f in a3.get("findings", [])
        ] or [t("a2.no_data")]
        events = [
            f"・{e['date']}｜{e['event'][:50]}｜{source_label(e['source_class'])}"
            f"（{e['source_url']}）"
            for e in a4.get("timeline", [])[-6:]
        ] or [t("a2.no_events")]
        if a4.get("trend_summary"):
            events.append(f"・{a4['trend_summary']}")
        if a4.get("disease_note"):
            events.append(f"・{a4['disease_note']}")
        lines = [
            t(
                "a2.section.question",
                target=target,
                metric=t(f"metric.{p['primary']}"),
                weeks=p["weeks"],
            ),
            t("a2.section.observation"),
            *observations,
            t("a2.section.events"),
            *events,
            t("a2.section.cause"),
            self._cause(),
            t("a2.section.suggestion"),
            self._suggestion(),
            t("a2.section.uncertain"),
            t(
                "a2.uncertain",
                cutoff=a3.get("data_cutoff", "—"),
                k=a3.get("k", 5),
                masked=a3.get("masked_rows", 0),
            ),
            *self._rejections(),
            t("a2.approval"),
        ]
        return "\n".join(lines)

    def _say_briefing(self) -> str:
        briefing = self.results.get("briefing_pack")
        if not briefing or "markdown" not in briefing:
            return self._say_analysis()
        a3 = self._accepted("delegate_a3") or {}
        self.meta = {"report": a3.get("report"), "briefing": True}
        return "\n".join([briefing["markdown"], "", *self._rejections(), t("a2.briefing_draft")])

    def _say_resource(self) -> str:
        sim = self.results.get("resource_sim")
        if not sim or "coverage_before" not in sim:
            return t("a2.no_data")
        return "\n".join(
            [
                t(
                    "a2.resource",
                    before=round(sim["coverage_before"] * 100, 1),
                    after=round(sim["coverage_after"] * 100, 1),
                    villages="、".join(sim.get("newly_covered", [])) or t("a4.none"),
                ),
                t("a2.assumptions", items="；".join(sim.get("assumptions", []))),
                t("a2.approval"),
            ]
        )
