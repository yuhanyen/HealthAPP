#!/bin/sh
# 容器啟動（Render 免費方案沒有 shell 與 pre-deploy，所以都在這裡做）：
#   1. REDIS_URL 未設定時，在容器內啟動 Redis（Render 免費方案每個 workspace 只能有 1 個 Key Value）
#   2. 套用 migrations（第一次會建立 schema 與六個 DB 角色）
#   3. SEED_ON_START=1 且資料庫還沒有使用者時，背景載入示範資料（已載入就略過）
#   4. RUN_WORKER=1 時，背景啟動 worker（排程與事件消費者）
#   5. 前景啟動 API（Render 以 $PORT 指定埠號）
set -e
cd /srv/backend

# 沒接到資料庫時給一句看得懂的錯誤，而不是一長串連線 127.0.0.1 失敗的 traceback。
# 映像裡沒有 PostgreSQL，PG_HOST 是 127.0.0.1／localhost（例如整份本機 .env 貼上雲端）一定連不到。
if [ -z "${DATABASE_URL:-}" ]; then
  case "${PG_HOST:-}" in
    "" | 127.0.0.1 | localhost)
      echo "[啟動失敗] 缺少 DATABASE_URL：這個服務沒有接到資料庫（PG_HOST=${PG_HOST:-未設定}）。請用 Blueprint（render.yaml）建立服務，不要貼本機 .env。" >&2
      exit 1
      ;;
  esac
fi

# 只收本機連線、不寫磁碟；設記憶體上限並且不逐出（事件流用 Stream），滿了寫入失敗而不是整個容器 OOM
if [ -z "${REDIS_URL:-}" ]; then
  redis-server --bind 127.0.0.1 --port 6379 --protected-mode yes --save "" --appendonly no \
    --maxmemory 64mb --maxmemory-policy noeviction --loglevel warning &
  tries=0
  until redis-cli -p 6379 ping >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 50 ]; then
      echo "[啟動失敗] 容器內的 Redis 沒有啟動。" >&2
      exit 1
    fi
    sleep 0.2
  done
  export REDIS_URL=redis://127.0.0.1:6379/0
fi

python -m alembic upgrade head

if [ "${SEED_ON_START:-0}" = "1" ]; then
  python -m scripts.seed --population "${SEED_POPULATION:-1200}" --weeks "${SEED_WEEKS:-12}" &
fi

if [ "${RUN_WORKER:-1}" = "1" ]; then
  python -m app.worker &
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="*"
