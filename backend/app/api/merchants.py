"""商家 API（SPEC §9 商家）：優惠上架、掃碼核銷、月成效（API key 或商家帳號）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.adapters.merchant import MerchantAdapter, verify_api_key
from app.api.deps import DB
from app.audit.writer import write_audit
from app.auth.security import InvalidToken, decode_token
from app.db.models_pub import Offer
from app.i18n import t
from app.services import merchant_ops
from app.sysconfig import get_k

router = APIRouter(prefix="/merchants", tags=["merchants 商家"])
_bearer = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


class OfferIn(BaseModel):
    offer_id: str = Field(pattern=r"^[A-Za-z0-9\-]{2,40}$")
    title: str = Field(min_length=1, max_length=80)
    points_cost: int = Field(ge=1, le=100000)
    stock: int = Field(ge=0, le=100000)
    valid_from: date
    valid_to: date


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    title: str
    points_cost: int
    stock: int
    valid_from: date
    valid_to: date


def merchant_actor(
    merchant_id: str,
    db: DB,
    token: Annotated[str | None, Depends(_bearer)],
    x_merchant_key: Annotated[str | None, Header()] = None,
) -> str:
    """商家身分：API key（系統對接）或商家帳號登入（商家端網頁）；回傳稽核用 actor_id。"""
    if x_merchant_key and verify_api_key(db, merchant_id, x_merchant_key):
        return f"merchant-key:{merchant_id}"
    if token:
        try:
            principal = decode_token(token)
        except InvalidToken:
            principal = None
        if principal and principal.role == "merchant" and principal.merchant_id == merchant_id:
            return str(principal.account_id)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, t("merchant.error.auth"))


MerchantActor = Annotated[str, Depends(merchant_actor)]


@router.get("/{merchant_id}/offers", response_model=list[OfferOut], summary="商家優惠列表")
def list_offers(merchant_id: str, db: DB) -> list[Offer]:
    return list(
        db.scalars(select(Offer).where(Offer.merchant_id == merchant_id).order_by(Offer.id))
    )


@router.post("/{merchant_id}/offers", response_model=OfferOut, summary="上架／更新優惠")
def upsert_offer(merchant_id: str, body: OfferIn, actor: MerchantActor, db: DB) -> Offer:
    if body.valid_to < body.valid_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, t("merchant.error.dates"))
    existing = db.get(Offer, body.offer_id)
    if existing is not None and existing.merchant_id != merchant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("merchant.error.not_yours"))
    offer = MerchantAdapter().upsert_offer(db, {**body.model_dump(), "merchant_id": merchant_id})
    write_audit(
        db,
        actor_type="user",
        actor_id=actor,
        action="merchant.offer.upsert",
        input=body.model_dump(mode="json"),
        decision="executed",
    )
    return offer


class VerifyIn(BaseModel):
    qr_token: str = Field(min_length=4, max_length=200)


@router.post("/{merchant_id}/verify", summary="掃碼核銷兌換券（只回品項與點數，看不到兌換者）")
def verify(merchant_id: str, body: VerifyIn, actor: MerchantActor, db: DB) -> dict[str, Any]:
    try:
        return merchant_ops.verify(db, merchant_id=merchant_id, qr_token=body.qr_token, actor=actor)
    except merchant_ops.CouponError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/{merchant_id}/report", summary="月成效報告（聚合；年齡層人數 < k 遮蔽；無任何個人）")
def report(
    merchant_id: str,
    actor: MerchantActor,
    db: DB,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> dict[str, Any]:
    return merchant_ops.monthly_report(db, merchant_id=merchant_id, month=month, k=get_k(db))
