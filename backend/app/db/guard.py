"""C-1：同一個 SQL 敘述不得同時引用 idn 與 anon。

三道防線：
1. 本模組在每個應用 engine 的 before_cursor_execute 攔截（含 ORM 產生的 SQL）。
2. scripts/check_schema.py 以同一套偵測邏輯靜態掃描原始碼（make check-schema）。
3. DB 權限：沒有任何非超級使用者角色同時擁有兩區的 USAGE（migration 0006）。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from app.i18n import t

PROTECTED_PAIR = frozenset({"idn", "anon"})

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SCHEMA_REF = re.compile(r'(?<![\w.$])"?(idn|anon)"?\s*\.\s*"?[A-Za-z_]', re.I)
_SEARCH_PATH = re.compile(r"search_path", re.I)
_BARE_SCHEMA = re.compile(r"\b(idn|anon)\b", re.I)


class CrossSchemaQueryError(RuntimeError):
    def __init__(self, statement: str) -> None:
        super().__init__(f"{t('db.error.cross_schema')}：{statement[:200]}")
        self.statement = statement


def _strip_noise(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return _STRING_LITERAL.sub("''", sql)


def referenced_schemas(sql: str) -> set[str]:
    """回傳敘述中以 `schema.物件` 形式引用到的受保護 schema。"""
    return {m.group(1).lower() for m in _SCHEMA_REF.finditer(_strip_noise(sql))}


def is_cross_schema(sql: str) -> bool:
    if referenced_schemas(sql) >= PROTECTED_PAIR:
        return True
    # 以 search_path 同時掛上兩區、再用不帶 schema 的表名 JOIN，也視為違規。
    # search_path 常以字串常值設定（set_config('search_path', 'idn, anon', …)），故掃原文。
    if _SEARCH_PATH.search(sql):
        return {m.group(1).lower() for m in _BARE_SCHEMA.finditer(sql)} >= PROTECTED_PAIR
    return False


def assert_no_cross_schema(sql: str) -> None:
    if is_cross_schema(sql):
        raise CrossSchemaQueryError(sql)


def _before_cursor_execute(
    conn: Connection,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    assert_no_cross_schema(statement)


def install_guard(engine: Engine) -> None:
    if not event.contains(engine, "before_cursor_execute", _before_cursor_execute):
        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
