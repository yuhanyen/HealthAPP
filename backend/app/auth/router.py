from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select

from app.api import ratelimit
from app.api.deps import DB
from app.audit.writer import write_audit
from app.auth.deps import CurrentPrincipal
from app.auth.security import create_access_token, verify_password
from app.config import get_settings
from app.db.models_sys import Account
from app.i18n import t

router = APIRouter(prefix="/auth", tags=["auth 身分驗證"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    display_name: str


class MeOut(BaseModel):
    account_id: uuid.UUID
    role: str
    display_name: str
    user_id: uuid.UUID | None
    site_id: str | None
    merchant_id: str | None


@router.post("/token", response_model=TokenOut, summary="登入取得 Bearer token")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB, request: Request
) -> TokenOut:
    client = request.client.host if request.client else "unknown"
    ratelimit.check(f"login:{form.username}:{client}", limit=get_settings().rate_login_per_min)
    account = db.scalars(
        select(Account).where(Account.username == form.username, Account.active.is_(True))
    ).first()
    if account is None or not verify_password(form.password, account.password_hash):
        write_audit(
            None,
            actor_type="user",
            actor_id=f"username:{form.username}",
            action="auth.login",
            decision="rejected",
            reason="帳號或密碼錯誤",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, t("auth.invalid_credentials"))
    write_audit(
        db,
        actor_type="admin" if account.role == "admin" else "user",
        actor_id=str(account.id),
        action="auth.login",
        decision="executed",
    )
    return TokenOut(
        access_token=create_access_token(account),
        role=account.role,
        display_name=account.display_name,
    )


@router.get("/me", response_model=MeOut, summary="目前登入身分")
def me(principal: CurrentPrincipal) -> MeOut:
    return MeOut(
        account_id=principal.account_id,
        role=principal.role,
        display_name=principal.display_name,
        user_id=principal.user_id,
        site_id=principal.site_id,
        merchant_id=principal.merchant_id,
    )
