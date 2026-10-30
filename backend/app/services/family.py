"""守護圈（SPEC §9 /me/family、/family/{owner}/summary；SKILL A1「家屬摘要視角」）。

- 家屬先在自己的 App 產生「守護碼」（30 分鐘有效的簽章 token，只含自己的 user_id），本人輸入後
  建立分享。分享屬高風險動作（C-5）：建立待確認動作，本人確認後才寫入 family_links。
- 本人可隨時收回分享，立即生效、不需確認（收回只會減少揭露）。
- 家屬只看摘要層（SPEC §13-5）：status_sentence、risk_level、pending_action；不含任何數值。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.state_machine import create_pending, register_executor
from app.audit.writer import write_audit
from app.auth.security import ALGORITHM, _secret
from app.db.models_idn import Booking, DailyReport, FamilyLink, PendingAction, User
from app.i18n import t
from app.masking import mask_name

INVITE_TTL = timedelta(minutes=30)
_DIGITS = re.compile(r"\d+([.,/]\d+)*")


class FamilyError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(t(f"family.{key}"))


def issue_invite(viewer_id: uuid.UUID, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    expires = now + INVITE_TTL
    token = jwt.encode(
        {"typ": "family_invite", "uid": str(viewer_id), "iat": now, "exp": expires},
        _secret(),
        algorithm=ALGORITHM,
    )
    return token, expires


def read_invite(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise FamilyError("bad_code") from exc
    if claims.get("typ") != "family_invite":
        raise FamilyError("bad_code")
    return uuid.UUID(claims["uid"])


def request_share(
    session: Session, *, owner_id: uuid.UUID, viewer_id: uuid.UUID, level: str = "summary"
) -> PendingAction:
    if owner_id == viewer_id:
        raise FamilyError("self")
    viewer = session.get(User, viewer_id)
    if viewer is None:
        raise FamilyError("bad_code")
    return create_pending(
        session,
        user_id=owner_id,
        action_type="share",
        payload={"viewer_user_id": str(viewer_id), "level": level},
        summary=t("family.summary", viewer=mask_name(viewer.name)),
        created_by="user",
    )


@register_executor("share")
def _execute_share(session: Session, action: PendingAction) -> dict[str, Any]:
    viewer_id = uuid.UUID(action.payload["viewer_user_id"])
    existing = session.scalars(
        select(FamilyLink).where(
            FamilyLink.owner_user_id == action.user_id,
            FamilyLink.viewer_user_id == viewer_id,
            FamilyLink.revoked_at.is_(None),
        )
    ).first()
    link = existing or FamilyLink(
        owner_user_id=action.user_id, viewer_user_id=viewer_id, level=action.payload["level"]
    )
    session.add(link)
    session.flush()
    write_audit(
        session,
        actor_type="user",
        actor_id=str(action.user_id),
        action="family.share",
        decision="executed",
        input={"viewer_user_id": viewer_id, "level": link.level},
        output={"link_id": link.id},
    )
    return {"link_id": str(link.id), "level": link.level}


def revoke_link(session: Session, *, owner_id: uuid.UUID, link_id: uuid.UUID) -> FamilyLink:
    link = session.get(FamilyLink, link_id)
    if link is None or link.owner_user_id != owner_id:
        raise FamilyError("not_allowed")
    if link.revoked_at is None:
        link.revoked_at = datetime.now(UTC)
        write_audit(
            session,
            actor_type="user",
            actor_id=str(owner_id),
            action="family.revoke",
            decision="executed",
            input={"link_id": link.id},
        )
    return link


@dataclass
class LinkView:
    link_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    level: str
    granted_at: datetime


def links(session: Session, user_id: uuid.UUID) -> tuple[list[LinkView], list[LinkView]]:
    """(我分享給誰, 誰分享給我)，只列有效的。姓名一律遮罩。"""

    def view(link: FamilyLink, other: uuid.UUID) -> LinkView:
        person = session.get_one(User, other)
        return LinkView(link.id, other, mask_name(person.name), link.level, link.granted_at)

    active = list(
        session.scalars(
            select(FamilyLink)
            .where(
                (FamilyLink.owner_user_id == user_id) | (FamilyLink.viewer_user_id == user_id),
                FamilyLink.revoked_at.is_(None),
            )
            .order_by(FamilyLink.granted_at)
        )
    )
    mine = [view(link, link.viewer_user_id) for link in active if link.owner_user_id == user_id]
    theirs = [view(link, link.owner_user_id) for link in active if link.viewer_user_id == user_id]
    return mine, theirs


def _no_numbers(text: str) -> str:
    """摘要層不得出現數值（SPEC §13-5）：狀態句理論上沒有數字，仍保險地移除。"""
    return _DIGITS.sub("", text)


def summary(session: Session, *, viewer_id: uuid.UUID, owner_id: uuid.UUID) -> dict[str, Any]:
    allowed = session.scalar(
        select(FamilyLink.id).where(
            FamilyLink.owner_user_id == owner_id,
            FamilyLink.viewer_user_id == viewer_id,
            FamilyLink.level == "summary",
            FamilyLink.revoked_at.is_(None),
        )
    )
    if allowed is None:
        write_audit(
            None,
            actor_type="user",
            actor_id=str(viewer_id),
            action="family.summary.read",
            decision="rejected",
            input={"owner_user_id": owner_id},
            reason=t("family.not_allowed"),
        )
        raise FamilyError("not_allowed")
    owner = session.get_one(User, owner_id)
    report = session.scalars(
        select(DailyReport)
        .where(DailyReport.user_id == owner_id)
        .order_by(DailyReport.report_date.desc())
        .limit(1)
    ).first()
    pending = session.scalar(
        select(PendingAction.action_type)
        .where(PendingAction.user_id == owner_id, PendingAction.status == "pending")
        .order_by(PendingAction.created_at)
        .limit(1)
    )
    content = report.content if report else {}
    level = (content.get("risk") or {}).get("level")
    upcoming = session.scalar(
        select(Booking.id).where(
            Booking.user_id == owner_id,
            Booking.slot_at >= datetime.now(UTC),
            Booking.status == "booked",
        )
    )
    if pending:
        pending_text: str | None = t(f"family.pending.{pending}")
    elif content.get("priority") == "safety" and upcoming is None:
        pending_text = t("family.pending.decision_booking")
    else:
        pending_text = None
    out = {
        "owner_display_name": mask_name(owner.name),
        "as_of": report.report_date.isoformat() if report else None,
        "status_sentence": _no_numbers(content.get("status_sentence") or t("family.no_report")),
        "risk_level": level,
        "risk_label": t(f"family.risk.{level}") if level else None,
        "pending_action": pending_text,
    }
    write_audit(
        session,
        actor_type="user",
        actor_id=str(viewer_id),
        action="family.summary.read",
        decision="executed",
        input={"owner_user_id": owner_id},
        output=out,
    )
    return out
