from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.security import InvalidToken, Principal, decode_token
from app.i18n import t

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def get_principal(token: Annotated[str | None, Depends(oauth2_scheme)]) -> Principal:
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            t("auth.unauthenticated"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_token(token)
    except InvalidToken:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            t("auth.unauthenticated"),
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, t("auth.forbidden"))
        return principal

    return dependency


def require_citizen(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if principal.role != "citizen" or principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("auth.forbidden"))
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
CurrentCitizen = Annotated[Principal, Depends(require_citizen)]
CurrentAdmin = Annotated[Principal, Depends(require_roles("admin"))]
