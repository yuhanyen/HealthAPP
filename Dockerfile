# 健康大數據 App：一個映像包含 API、worker 與四個已建置的前端（Render 與 docker compose 共用）。
# build context = 專案根目錄。啟動流程見 deploy/start.sh。

# --- 1. 前端：四個 app（pnpm workspace），產出 web/apps/*/dist ---
FROM node:22-slim AS web
WORKDIR /srv
RUN corepack enable && corepack prepare pnpm@10.34.7 --activate
COPY i18n/ i18n/
COPY web/ web/
WORKDIR /srv/web
RUN pnpm install --frozen-lockfile && pnpm build

# --- 2. 後端 ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HEALTHAPP_ROOT=/srv

WORKDIR /srv/backend
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY backend/ ./
COPY i18n/ /srv/i18n/
COPY skills/ /srv/skills/
COPY --from=web /srv/web/apps/citizen/dist /srv/web/apps/citizen/dist
COPY --from=web /srv/web/apps/agency/dist /srv/web/apps/agency/dist
COPY --from=web /srv/web/apps/site/dist /srv/web/apps/site/dist
COPY --from=web /srv/web/apps/merchant/dist /srv/web/apps/merchant/dist
COPY deploy/start.sh /srv/start.sh

EXPOSE 8000
CMD ["sh", "/srv/start.sh"]
