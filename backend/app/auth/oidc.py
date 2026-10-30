"""OIDC 介面（一期不實作；SPEC §16-1：自建簡版，介面留 OIDC／TW FidO）。

TODO(二期)：接 Keycloak 或 TW FidO（對口：數位發展部）。實作此 Protocol 後，
在 auth/router.py 增加 /auth/oidc/callback，將外部身分對應到 sys.accounts。
"""

from __future__ import annotations

from typing import Protocol

from app.auth.security import Principal


class IdentityProvider(Protocol):
    def authorization_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> Principal: ...
