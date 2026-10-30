"""關懷規則（SPEC §7.2 care_rules）：規則在 idn 執行，只輸出代號。

機關側 Agent（A2）只拿到（代號, 任務類型, 規則編號, 據點）——沒有姓名、沒有任何健康數值；
代號由據點人員在據點端輸入任務編號解碼（care/decode.py，逐次稽核）。
這個服務自己以 idn_app 連線執行；A2 本身的 DB 帳號仍只有 anon_reader（C-2）。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict, dataclass
from datetime import date, datetime

from sqlalchemy import select

from app.audit.writer import write_audit
from app.consent.service import effective_scope
from app.db.engines import DbRole, session_scope
from app.db.models_idn import CareTask, User
from app.events.outbox import enqueue
from app.services import geo, risk

log = logging.getLogger(__name__)

RULES = {
    "R-BP-L3": ("revisit_reminder", "血壓連續偏高且 30 日內未回診"),
    "R-STEPS-DROP": ("phone_followup", "65 歲以上、步數兩週下降四成以上"),
}


@dataclass(frozen=True)
class CareTaskOut:
    code: str
    task_type: str
    rule_id: str
    site_id: str


def _new_code(existing: set[str]) -> str:
    while True:
        code = f"N-{secrets.randbelow(9000) + 1000}"
        if code not in existing:
            existing.add(code)
            return code


def run_care_rules(today: date, *, publish: bool = True) -> list[CareTaskOut]:
    created: list[CareTaskOut] = []
    with session_scope(DbRole.IDN_APP) as s:
        open_pairs = {
            (uid, rule)
            for uid, rule in s.execute(
                select(CareTask.user_id, CareTask.rule_id).where(CareTask.status != "closed")
            )
        }
        codes = set(s.scalars(select(CareTask.code)))
        for user in s.scalars(select(User)):
            scope = effective_scope(s, user.id)
            if not scope:
                continue
            result = risk.evaluate(s, user.id, scope, today)
            age = today.year - user.birth_date.year
            rule = None
            if result.level == "L3":
                rule = "R-BP-L3"
            elif "steps_drop" in result.triggers and age >= 65:
                rule = "R-STEPS-DROP"
            if rule is None or (user.id, rule) in open_pairs:
                continue
            site = geo.nearest_site(s, user.village_code)
            if site is None:
                continue
            task = CareTask(
                code=_new_code(codes),
                user_id=user.id,
                site_id=site,
                task_type=RULES[rule][0],
                rule_id=rule,
            )
            s.add(task)
            created.append(CareTaskOut(task.code, task.task_type, rule, site))
        if publish:
            by_site: dict[str, list[str]] = {}
            for item in created:
                by_site.setdefault(item.site_id, []).append(item.code)
            for site_id, site_codes in by_site.items():
                enqueue(
                    s, "care.list.ready", date=today.isoformat(), site_id=site_id, codes=site_codes
                )
        write_audit(
            s,
            actor_type="system",
            actor_id="care_rules",
            action="care.rules.run",
            decision="executed",
            output={"created": [asdict(c) for c in created]},
            reason=f"{datetime.now().isoformat(timespec='seconds')} 產生 {len(created)} 筆代號",
        )
    log.info("關懷規則：新增 %d 筆", len(created))
    return created
