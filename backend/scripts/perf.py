"""效能量測（SPEC §11）：產生 docs/perf-report.md。

  python -m scripts.perf [--users 1200] [--parallel 8] [--chats 20]

1. A1 每日批次吞吐：對資料庫裡所有有授權的民眾跑一次 run_daily（不只 App 使用者），
   以實測每人耗時推估 10,000 人在相同平行度下的時間（目標 ≤ 60 分鐘）。
2. 對話首字回應：A1 對話送出到第一個文字事件的時間（目標 < 3 秒）。
3. k-匿名群體查詢（cohort_analysis）與 deidentify 批次耗時。
量測完把為背景居民產生的報告與快照、量測用的對話刪掉，不污染示範資料。
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
import uuid
from datetime import datetime

from sqlalchemy import delete, select

import app.tools  # noqa: F401  註冊工具
from app.agents import a1
from app.agents.runtime import TextEvent
from app.config import PROJECT_ROOT, get_settings
from app.db.engines import DbRole, session_scope
from app.db.models_idn import ChatMessage, Consent, DailyReport, TwinSnapshot
from app.db.models_sys import Account
from app.deidentify.aggregate import recent_weeks, run_deidentify
from app.llm.client import llm_available
from app.tools._common import today
from app.tools.registry import ToolContext, registry

TARGET_USERS = 10_000
TARGET_MINUTES = 60
TARGET_FIRST_TOKEN_S = 3.0


def consenting_users(limit: int) -> list[uuid.UUID]:
    with session_scope(DbRole.IDN_APP) as s:
        return list(
            s.scalars(
                select(Consent.user_id)
                .where(Consent.revoked_at.is_(None), Consent.superseded_at.is_(None))
                .distinct()
                .limit(limit)
            )
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=1200)
    parser.add_argument("--parallel", type=int, default=get_settings().a1_parallelism)
    parser.add_argument("--chats", type=int, default=20)
    args = parser.parse_args(argv)
    day = today()

    with session_scope(DbRole.IDN_APP) as s:
        app_users = set(s.scalars(select(Account.user_id).where(Account.role == "citizen")))
    users = consenting_users(args.users)

    # 1. A1 每日批次
    started = time.perf_counter()
    summary = a1.run_daily_batch(day, user_ids=users, parallel=args.parallel)
    batch_s = time.perf_counter() - started
    per_user = batch_s / max(1, len(users))
    projected_min = per_user * TARGET_USERS / 60

    # 2. 對話首字回應（A1，一位示範民眾）
    demo = sorted((u for u in app_users if u is not None), key=str)[0]
    first_tokens: list[float] = []
    totals: list[float] = []
    for _ in range(args.chats):
        t0 = time.perf_counter()
        first = None
        for event in a1.chat(demo, "我最近狀況怎麼樣？"):
            if first is None and isinstance(event, TextEvent):
                first = time.perf_counter() - t0
        totals.append(time.perf_counter() - t0)
        first_tokens.append(first or totals[-1])

    # 3. k-匿名群體查詢與 deidentify
    query_times: list[float] = []
    for _ in range(10):
        with session_scope(DbRole.ANON_READER) as s:
            t0 = time.perf_counter()
            registry.call(
                "cohort_analysis",
                {"metrics": ["bp_uncontrolled_rate", "measure_freq"], "weeks": 12},
                ToolContext(agent="A2", db=s, actor_id="perf"),
            )
            query_times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    stats = run_deidentify(weeks=recent_weeks(day, 2), today=day)
    deidentify_s = time.perf_counter() - t0

    # 清理：背景居民本來不跑 A1；量測用的對話也刪掉
    others = [u for u in users if u not in app_users]
    with session_scope(DbRole.IDN_APP) as s:
        s.execute(
            delete(DailyReport).where(
                DailyReport.user_id.in_(others), DailyReport.report_date == day
            )
        )
        s.execute(
            delete(TwinSnapshot).where(TwinSnapshot.user_id.in_(others), TwinSnapshot.as_of == day)
        )
        s.execute(delete(ChatMessage).where(ChatMessage.user_id == demo))

    def p95(values: list[float]) -> float:
        return sorted(values)[max(0, int(len(values) * 0.95) - 1)]

    ok_batch = projected_min <= TARGET_MINUTES
    ok_chat = p95(first_tokens) < TARGET_FIRST_TOKEN_S
    verdict = {True: "符合", False: "未符合"}
    llm = (
        "Claude（ANTHROPIC_API_KEY 已設定）"
        if llm_available()
        else "未設定金鑰：A1 對話為規則式 planner（[MOCK]）"
    )
    chat_note = (
        "已使用 Claude 串流量測。"
        if llm_available()
        else "規則式 planner 不經網路；接上 Claude 後首字時間主要取決於 API 延遲"
        "（串流回傳、逐句過濾），需以實際金鑰複測。"
    )
    counted = (
        f"{len(users):,}（報告 {summary.reports:,}、略過 {summary.skipped}、失敗 {summary.failed}）"
    )
    first_line = (
        f"{statistics.median(first_tokens):.2f} 秒／**{p95(first_tokens):.2f} 秒** — "
        f"{verdict[ok_chat]}"
    )
    lines = [
        "# 效能量測報告（SPEC §11）",
        "",
        f"產生時間：{datetime.now().isoformat(timespec='seconds')}　"
        "由 `python -m scripts.perf` 自動產生",
        "",
        "## 環境",
        f"- 主機：{platform.platform()}；Python {platform.python_version()}",
        "- 資料庫：PostgreSQL 16（本機 pgserver），Redis 5（本機）",
        f"- LLM：{llm}",
        f"- A1 平行度：{args.parallel}",
        "",
        f"## 1. 每日批次（目標：{TARGET_USERS:,} 人 ≤ {TARGET_MINUTES} 分鐘）",
        "| 項目 | 數值 |",
        "|---|---|",
        f"| 實測人數 | {counted} |",
        f"| 實測總時間 | {batch_s:.1f} 秒 |",
        f"| 每人平均 | {per_user * 1000:.0f} 毫秒（平行度 {args.parallel}） |",
        f"| 推估 {TARGET_USERS:,} 人 | **{projected_min:.1f} 分鐘** — {verdict[ok_batch]} |",
        "",
        "每日批次依 SKILL 固定步驟呼叫工具（不呼叫 LLM，見 DECISIONS），"
        "每位民眾約 8 次工具呼叫＋稽核寫入。",
        "增加平行度或 API／DB 連線池可再縮短；瓶頸為 PostgreSQL 連線與稽核寫入。",
        "",
        f"## 2. 對話首字回應（目標：< {TARGET_FIRST_TOKEN_S:.0f} 秒）",
        "| 項目 | 數值 |",
        "|---|---|",
        f"| 次數 | {args.chats} |",
        f"| 首字回應 中位數／P95 | {first_line} |",
        f"| 整輪完成 中位數 | {statistics.median(totals):.2f} 秒 |",
        "",
        chat_note,
        "",
        "## 3. 其他",
        "| 項目 | 數值 |",
        "|---|---|",
        f"| cohort_analysis（k-匿名，2 指標 × 12 週）中位數 | "
        f"{statistics.median(query_times) * 1000:.0f} 毫秒 |",
        f"| deidentify 批次（2 週，{stats.cells:,} 格） | {deidentify_s:.1f} 秒 |",
        "",
    ]
    report = "\n".join(lines)
    out = PROJECT_ROOT / "docs" / "perf-report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    return 0 if ok_batch and ok_chat else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
