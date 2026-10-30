# build context = 專案根目錄（需要 i18n/ 與 skills/）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /srv/backend

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY backend/ ./
COPY i18n/ /srv/i18n/
COPY skills/ /srv/skills/

ENV HEALTHAPP_ROOT=/srv

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
