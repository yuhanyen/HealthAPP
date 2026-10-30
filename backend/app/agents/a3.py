"""A3 數據分析 Agent（SPEC §8.2；行為見 skills/a3-data-analysis/SKILL.md）。

接 A2 的委派（§8.3），交回「程式（查詢語句）、圖表、結論」三件，每個數字都能重跑：
- 有 ANTHROPIC_API_KEY：Claude 依 SKILL 自行使用 sandbox_sql／sandbox_python／
  chart_render／report_build。
- 無金鑰（[MOCK]）：依 SKILL「標準分析流程」以固定程式執行同一組工具（查結構 → 每個指標一條 SQL →
  沙箱 Python 算相關 → 畫圖 → 報告附全部 SQL 與 Python）。
連線一律 anon_reader（C-2），查詢經 k-匿名（C-4），工具呼叫逐次稽核（C-6）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from app.adapters.base import mock_notice
from app.agents import agency, runtime
from app.deidentify.aggregate import METRICS
from app.i18n import t
from app.llm import client as llm

AGENT = "A3"

SERIES_SQL = """SELECT week, village_code, round(sum(value * n) / sum(n), 4) AS value, sum(n) AS n
FROM anon.cohort_weekly
WHERE metric = '{metric}' AND age_band = 'ALL' AND sex = 'ALL'{villages}
  AND week <= (SELECT max(week) FROM anon.cohort_weekly WHERE metric = '{metric}') - 7
  AND week > (SELECT max(week) FROM anon.cohort_weekly WHERE metric = '{metric}') - {days}
GROUP BY week, village_code
ORDER BY week, village_code"""

# 沙箱只有標準函式庫：以純 Python 計算皮爾森相關（同一里同一週的兩個指標配對）
CORRELATION_PY = """import json, math

series = INPUTS["series"]  # {指標: {里: {週: 值}}}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


out = {}
for a, b in INPUTS["pairs"]:
    xs, ys = [], []
    for village, points in series.get(a, {}).items():
        if village == "ALL":
            continue
        other = series.get(b, {}).get(village, {})
        for week, value in points.items():
            if value is not None and other.get(week) is not None:
                xs.append(value)
                ys.append(other[week])
    r = pearson(xs, ys)
    out[a + "|" + b] = {"r": None if r is None else round(r, 2), "n_points": len(xs)}
