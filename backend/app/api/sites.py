"""據點 API（SPEC §9 據點）：量測上傳、今日工作板、關懷代號清單、解碼、訪視回報。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.adapters.base import RawRecord
from app.adapters.site import SiteAdapter
from app.api.deps import DB
from app.audit.writer import write_audit
from app.auth.deps import require_roles
from app.auth.security import Principal
from app.auth.user_qr import InvalidUserQr, read_user_qr
from app.db.models_idn import User
from app.fhir.writer import ingest
from app.i18n import t
from app.masking import mask_name
from app.services import visit
from app.tools.registry import ToolContext, registry

router = APIRouter(prefix="/sites", tags=["sites 據點"])

SiteStaff = Annotated[Principal, Depends(require_roles("site_staff", "admin"))]


class MeasurementItems(BaseModel):
    bp: tuple[Annotated[int, Field(ge=60, le=260)], Annotated[int, Field(ge=30, le=160)]] | None = (
        None
    )
    weight: Annotated[float, Field(ge=10, le=300)] | None = None
    height: Annotated[float, Field(ge=50, le=230)] | None = None
    body_fat: Annotated[float, Field(ge=2, le=70)] | None = None
    glucose: Annotated[float, Field(ge=20, le=600)] | None = None


class MeasurementIn(BaseModel):
    user_qr: str
    items: MeasurementItems
    measured_at: datetime | None = None
    device_id: str | None = Field(default=None, description="模擬藍牙設備編號")


class MeasurementOut(BaseModel):
    person: str
    written: int
    resource_ids: list[uuid.UUID]
    rejected_items: int


def _check_site(principal: Principal, site_id: str) -> None:
    if principal.role != "admin" and principal.site_id != site_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("site.error.not_your_site"))


@router.post(
    "/{site_id}/measurements", response_model=MeasurementOut, summary="據點量測上傳（掃民眾 QR）"
)
def upload_measurements(
    site_id: str, body: MeasurementIn, staff: SiteStaff, db: DB
) -> MeasurementOut:
    _check_site(staff, site_id)
    try:
        user_id = read_user_qr(body.user_qr)
    except InvalidUserQr:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("site.error.bad_qr")) from None
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("site.error.bad_qr"))
    items = body.items.model_dump()

    payload = {
        "site_id": site_id,
        "measured_at": body.measured_at or datetime.now(UTC),
        "device_id": body.device_id,
        "items": items,
    }
    resources = SiteAdapter().normalize(RawRecord("site", payload, datetime.now(UTC)))
    if not resources:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, t("site.error.no_items"))
    result = ingest(db, user_id=user_id, source="site", resources=resources)
    rejected = result.skipped_reason is not None
    write_audit(
        # 被拒時請求交易會 rollback，稽核要另開交易才留得下來
        None if rejected else db,
        actor_type="staff",
        actor_id=str(staff.account_id),
        action="site.measurement.upload",
        input={"site_id": site_id, "items": sorted(k for k, v in items.items() if v)},
        output={"written": result.written, "out_of_scope": result.out_of_scope},
        decision="rejected" if rejected else "executed",
        reason=result.skipped_reason,
    )
    if rejected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("site.error.no_consent"))
    return MeasurementOut(
        person=mask_name(user.name),
        written=result.written,
        resource_ids=result.inserted,
        rejected_items=result.out_of_scope,
    )


# --- 今日工作板與關懷任務（SPEC §9 據點；SKILL A5，一期由據點人員直接操作） ---


@router.get("/{site_id}/today", summary="今日工作板（預約人數、關懷任務數；無健康數值）")
def today(site_id: str, staff: SiteStaff, db: DB) -> dict[str, Any]:
    _check_site(staff, site_id)
    return visit.today_board(db, site_id)


class CareTaskOut(BaseModel):
    code: str
    task_type: str
    rule_id: str
    status: str
    created_at: datetime
    decoded: bool


@router.get(
    "/{site_id}/care-tasks",
    response_model=list[CareTaskOut],
    summary="本據點的關懷代號清單（只有代號、任務類型、規則編號）",
)
def care_tasks(site_id: str, staff: SiteStaff, db: DB) -> list[CareTaskOut]:
    _check_site(staff, site_id)
    return [CareTaskOut(**vars(row)) for row in visit.care_tasks(db, site_id)]


def _site_ctx(staff: Principal, db: Any) -> ToolContext:
    return ToolContext(
        agent="site-api",
        db=db,
        actor_type="staff",
        actor_id=str(staff.account_id),
        site_id=staff.site_id,
    )


class DecodeIn(BaseModel):
    task_ref: str = Field(min_length=1, max_length=40, description="任務編號（必填，寫入稽核）")


@router.post(
    "/{site_id}/care-tasks/{code}/decode",
    summary="解碼關懷代號（必須附任務編號；逐次稽核；回傳姓名、聯絡、訪綱，無健康資料）",
)
def decode(site_id: str, code: str, body: DecodeIn, staff: SiteStaff, db: DB) -> dict[str, Any]:
    _check_site(staff, site_id)
    ctx = _site_ctx(staff, db)
    decoded = registry.call("decode_care_list", {"code": code, "task_ref": body.task_ref}, ctx).data
    assert decoded is not None
    guide = registry.call("visit_guide", {"task_type": decoded["task_type"]}, ctx).data
    return {**decoded, "visit_guide": (guide or {}).get("steps", [])}


class VisitReportIn(BaseModel):
    task_ref: str = Field(min_length=1, max_length=40)
    reason_class: Literal["distance", "equipment", "willingness", "health", "other"]
    needs: list[str] = Field(default_factory=list, max_length=6)
    referral: bool = False
    next_followup: date | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post(
    "/{site_id}/care-tasks/{code}/report",
    summary="訪視回報（去姓名後由每日批次聚合進 anon.visit_outcomes）",
)
def report(
    site_id: str, code: str, body: VisitReportIn, staff: SiteStaff, db: DB
) -> dict[str, Any]:
    _check_site(staff, site_id)
    saved = visit.submit_report(
        db,
        site_id=site_id,
        staff_account_id=staff.account_id,
        code=code,
        task_ref=body.task_ref,
        reason_class=body.reason_class,
        needs=body.needs,
        referral=body.referral,
        next_followup=body.next_followup,
        note=body.note,
    )
    return {"report_id": str(saved.id), "status": "visited", "message": t("site.report_saved")}
