"""FHIR 寫入（所有 Adapter 進 idn 的唯一入口）。

1. 在同一交易內以 FOR SHARE 鎖住該來源的現行授權：撤回（FOR UPDATE）會等這筆寫完，
   之後的刪除任務就會一併刪掉；若撤回先完成，這裡讀到已撤回就整批不寫。
2. 只寫授權範圍內的資料項目（scope.read），範圍外的直接丟棄並計數。
3. 以 (user, type, source, code, effective_at) upsert，重複拉取不會重複。
4. commit 後發事件：site 每筆一則 measurement.created；wearable 每批一則；nhi 每批一則 nhi.updated。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Result, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_idn import FHIR_RESOURCE_TYPES, Consent, FhirResource
from app.events.outbox import enqueue
from app.fhir import codes as c

log = logging.getLogger(__name__)


class InvalidResource(ValueError):
    pass


@dataclass
class IngestResult:
    inserted: list[uuid.UUID] = field(default_factory=list)
    updated: int = 0
    out_of_scope: int = 0
    skipped_reason: str | None = None

    @property
    def written(self) -> int:
        return len(self.inserted) + self.updated


def primary_code(resource: dict[str, Any]) -> str:
    for path in ("code", "vaccineCode", "medicationCodeableConcept"):
        coding = (resource.get(path) or {}).get("coding") or []
        if coding and coding[0].get("code"):
            return str(coding[0]["code"])
    klass = resource.get("class") or {}
    if klass.get("code"):
        return str(klass["code"])
    return str(resource["resourceType"]).lower()


def _parse_moment(value: str) -> datetime:
    if len(value) == 10:  # 只有日期：視為台北時間當天 00:00
        return datetime.combine(
            date.fromisoformat(value), time(), ZoneInfo(get_settings().timezone)
        )
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(get_settings().timezone))
    return moment


def effective_of(resource: dict[str, Any]) -> datetime:
    for key in ("effectiveDateTime", "occurrenceDateTime", "recordedDate"):
        if resource.get(key):
            return _parse_moment(resource[key])
    for key in ("effectivePeriod", "period"):
        start = (resource.get(key) or {}).get("start")
        if start:
            return _parse_moment(start)
    raise InvalidResource(f"{resource.get('resourceType')} 沒有可辨識的時間欄位")


def consent_item(source: str, resource: dict[str, Any]) -> str | None:
    """這筆資源受哪個授權項目管轄；None 表示不認得（不寫入）。"""
    if source == "nhi":
        return c.NHI_ITEM_OF_TYPE.get(resource["resourceType"])
    table = c.SITE_ITEM_OF_CODE if source == "site" else c.WEARABLE_ITEM_OF_CODE
    return table.get(primary_code(resource))


def validate(resource: dict[str, Any]) -> None:
    rtype = resource.get("resourceType")
    if rtype not in FHIR_RESOURCE_TYPES:
        raise InvalidResource(f"不支援的 resourceType：{rtype}")
    if rtype == "Observation":
        if resource.get("status") not in ("final", "amended", "preliminary"):
            raise InvalidResource("Observation.status 不正確")
        if "valueQuantity" not in resource and "component" not in resource:
            raise InvalidResource("Observation 缺少 valueQuantity 或 component")
    effective_of(resource)


def ingest(
    session: Session,
    *,
    user_id: uuid.UUID,
    source: str,
    resources: list[dict[str, Any]],
    emit_events: bool = True,
) -> IngestResult:
    result = IngestResult()
    consent = session.scalars(
        select(Consent)
        .where(
            Consent.user_id == user_id,
            Consent.source == source,
            Consent.revoked_at.is_(None),
            Consent.superseded_at.is_(None),
        )
        .with_for_update(read=True)
    ).first()
    if consent is None:
        result.skipped_reason = "no_active_consent"
        return result

    allowed = set(consent.scope.get("read", []))
    # 同一批內若有相同自然鍵（例如同一天兩筆門診），以最後一筆為準；否則 ON CONFLICT 會報錯
    rows: dict[tuple[str, str, datetime], dict[str, Any]] = {}
    for resource in resources:
        validate(resource)
        if consent_item(source, resource) not in allowed:
            result.out_of_scope += 1
            continue
        row = {
            "user_id": user_id,
            "resource_type": resource["resourceType"],
            "resource": resource,
            "source": source,
            "code": primary_code(resource),
            "effective_at": effective_of(resource),
        }
        rows[(row["resource_type"], row["code"], row["effective_at"])] = row
    if not rows:
        return result

    base = insert(FhirResource).values(list(rows.values()))
    returned: Result[Any] = session.execute(
        base.on_conflict_do_update(
            constraint="uq_fhir_resources_natural_key",
            set_={"resource": base.excluded.resource},
        ).returning(FhirResource.id, literal_column("(xmax = 0)").label("inserted"))
    )
    for written in returned:
        if written.inserted:
            result.inserted.append(written.id)
        else:
            result.updated += 1

    if emit_events and result.inserted:
        _emit(session, user_id, source, result)
    return result


def _emit(session: Session, user_id: uuid.UUID, source: str, result: IngestResult) -> None:
    if source == "site":
        for resource_id in result.inserted:
            enqueue(
                session,
                "measurement.created",
                user_id=str(user_id),
                source=source,
                resource_id=str(resource_id),
            )
    elif source == "wearable":
        enqueue(
            session,
            "measurement.created",
            user_id=str(user_id),
            source=source,
            resource_id=str(result.inserted[-1]),
            count=len(result.inserted),
        )
    elif source == "nhi":
        enqueue(session, "nhi.updated", user_id=str(user_id), count=len(result.inserted))
