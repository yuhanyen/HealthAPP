"""把所有模型註冊進 Base.metadata（供 Alembic 與測試使用）。

這是除了 app/deidentify/ 以外，唯一允許同時 import idn 與 anon 模型的地方；
check_schema 的 import 規則有列入白名單。
"""

from app.db import models_anon, models_idn, models_pub, models_sys
from app.db.base import Base

__all__ = ["Base", "models_anon", "models_idn", "models_pub", "models_sys"]
