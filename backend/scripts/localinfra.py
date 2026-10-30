"""無 Docker 時的本機基礎設施：PostgreSQL 16（pgserver 套件內附的執行檔）＋ Redis。

資料目錄放在 ~/.healthapp/（純 ASCII 路徑、不在 OneDrive 同步範圍）。
用法：python -m scripts.localinfra start|stop|status
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
import redis
from psycopg import sql

from app.config import get_settings

HOME = Path.home() / ".healthapp"
PG_DATA = HOME / "pgdata"
PG_LOG = HOME / "postgres.log"
REDIS_DATA = HOME / "redis" / "data"


def _pg_bin(name: str) -> str:
    exe = f"{name}.exe" if os.name == "nt" else name
    try:
        import pgserver

        candidate = Path(pgserver.__file__).parent / "pginstall" / "bin" / exe
        if candidate.exists():
            return str(candidate)
    except ImportError:
        pass
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"找不到 {name}；請 pip install pgserver 或安裝 PostgreSQL 16")
    return found


def _redis_bin() -> str | None:
    exe = "redis-server.exe" if os.name == "nt" else "redis-server"
    local = HOME / "redis" / "bin" / exe
    if local.exists():
        return str(local)
    return shutil.which("redis-server")


def _redis_port() -> int:
    pool = redis.Redis.from_url(get_settings().redis_url).connection_pool
    return int(pool.connection_kwargs.get("port", 6379))


# 背景常駐：Windows 脫離主控台、POSIX 另開 session，關掉終端機也不會被一起結束
DETACHED_FLAGS = (
    getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if os.name == "nt"
    else 0
)
NEW_SESSION = os.name != "nt"


def ensure_timezone_data() -> None:
    """pgserver 的 Windows 版沒附時區資料庫（share/postgresql/timezone），
    `AT TIME ZONE 'Asia/Taipei'` 會失敗。
    Python 的 tzdata 套件內含同格式（TZif）的檔案，複製過去即可。"""
    try:
        import pgserver
        import tzdata
    except ImportError:
        return
    target = Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
    if (target / "Asia" / "Taipei").exists():
        return
    source = Path(tzdata.__file__).parent / "zoneinfo"
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__init__.py", "__pycache__"),
    )
    print(f"已安裝 PostgreSQL 時區資料：{target}")


def pg_running() -> bool:
    if not (PG_DATA / "PG_VERSION").exists():
        return False
    res = subprocess.run(
        [_pg_bin("pg_ctl"), "-D", str(PG_DATA), "status"], capture_output=True, text=True
    )
    return res.returncode == 0


def start_postgres() -> None:
    s = get_settings()
    HOME.mkdir(parents=True, exist_ok=True)
    ensure_timezone_data()
    if not (PG_DATA / "PG_VERSION").exists():
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
            fh.write(s.pg_superpass)
            pwfile = fh.name
        try:
            subprocess.run(
                [
                    _pg_bin("initdb"),
                    "-D",
                    str(PG_DATA),
                    "-U",
                    s.pg_superuser,
                    "--pwfile",
                    pwfile,
                    "--auth-host=scram-sha-256",
                    "--auth-local=scram-sha-256",
                    "-E",
                    "UTF8",
                    "--locale=C",
                ],
                check=True,
                capture_output=True,
            )
        finally:
            os.unlink(pwfile)
        print(f"已初始化 PostgreSQL 資料目錄：{PG_DATA}")
    if not pg_running():
        subprocess.run(
            [
                _pg_bin("pg_ctl"),
                "-D",
                str(PG_DATA),
                "-l",
                str(PG_LOG),
                "-o",
                f"-p {s.pg_port} -c listen_addresses=127.0.0.1",
                "-w",
                "start",
            ],
            check=True,
            # postgres 子行程會繼承管線；用 DEVNULL 避免 pg_ctl 等不到管線關閉而卡住
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_FLAGS,
            start_new_session=NEW_SESSION,
        )
    print(f"PostgreSQL 執行中：127.0.0.1:{s.pg_port}")
    ensure_database(s.pg_db)


def ensure_database(name: str) -> None:
    s = get_settings()
    conninfo = (
        f"host={s.pg_host} port={s.pg_port} dbname=postgres "
        f"user={s.pg_superuser} password={s.pg_superpass}"
    )
    with psycopg.connect(conninfo, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"已建立資料庫 {name}")


def stop_postgres() -> None:
    if pg_running():
        subprocess.run([_pg_bin("pg_ctl"), "-D", str(PG_DATA), "-m", "fast", "stop"], check=True)
    print("PostgreSQL 已停止")


def _redis_client() -> redis.Redis:
    # protocol=2：Windows 版 Redis 5 不支援 RESP3 的 HELLO 指令
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=1, protocol=2)


def redis_running() -> bool:
    try:
        return bool(_redis_client().ping())
    except redis.RedisError:
        return False


def start_redis() -> None:
    if redis_running():
        print(f"Redis 執行中：{get_settings().redis_url}")
        return
    binary = _redis_bin()
    if binary is None:
        print("[MOCK] 找不到 redis-server；事件匯流將以 EVENT_BUS=memory（fakeredis）運作")
        return
    REDIS_DATA.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            binary,
            "--port",
            str(_redis_port()),
            "--bind",
            "127.0.0.1",
            "--protected-mode",
            "yes",
            "--dir",
            str(REDIS_DATA),
            "--appendonly",
            "yes",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_FLAGS,
        start_new_session=NEW_SESSION,
        close_fds=True,
    )
    for _ in range(20):
        if redis_running():
            print(f"Redis 執行中：{get_settings().redis_url}")
            return
        time.sleep(0.25)
    raise SystemExit("Redis 啟動失敗")


def stop_redis() -> None:
    if redis_running():
        with contextlib.suppress(redis.ConnectionError):  # shutdown 會直接斷線
            _redis_client().shutdown(nosave=False)
    print("Redis 已停止")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "start":
        start_postgres()
        start_redis()
    elif cmd == "stop":
        stop_redis()
        stop_postgres()
    elif cmd == "status":
        print(f"PostgreSQL：{'執行中' if pg_running() else '未啟動'}")
        print(f"Redis：{'執行中' if redis_running() else '未啟動'}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
