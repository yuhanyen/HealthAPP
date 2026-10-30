"""idn 敏感欄位的應用層加密（SPEC §11：AES-GCM，金鑰在環境變數）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

_PREFIX = "v1."
_NONCE_BYTES = 12


class FieldCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("FIELD_ENCRYPTION_KEY 必須是 32 bytes（urlsafe base64）")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token.startswith(_PREFIX):
            raise ValueError("非預期的密文格式")
        raw = base64.urlsafe_b64decode(token[len(_PREFIX) :])
        return self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], None).decode("utf-8")


@lru_cache
def get_cipher() -> FieldCipher:
    key = get_settings().field_encryption_key
    if not key:
        raise RuntimeError("未設定 FIELD_ENCRYPTION_KEY")
    return FieldCipher(base64.urlsafe_b64decode(key))


class EncryptedText(TypeDecorator[str]):
    """存入時加密、讀出時解密；資料庫裡只看得到密文。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else get_cipher().decrypt(value)


def hash_national_id(national_id: str) -> str:
    pepper = get_settings().national_id_pepper.encode("utf-8")
    normalized = national_id.strip().upper().encode("utf-8")
    return hmac.new(pepper, normalized, hashlib.sha256).hexdigest()
