"""SPEC §8.2：每個 Agent 綁定的 DB 帳號（C-2）。Agent runtime 只能經 agent_engine() 取得連線。"""

from __future__ import annotations

from types import MappingProxyType

from sqlalchemy.engine import Engine

from app.db.engines import DbRole, get_engine

AGENT_DB_ROLE = MappingProxyType(
    {
        "A1": DbRole.IDN_USER_SCOPED,  # 民眾側：只看本人（RLS）
        "A2": DbRole.ANON_READER,  # 機關側：只讀 anon
        "A3": DbRole.ANON_READER,
        "A4": DbRole.ANON_READER,
        "A5": DbRole.IDN_SITE_SCOPED,  # 據點：只看本據點（RLS），二期
    }
)


def agent_engine(agent: str) -> Engine:
    try:
        role = AGENT_DB_ROLE[agent]
    except KeyError:
        raise ValueError(f"未定義的 Agent：{agent}") from None
    return get_engine(role)
