# 健康大數據 App — 常用指令（Linux／macOS／Docker）。Windows 請用 .\make.ps1 <target>。
PY      ?= python
BACKEND := backend

.PHONY: help setup infra infra-stop up down migrate seed reset api worker web web-install web-build perf test check-schema lint

help:
	@echo "setup infra up down migrate seed reset api worker web test check-schema lint"

setup:          ## 安裝後端相依套件
	$(PY) -m pip install -e "$(BACKEND)[dev]"

infra:          ## 不用 Docker：本機啟動 PostgreSQL 16 + Redis
	cd $(BACKEND) && $(PY) -m scripts.localinfra start

infra-stop:
	cd $(BACKEND) && $(PY) -m scripts.localinfra stop

up:             ## Docker：啟動 db、redis
	docker compose up -d db redis

down:
	docker compose down

migrate:        ## alembic upgrade head
	cd $(BACKEND) && alembic upgrade head

seed:           ## 載入示範資料（10 里、50 民眾、6 據點、12 商家）
	cd $(BACKEND) && $(PY) -m scripts.seed

reset:          ## 刪庫重建 + migrate + seed
	cd $(BACKEND) && $(PY) -m scripts.seed --reset

api:
	cd $(BACKEND) && uvicorn app.main:app --reload --port 8000

worker:         ## 事件消費者 + 排程
	cd $(BACKEND) && $(PY) -m app.worker

perf:           ## 效能量測（SPEC §11），輸出 docs/perf-report.md
	cd $(BACKEND) && $(PY) -m scripts.perf

web-install:
	cd web && pnpm install

web-build:
	cd web && pnpm build

web:
	cd web && pnpm dev

check-schema:   ## C-1 靜態掃描 idn/anon 跨 schema
	cd $(BACKEND) && $(PY) -m scripts.check_schema

test: check-schema  ## pytest（含 tests/constraints/ 八條約束）
	cd $(BACKEND) && $(PY) -m pytest

lint:
	cd $(BACKEND) && ruff check . && ruff format --check . && mypy app scripts tests
