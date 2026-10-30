"""據點端關懷任務：清單、今日工作板、訪視回報（SKILL A5「關懷派工」「收回訪視結果」）。

據點 API 以 idn_app 連線操作，但每個查詢都限定「本據點」；清單只有代號與任務類型，
姓名等只能經 decode_care_list（附任務編號、逐次稽核）取得。訪視回報去姓名後由 deidentify
聚合進 anon.visit_outcomes。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.writer import write_audit
from app.db.models_idn import VISIT_REASONS, Booking, CareTask, DecodeAudit, VisitReport
from app.db.models_pub import Site
from app.i18n import t


class VisitError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(t(f"site.error.{key}"))


@dataclass(frozen=True)
class TaskRow:
    code: str
    task_type: str
    rule_id: str
    status: str
    created_at: datetime
    decoded: bool


def care_tasks(session: Session, site_id: str) -> list[TaskRow]:
    decoded = set(
        session.scalars(select(DecodeAudit.care_task_code).where(DecodeAudit.site_id == site_id))
    )
    rows = session.scalars(
        select(CareTask)
        .where(CareTask.site_id == site_id, CareTask.status != "closed")
        .order_by(CareTask.created_at.desc())
    )
    return [
        TaskRow(r.code, r.task_type, r.rule_id, r.status, r.created_at, r.code in decoded)
        for r in rows
    ]


def today_board(session: Session, site_id: str, now: datetime | None = None) -> dict[str, object]:
    """今日工作板：預約時段分佈（只有人數）、關懷任務數。不含任何健康數值。"""
    from app.tools._common import tz

    now = now or datetime.now(UTC)
    local = now.astimezone(tz())
    start = datetime.combine(local.date(), datetime.min.time(), tz())
    site = session.get(Site, site_id)
    slots = session.execute(
        select(Booking.slot_at, func.count())
        .where(
            Booking.site_id == site_id,
            Booking.status == "booked",
            Booking.slot_at >= start,
            Booking.slot_at < start + timedelta(days=1),
        )
        .group_by(Booking.slot_at)
        .order_by(Booking.slot_at)
    ).all()
    tasks = care_tasks(session, site_id)
    return {
        "site_id": site_id,
        "site_name": site.name if site else site_id,
        "date": local.date().isoformat(),
        "bookings": [{"slot_at": at.isoformat(), "count": n} for at, n in slots],
        "booking_total": sum(n for _, n in slots),
        "care_tasks": {
            "total": len(tasks),
            "open": sum(t.status == "open" for t in tasks),
            "visited": sum(t.status == "visited" for t in tasks),
        },
        "equipment": t("site.board.equipment_stub"),
    }


def submit_report(
    session: Session,
    *,
    site_id: str,
    staff_account_id: uuid.UUID,
    code: str,
    task_ref: str,
    reason_class: str,
    needs: list[str],
    referral: bool,
    next_followup: date | None,
    note: str | None,
) -> VisitReport:
    if reason_class not in VISIT_REASONS:
        raise VisitError("bad_reason")
    if not task_ref.strip():
        raise VisitError("task_ref_required")
    task = session.scalars(select(CareTask).where(CareTask.code == code)).first()
    if task is None or task.site_id != site_id:
        raise VisitError("task_not_found")
    report = VisitReport(
        care_task_id=task.id,
        reported_by=staff_account_id,
        task_ref=task_ref.strip(),
        reason_class=reason_class,
        needs=needs,
        referral=referral,
        next_followup=next_followup,
        note=note,
    )
    session.add(report)
    task.status = "visited"
    session.flush()
    write_audit(
        session,
        actor_type="staff",
        actor_id=str(staff_account_id),
        action="care.visit_report",
        decision="executed",
        input={"code": code, "task_ref": task_ref, "site_id": site_id},
        output={"reason_class": reason_class, "referral": referral},
        reason=t("site.audit.referral_human") if referral else None,
    )
    return report