print(json.dumps(out))
"""


def _call(task_id: str, tool: str, **args: Any) -> dict[str, Any]:
    return agency.call(AGENT, f"{AGENT}:{task_id}", tool, **args)


def _label(metric: str) -> str:
    return t(f"metric.{metric}")


def _fmt(metric: str, value: float) -> str:
    return f"{value:,.0f}" if metric == "steps_avg" else f"{value:.2f}"


def _change(points: dict[str, float]) -> tuple[float, float] | None:
    """近 4 週平均 vs 之前的平均。"""
    weeks = sorted(points)
    if len(weeks) < 6:
        return None
    recent = [points[w] for w in weeks[-4:]]
    earlier = [points[w] for w in weeks[:-4]]
    return sum(earlier) / len(earlier), sum(recent) / len(recent)


def _direction(earlier: float, recent: float) -> str:
    delta = recent - earlier
    if abs(delta) < max(0.02, 0.1 * abs(earlier)):
        return "stable"
    return "up" if delta > 0 else "down"


def run(task: dict[str, Any]) -> dict[str, Any]:
    if llm.llm_available():
        return _run_claude(task)
    mock_notice("A3", "未設定 ANTHROPIC_API_KEY，A3 以固定分析流程執行")
    return run_pipeline(task)


def run_pipeline(task: dict[str, Any]) -> dict[str, Any]:
    task_id = task["task_id"]
    spec = task["spec"]
    villages = agency.clean_villages(spec.get("village_codes"))
    metrics = [m for m in spec.get("metrics", []) if m in METRICS] or ["bp_uncontrolled_rate"]
    weeks = int(spec.get("weeks", 12))
    primary = metrics[0]

    # 1–2. 確認欄位定義與資料截止
    catalog = _call(task_id, "schema_catalog")["tables"]["anon.cohort_weekly"]

    # 3–4. 每個指標一條 SQL（經 k-匿名；被遮蔽的格子不補值）
    village_filter = (
        " AND village_code IN ({})".format(", ".join(f"'{v}'" for v in [*villages, "ALL"]))
        if villages
        else ""
    )
    series: dict[str, dict[str, dict[str, float]]] = {}
    queries: list[str] = []
    sqls: list[str] = []
    masked = 0
    k = 5
    all_weeks: set[str] = set()
    for metric in metrics:
        sql = SERIES_SQL.format(metric=metric, villages=village_filter, days=(weeks + 1) * 7)
        result = _call(task_id, "sandbox_sql", sql=sql)
        queries.append(result["query_id"])
        sqls.append(f"-- {result['query_id']}（{_label(metric)}）\n{sql}")
        masked += result["masked_rows"]
        k = result["k"]
        for row in result["rows"]:
            if row.get("_masked") or row.get("value") is None:
                continue
            series.setdefault(metric, {}).setdefault(row["village_code"], {})[row["week"]] = float(
                row["value"]
            )
            all_weeks.add(row["week"])
    if not all_weeks:
        return {"findings": [], "queries": queries, "charts": [], "k": k, "masked_rows": masked}
    week_list = sorted(all_weeks)
    cutoff = (date.fromisoformat(week_list[-1]) + timedelta(days=6)).isoformat()

    # 5. 相關（沙箱 Python，程式碼一併交回）
    pairs = [[primary, m] for m in metrics[1:]]
    correlations: dict[str, Any] = {}
    if pairs:
        py = _call(
            task_id,
            "sandbox_python",
            code=CORRELATION_PY,
            inputs={"series": series, "pairs": pairs},
        )
        if py.get("exit_code") == 0 and py.get("stdout"):
            correlations = json.loads(py["stdout"].strip().splitlines()[-1])

    # 4. EDA：主要指標各里的變化
    changes: list[tuple[str, float, float, str]] = []
    for village, points in series.get(primary, {}).items():
        change = _change(points)
        if change and village != "ALL":
            changes.append((village, *change, _direction(*change)))
    rising = sorted((c for c in changes if c[3] == "up"), key=lambda c: c[2] - c[1], reverse=True)[
        :3
    ]
    focus = [c[0] for c in rising] or [c[0] for c in changes[:3]]

    labels = [f"{int(w[5:7])}/{int(w[8:10])}" for w in week_list]
    charts: list[str] = []
    for i, metric in enumerate(metrics[:2], start=1):
        lines = [
            {
                "name": t("a3.series_all") if v == "ALL" else v,
                "values": [series.get(metric, {}).get(v, {}).get(w) for w in week_list],
            }
            for v in [*focus, "ALL"]
            if v in series.get(metric, {})
        ]
        if lines:
            chart = _call(
                task_id,
                "chart_render",
                spec={
                    "type": "line",
                    "title": t("a3.chart_title", n=i, metric=_label(metric), weeks=weeks),
                    "x": labels,
                    "series": lines,
                    "y_label": _label(metric),
                },
            )
            charts.append(chart["artifact"])

    # 7. 結論：一句話＋依據（查詢編號／圖）
    findings: list[dict[str, Any]] = []
    evidence = f"{queries[0]}, fig1"
    for village, earlier, recent, _ in rising:
        findings.append(
            {
                "claim": t(
                    "a3.finding.rising",
                    village=village,
                    metric=_label(primary),
                    earlier=_fmt(primary, earlier),
                    recent=_fmt(primary, recent),
                ),
                "evidence": evidence,
                "confidence": "high" if recent - earlier > 0.1 else "medium",
                "village_code": village,
            }
        )
    total = series.get(primary, {}).get("ALL")
    district = _change(total) if total else None
    if district:
        findings.append(
            {
                "claim": t(
                    "a3.finding.district",
                    metric=_label(primary),
                    earlier=_fmt(primary, district[0]),
                    recent=_fmt(primary, district[1]),
                    direction=t(f"a3.direction.{_direction(*district)}"),
                ),
                "evidence": evidence,
                "confidence": "high",
            }
        )
    for j, metric in enumerate(metrics[1:], start=1):
        for village in focus:
            pts = series.get(metric, {}).get(village)
            change = _change(pts) if pts else None
            if change:
                findings.append(
                    {
                        "claim": t(
                            "a3.finding.secondary",
                            village=village,
                            metric=_label(metric),
                            earlier=_fmt(metric, change[0]),
                            recent=_fmt(metric, change[1]),
                            direction=t(f"a3.direction.{_direction(*change)}"),
                        ),
                        "evidence": f"{queries[j]}" + (", fig2" if j == 1 else ""),
                        "confidence": "medium",
                        "village_code": village,
                        "metric": metric,
                        "direction": _direction(*change),
                    }
                )
        corr = correlations.get(f"{primary}|{metric}")
        if corr and corr.get("r") is not None:
            findings.append(
                {
                    "claim": t(
                        "a3.finding.correlation",
                        a=_label(primary),
                        b=_label(metric),
                        r=corr["r"],
                        n=corr["n_points"],
                    ),
                    "evidence": f"{queries[0]}, {queries[j]}, sandbox_python",
                    "confidence": "medium" if abs(corr["r"]) >= 0.5 else "low",
                }
            )

    # 8. 報告（附錄含全部 SQL 與 Python、資料截止、k）
    body_findings = "\n".join(f"- {f['claim']}　依據：{f['evidence']}" for f in findings)
    report = _call(
        task_id,
        "report_build",
        title=t("a3.report_title", question=spec.get("question", "")[:40]),
        sections=[
            {
                "heading": t("a3.section.spec"),
                "body": t(
                    "a3.spec_body",
                    villages="、".join(villages) or t("a3.all_villages"),
                    metrics="、".join(_label(m) for m in metrics),
                    weeks=weeks,
                    cutoff=cutoff,
                    k=k,
                ),
            },
            {
                "heading": t("a3.section.cleaning"),
                "body": t("a3.cleaning_body", masked=masked, notes=catalog.get("notes", "")),
            },
            {"heading": t("a3.section.findings"), "body": body_findings, "charts": charts},
            {"heading": t("a3.section.explain"), "body": t("a3.explain_body")},
            {"heading": t("a3.section.limits"), "body": t("a3.limits_body")},
        ],
        appendix=[*sqls, f"# sandbox_python\n{CORRELATION_PY}"],
    )
    return {
        "findings": findings,
        "queries": queries,
        "charts": charts,
        "report": report["artifact"],
        "data_cutoff": cutoff,
        "k": k,
        "masked_rows": masked,
        "focus_villages": focus,
        "sql": sqls,
    }


def _run_claude(task: dict[str, Any]) -> dict[str, Any]:
    spec = runtime.AGENTS[AGENT]
    rc = runtime.RunContext(
        agent=spec,
        tool_context=agency.context_factory(AGENT, f"{AGENT}:{task['task_id']}"),
        context_text=t("a3.context", task=json.dumps(task, ensure_ascii=False)),
        actor_id=f"{AGENT}:{task['task_id']}",
    )
    done = None
    for event in runtime.run(
        rc,
        history=[],
        user_message=task["spec"].get("question", ""),
        planner=runtime.ClaudePlanner(spec),
    ):
        if isinstance(event, runtime.DoneEvent):
            done = event
    return (agency.last_json(done.text) if done else None) or {}
