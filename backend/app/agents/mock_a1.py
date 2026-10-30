"""A1 規則式 planner（無 ANTHROPIC_API_KEY 時，[MOCK]）。

與 Claude 走同一條 runtime：一樣經 registry 呼叫工具、一樣產生確認卡片、一樣過 C-7 過濾與稽核。
差別只在「決定呼叫哪些工具、怎麼說」是關鍵字規則：
    使用者訊息 → 意圖 → 第一步：工具呼叫 → 第二步：依工具結果組回覆
上一輪提出的方案（時段、優惠）存在對話紀錄的 meta，使用者回「好」「第二個」時據此執行。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

from app.adapters.base import mock_notice
from app.agents.runtime import Delta, StepResult, ToolCall
from app.i18n import t
from app.tools._common import md

SOURCE_WORDS = {
    "nhi": ("健保", "就醫", "健康存摺", "用藥", "看病"),
    "wearable": ("穿戴", "手機", "步數", "手環", "手錶"),
    "site": ("據點", "量測", "量血壓"),
    "env": ("天氣", "空品", "行政區", "所在地"),
    "merchant": ("商家", "兌換紀錄"),
}
ORDINALS = (("第一", "1", "①", "一"), ("第二", "2", "②", "二"), ("第三", "3", "③", "三"))
AFFIRM = re.compile(r"^(好|可以|要|麻煩|就這|預約吧|兌換吧|報名吧|ok|OK|對|嗯)|第[一二三123]|[①②③]")
DECLINE = re.compile(r"不用|不要|算了|先不|暫時不")
INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("revoke", re.compile(r"撤回|取消授權|不想授權|停止(分享|授權)|收回授權")),
    ("consent", re.compile(r"授權|誰看得到|隱私|個資")),
    (
        "events",
        re.compile(
            r"講座|課程|報名|社區活動|近日活動|里民|活動.{0,4}(附近|這週|週末|最近|有哪些|有什麼|推薦)|(附近|這週|週末|最近|有什麼|有哪些).{0,6}活動"
        ),
    ),
    ("booking", re.compile(r"預約|複測|時段|量血壓")),
    (
        "dining",
        re.compile(
            r"餐廳|吃什麼|吃飯|美食|小吃|早餐|午餐|晚餐|便當|評分|評價|推薦.{0,4}(店|商家|餐)"
        ),
    ),
    ("redeem", re.compile(r"兌換|商家|優惠|換什麼|可以換")),
    ("points", re.compile(r"點數|幾點|餘額|到期")),
    ("exercise", re.compile(r"運動|走路|散步|處方|活動|伸展")),
    ("stats", re.compile(r"統計|這個月|本月|上個月|這週|一週|趨勢|平均|月報|週報|儀表板")),
    ("status", re.compile(r"變差|狀況|健康|報告|今天|血壓|步數|怎麼樣|好不好|身體")),
)


def _ordinal(text: str) -> int:
    for index, words in enumerate(ORDINALS):
        if any(w in text for w in words[:3]):
            return index
    return 0


def _source_of(text: str) -> str | None:
    return next((s for s, words in SOURCE_WORDS.items() if any(w in text for w in words)), None)


def _slot(slot: str) -> str:
    from app.agents.a1 import slot_text

    return slot_text(slot)


class MockA1Planner:
    name = "mock"

    def __init__(self, last_meta: dict[str, Any] | None = None) -> None:
        mock_notice("A1", "未設定 ANTHROPIC_API_KEY，A1 對話改用規則式")
        self.last_meta = last_meta or {}
        self.intent = "help"
        self.meta: dict[str, Any] = {}
        self._aliases: dict[str, str] = {}  # tool_use_id → 結果名稱（同一工具呼叫兩次時區分）

    # --- planner 介面 ---

    def step(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], tools: list[Any]
    ) -> Iterator[Delta | StepResult]:
        last = messages[-1]["content"]
        if isinstance(last, str):
            calls = self._plan(last)
            if calls:
                content = [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                    for c in calls
                ]
                yield StepResult(content, calls, "tool_use")
                return
            text = self._reply({})
        else:
            text = self._reply(self._results(messages))
        for sentence in re.split(r"(?<=[。？！\n])", text):
            if sentence:
                yield Delta(sentence)
        yield StepResult(
            [{"type": "text", "text": text}], [], "end_turn", {"topic": self.intent, **self.meta}
        )

    # --- 第一步：意圖 → 工具 ---

    def _call(self, name: str, alias: str | None = None, **args: Any) -> ToolCall:
        call = ToolCall(f"mock_{uuid.uuid4().hex[:12]}", name, args)
        self._aliases[call.id] = alias or name
        return call

    def _plan(self, text: str) -> list[ToolCall]:
        proposal = self.last_meta.get("proposal")
        if proposal and DECLINE.search(text):
            self.intent = "decline"
            return []
        if proposal and AFFIRM.search(text.strip()):
            index = _ordinal(text)
            if proposal["kind"] == "booking":
                slots = proposal["slots"]
                self.intent = "book"
                return [
                    self._call(
                        "site_booking",
                        mode="book",
                        site_id=proposal["site_id"],
                        slot_at=slots[min(index, len(slots) - 1)],
                    )
                ]
            if proposal["kind"] == "redeem":
                offers = proposal["offers"]
                self.intent = "redeem_confirm"
                offer = offers[min(index, len(offers) - 1)]
                return [self._call("points_engine", action="redeem", offer_id=offer["offer_id"])]
            if proposal["kind"] == "event":
                found = proposal["events"]
                self.intent = "event_confirm"
                event = found[min(index, len(found) - 1)]
                return [self._call("event_register", event_id=event["event_id"])]
        self.intent = next((name for name, rx in INTENTS if rx.search(text)), "help")
        source = _source_of(text)
        match self.intent:
            case "revoke":
                if source:
                    return [self._call("consent_mgmt", action="revoke", source=source)]
                return [self._call("consent_mgmt", action="get")]
            case "consent":
                calls = [self._call("consent_mgmt", action="get")]
                if source:
                    calls.append(
                        self._call(
                            "consent_mgmt", alias="consent_explain", action="explain", source=source
                        )
                    )
                return calls
            case "booking":
                return [self._call("site_booking", mode="query", days=2)]
            case "redeem":
                return [
                    self._call("points_engine", action="status"),
                    self._call("merchant_lookup"),
                ]
            case "points":
                return [self._call("points_engine", action="status")]
            case "exercise":
                return [self._call("exercise_rx")]
            case "events":
                return [self._call("event_recommend", days=14, limit=3)]
            case "dining":
                return [self._call("merchant_recommend", category="dining", limit=3)]
            case "stats":
                period = "week" if re.search(r"這週|一週|週報", text) else "month"
                return [self._call("health_summary", period=period)]
            case "status":
                return [
                    self._call("daily_report"),
                    self._call("risk_score"),
                    self._call("site_booking", mode="query", days=2),
                ]
        return []

    # --- 第二步：工具結果 → 回覆 ---

    def _results(self, messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for block in messages[-1]["content"]:
            name = self._aliases.get(block["tool_use_id"], "?")
            if block.get("is_error"):
                out[name] = {"status": "error", "message": block["content"]}
            else:
                out[name] = json.loads(block["content"])
        return out

    def _reply(self, results: dict[str, dict[str, Any]]) -> str:
        """工具結果 → 回覆。主要工具失敗（例如不在授權範圍）就照實說明原因。"""
        errors = [r["message"] for r in results.values() if r.get("status") == "error"]
        data = {k: r.get("data") or r for k, r in results.items() if r.get("status") != "error"}
        handler = getattr(self, f"_say_{self.intent}", self._say_help)
        text: str = handler(data)
        if not text and errors:
            return t("a1.chat.tool_error", reason=errors[0])
        return text or t("a1.chat.help")

    def _say_help(self, data: dict[str, Any]) -> str:
        return t("a1.chat.help")

    def _say_decline(self, data: dict[str, Any]) -> str:
        return t("a1.chat.decline")

    def _say_status(self, data: dict[str, Any]) -> str:
        report = data.get("daily_report")
        if not report:
            return ""
        parts = [report["status_sentence"], *[m["text"] + "。" for m in report["metrics"]]]
        level = (data.get("risk_score") or {}).get("level", "L0")
        slots = data.get("site_booking")
        if level in ("L2", "L3") and slots and slots.get("slots"):
            parts.append(self._propose_booking(slots))
        return "\n".join(parts)

    def _propose_booking(self, slots: dict[str, Any]) -> str:
        top = slots["slots"][:3]
        self.meta["proposal"] = {
            "kind": "booking",
            "site_id": slots["site_id"],
            "site_name": slots["site_name"],
            "slots": top,
        }
        options = "／".join(_slot(s) for s in top)
        return t("a1.chat.propose_booking", site=slots["site_name"], options=options)

    def _say_booking(self, data: dict[str, Any]) -> str:
        slots = data.get("site_booking")
        if not slots:
            return ""
        if not slots["slots"]:
            return t("a1.chat.no_slots", site=slots["site_name"])
        return self._propose_booking(slots)

    def _say_book(self, data: dict[str, Any]) -> str:
        pending = data.get("site_booking")
        return t("a1.chat.confirm_card", summary=pending["summary"]) if pending else ""

    def _say_redeem_confirm(self, data: dict[str, Any]) -> str:
        pending = data.get("points_engine")
        return t("a1.chat.confirm_card", summary=pending["summary"]) if pending else ""

    def _say_points(self, data: dict[str, Any]) -> str:
        status = data.get("points_engine")
        if not status:
            return ""
        text = t("a1.chat.points", balance=status["balance"])
        if status["expiring"]:
            soon = status["expiring"][0]
            text += t(
                "a1.chat.points_expiring",
                points=soon["points"],
                date=md(date.fromisoformat(soon["expires_on"])),
            )
        return text

    def _say_redeem(self, data: dict[str, Any]) -> str:
        balance = (data.get("points_engine") or {}).get("balance", 0)
        offers = [
            {
                "offer_id": o["offer_id"],
                "title": o["title"],
                "merchant": m["name"],
                "cost": o["points_cost"],
            }
            for m in (data.get("merchant_lookup") or {}).get("merchants", [])
            for o in m["offers"]
            if o["points_cost"] <= balance
        ][:3]
        if not offers:
            return t("a1.chat.no_offers", balance=balance)
        self.meta["proposal"] = {"kind": "redeem", "offers": offers}
        options = "／".join(
            t("a1.chat.offer", merchant=o["merchant"], title=o["title"], cost=o["cost"])
            for o in offers
        )
        return t("a1.chat.propose_redeem", balance=balance, options=options)

    def _say_exercise(self, data: dict[str, Any]) -> str:
        rx = data.get("exercise_rx")
        if not rx:
            return ""
        basis = "、".join(rx["basis"]) or t("a1.report.basis_steps")
        return "\n".join(
            [
                t("a1.chat.exercise", plan=rx["plan"], goal=f"{rx['step_goal']:,}", basis=basis),
                t("a1.chat.contra", items="；".join(rx["contraindications"])),
            ]
        )

    def _say_events(self, data: dict[str, Any]) -> str:
        found = (data.get("event_recommend") or {}).get("events")
        if found is None:
            return ""
        open_events = [e for e in found if e.get("registration") is None][:3]
        if not open_events:
            return t("a1.chat.no_events")
        self.meta["proposal"] = {
            "kind": "event",
            "events": [{"event_id": e["event_id"], "title": e["title"]} for e in open_events],
        }
        lines = [
            t(
                "a1.chat.event_option",
                n=i + 1,
                title=e["title"],
                when=e["when"],
                venue=e["venue"],
                reason="；".join(e["reasons"][:2]),
            )
            for i, e in enumerate(open_events)
        ]
        basis = sorted({b for e in open_events for b in e.get("basis", [])})
        if basis:
            lines.append(t("a1.chat.basis", items="；".join(basis)))
        return "\n".join([t("a1.chat.events_intro"), *lines, t("a1.chat.propose_event")])

    def _say_event_confirm(self, data: dict[str, Any]) -> str:
        pending = data.get("event_register")
        return t("a1.chat.confirm_card", summary=pending["summary"]) if pending else ""

    def _say_dining(self, data: dict[str, Any]) -> str:
        found = data.get("merchant_recommend")
        if found is None:
            return ""
        if not found["merchants"]:
            return t("a1.chat.no_dining", min=f"{found['min_rating']:.1f}")
        lines = [
            t(
                "a1.chat.dining_option",
                name=m["name"],
                category=m["category"],
                rating=f"{m['rating']:.1f}",
                reason="；".join(m["reasons"][1:3]),
            )
            for m in found["merchants"]
        ]
        intro = t("a1.chat.dining_intro", min=f"{found['min_rating']:.1f}")
        tail = [found["excluded_text"]] if found.get("excluded_text") else []
        return "\n".join([intro, *lines, *tail])

    def _say_stats(self, data: dict[str, Any]) -> str:
        summary = data.get("health_summary")
        if summary is None:
            return ""
        lines = summary["lines"] or [t("a1.chat.stats_empty")]
        return "\n".join([t("a1.chat.stats_intro", title=summary["title"]), *lines])

    def _say_consent(self, data: dict[str, Any]) -> str:
        if "consent_mgmt" not in data:
            return ""
        current = data["consent_mgmt"].get("scope", {})
        if not current:
            return t("a1.chat.consent_none")
        names = "、".join(t(f"consent.source.{s}.name") for s in current)
        text = t("a1.chat.consent", sources=names)
        explain = data.get("consent_explain")
        if explain:
            text += "\n" + "\n".join(f"・{line}" for line in explain["who_sees"])
        return text

    def _say_revoke(self, data: dict[str, Any]) -> str:
        result = data.get("consent_mgmt")
        if result is None:
            return ""
        if "summary" in result:
            # 撤回只說明影響，不加任何其他文案（CLAUDE.md）
            return str(result["summary"])
        return self._say_consent(data) + t("a1.chat.revoke_which")
