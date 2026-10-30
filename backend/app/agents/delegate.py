"""委派協定（SPEC §8.3）：A2 → A3／A4。

    {"task_id", "from": "A2", "to": "A3", "spec": {...}}
    → {"task_id", "findings": [{claim, evidence, confidence}], "queries", "charts",
       "data_cutoff", "k", ...}

A2 驗證（SKILL A2「交證據不交結論」）：
- A3：queries 非空，且每個 finding 都有 evidence。
- A4：時間軸每一條都有 source_url。
不通過 → status=rejected，A2 不得採用；每次委派寫一筆稽核。
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from app.audit.writer import write_audit

log = logging.getLogger(__name__)

Target = Literal["A3", "A4"]


def new_task_id() -> str:
    return f"D-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(3)}"


def validate(to: Target, result: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if to == "A3":
        if not result.get("queries"):
            problems.append("A3 沒有附查詢編號（queries）")
        problems += [
            f"結論缺依據：{f.get('claim', '')[:30]}"
            for f in result.get("findings", [])
            if not f.get("evidence")
        ]
        if not result.get("findings"):
            problems.append("A3 沒有任何結論")
    else:
        problems += [
            f"事件缺來源連結：{e.get('event', '')[:30]}"
            for e in result.get("timeline", [])
            if not e.get("source_url")
        ]
    return problems


def delegate(to: Target, spec: dict[str, Any], *, requested_by: str) -> dict[str, Any]:
    task_id = new_task_id()
    task = {"task_id": task_id, "from": "A2", "to": to, "spec": spec}
    try:
        if to == "A3":
            from app.agents import a3

            result = a3.run(task)
        else:
            from app.agents import a4

            result = a4.run(task)
    except Exception as exc:
        log.exception("委派 %s 失敗", to)
        result = {"error": type(exc).__name__}
    problems = validate(to, result) if "error" not in result else [f"{to} 執行失敗"]
    status = "rejected" if problems else "accepted"
    write_audit(
        None,
        actor_type="agent",
        actor_id=requested_by,
        action="agent.delegate",
        tool=f"delegate_{to.lower()}",
        decision="executed" if status == "accepted" else "rejected",
        input=task,
        output=result,
        reason="；".join(problems) if problems else f"{to} 交回證據齊全，採用",
    )
    return {**result, "task_id": task_id, "to": to, "status": status, "problems": problems}
