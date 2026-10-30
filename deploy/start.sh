#!/bin/sh
# 容器啟動（Render 免費方案沒有 shell 與 pre-deploy，所以都在這裡做）：
#   1. 套用 migrations（第一次會建立 schema 與六個 DB 角色）
#   2. SEED_ON_START=1 且資料庫還沒有使用者時，背景載入示範資料（已載入就略過）
#   3. RUN_WORKER=1 時，背景啟動 worker（排程與事件消費者）
#   4. 前景啟動 API（Render 以 $PORT 指定埠號）
set -e
cd /srv/backend

python -m alembic upgrade head

if [ "${SEED_ON_START:-0}" = "1" ]; then
  python -m scripts.seed --population "${SEED_POPULATION:-1200}" --weeks "${SEED_WEEKS:-12}" &
fi

if [ "${RUN_WORKER:-1}" = "1" ]; then
  python -m app.worker &
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="*"
