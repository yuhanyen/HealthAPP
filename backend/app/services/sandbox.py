"""A3 分析沙箱（SPEC §7.2）。

- sandbox_sql：唯讀帳號＋k-匿名
- sandbox_python：子行程、無網路、60 秒
一期隔離：子行程＋資源限制＋唯讀 DB 帳號；二期換容器（gVisor 等）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import insert, text

from app.audit.writer import jsonable
from app.db.engines import DbRole, get_engine
from app.db.models_sys import AnalysisQuery
from app.deidentify.k_anon import KAnonResult, execute_k_anonymous
from app.services import artifacts

MAX_ROWS = 5000
SAMPLE_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000
PYTHON_TIMEOUT_S = 60
_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.I)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call|do|"
    r"set|reset|listen|notify|vacuum|analyze|lock|pg_sleep|pg_read|lo_import|dblink)\b",
    re.I,
)


class SandboxRejected(ValueError):
    pass


def validate_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        raise SandboxRejected("只接受單一敘述")
    if not _ALLOWED_START.match(stripped):
        raise SandboxRejected("只接受 SELECT／WITH 查詢")
    if _FORBIDDEN.search(stripped):
        raise SandboxRejected("查詢含不允許的關鍵字")
    return stripped


def next_query_id() -> str:
    with get_engine(DbRole.AUDIT_WRITER).begin() as conn:
        seq = conn.execute(text("SELECT nextval('sys.analysis_query_seq')")).scalar_one()
    return f"Q-{int(seq):04d}"


@dataclass
class SqlResult:
    query_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    masked_rows: int
    k: int
    truncated: bool = False


def run_sql(sql: str, *, k: int, agent: str) -> SqlResult:
    statement = validate_sql(sql)
    wrapped = f"SELECT * FROM ({statement}) AS q LIMIT {MAX_ROWS + 1}"
    with get_engine(DbRole.ANON_READER).connect() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        # 包裝後的 SQL 仍含原敘述，中介軟體照樣能判斷是否為聚合查詢
        probe: KAnonResult = execute_k_anonymous(conn, wrapped, k=k)
        conn.rollback()
    truncated = len(probe.rows) > MAX_ROWS
    rows = probe.rows[:MAX_ROWS]
    query_id = next_query_id()
    with get_engine(DbRole.AUDIT_WRITER).begin() as conn:
        conn.execute(
            insert(AnalysisQuery).values(
                query_id=query_id,
                agent=agent,
                sql=statement,
                row_count=len(rows),
                masked_rows=probe.masked_rows,
            )
        )
    return SqlResult(
        query_id=query_id,
        columns=probe.columns,
        rows=jsonable(rows[:SAMPLE_ROWS]),
        row_count=len(rows),
        masked_rows=probe.masked_rows,
        k=k,
        truncated=truncated,
    )


@dataclass
class PythonResult:
    stdout: str
    stderr: str
    exit_code: int
    artifacts: list[str] = field(default_factory=list)


PRELUDE = """
import socket, sys, json, os
def _blocked(*a, **k):
    raise PermissionError("沙箱禁止網路")
socket.socket = _blocked
socket.create_connection = _blocked
INPUTS = json.load(open(os.environ["SANDBOX_INPUTS"], encoding="utf-8"))
OUT_DIR = os.environ["SANDBOX_OUT"]
"""


def run_python(code: str, inputs: dict[str, Any]) -> PythonResult:
    with tempfile.TemporaryDirectory(prefix="sandbox-") as tmp:
        work = Path(tmp)
        out_dir = work / "out"
        out_dir.mkdir()
        (work / "inputs.json").write_text(
            json.dumps(jsonable(inputs), ensure_ascii=False), encoding="utf-8"
        )
        script = work / "main.py"
        script.write_text(PRELUDE + "\n" + code, encoding="utf-8")
        env = {
            "SANDBOX_INPUTS": str(work / "inputs.json"),
            "SANDBOX_OUT": str(out_dir),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
        try:
            # -I（隔離模式）會忽略 PYTHONIOENCODING；-X utf8 是命令列旗標，隔離模式下仍有效
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", str(script)],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PYTHON_TIMEOUT_S,
            )
            stdout, stderr, code_ = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            stdout, stderr, code_ = "", f"超過 {PYTHON_TIMEOUT_S} 秒，已中止", -1
        saved = []
        for path in sorted(out_dir.iterdir())[:10]:
            ext = path.suffix.lstrip(".").lower()
            if ext in artifacts.MEDIA and path.stat().st_size < 2_000_000:
                saved.append(artifacts.save(path.read_bytes(), ext))
    return PythonResult(
        stdout=stdout[-8000:], stderr=stderr[-4000:], exit_code=code_, artifacts=saved
    )
