# CLAUDE.md — 健康大數據 App

## 專案是什麼
健康大數據 App 一期 PoC：六個資料來源 → 健康資料中台（可識別區／去識別化區）→ 工具層 → 五個 Agent → 五種使用端。完整規格在 `SPEC.md`，Agent 行為在 `skills/*/SKILL.md`。

## 開始任何工作前
1. 讀 `SPEC.md` 對應章節；規格未定義的事依 §13 預設決策，並在 `docs/DECISIONS.md` 追加一行（日期、決定、理由）。
2. 目前里程碑看 `docs/DECISIONS.md` 最上方的「現在在做」。

## 不可違反的約束（SPEC §2.2，違反即為缺陷）
- `idn` 與 `anon` 兩個 schema 之間**不得有任何 JOIN**；資料只能經 `backend/app/deidentify/` 單向流動。
- 機關側 Agent（A2、A3、A4）只能用 `anon_reader` 連線；民眾側 A1 用 `idn_user_scoped`，每次工具呼叫帶 `consent_scope`。
- 聚合查詢列對應人數 < k（預設 5）必須遮蔽，由 `deidentify/k_anon.py` 中介軟體處理，不在個別工具重寫。
- 高風險動作（預約、分享、兌換、撤回授權）走確認狀態機，`confirmed=true` 才執行。
- 每次 Agent 工具呼叫與每次代號解碼寫 `sys.audit_log`（append-only）。
- A1 輸出經 `agents/output_filter.py` 黑名單過濾，不得出現疾病斷言。
- 輿情資料在 `adapters/sentiment.py` 落地前先 `strip_identity()`。

## 程式慣例
- Python 3.12、FastAPI、SQLAlchemy 2.x、Alembic；型別註記必填；`ruff` + `mypy --strict`。
- 前端 React + Vite + TS；共用元件在 `web/packages/ui`；民眾 App 最小字級 18px。
- 工具層每個工具一檔，放 `backend/app/tools/`，以 `@registry.tool(schema=…)` 註冊，附 pytest。
- 外部 API 一律經 Adapter 介面；無金鑰時自動用 `adapters/mocks/`，並在日誌標 `[MOCK]`。
- 所有使用者可見文字放 `i18n/zh-TW.json`，繁體中文。
- Agent system prompt 直接讀 `skills/<agent>/SKILL.md`，不複製內容到程式碼。
- commit 訊息格式：`<scope>: <摘要>`，scope ∈ {db, consent, adapters, tools, agents, api, web, docs}。

## 常用指令
```
docker compose up -d db redis
make migrate          # alembic upgrade head
make seed             # 載入 fixtures（10 個里、50 位 Mock 使用者、6 個據點、18 家商家、近兩週活動）
make api              # uvicorn --reload
make worker           # 排程與事件消費者
make web              # pnpm dev（四個 app）
make test             # pytest + 約束測試（constraints/）
make check-schema     # 靜態掃描 idn/anon 跨 schema JOIN
```

## 測試要求
- 每個里程碑結束前 `make test` 全綠，且 `tests/constraints/` 八條約束測試（SPEC §15-8）通過。
- 新工具必附：正常輸入、範圍外 `consent_scope`、k 遮蔽三種測試。

## 不要做的事
- 不要為了方便在 `anon` 表加 `user_id` 或任何可反查欄位。
- 不要在工具層或 Agent 裡直接寫 SQL 跨 schema；用既有 service。
- 不要讓 A1 在未確認時執行預約、分享、兌換、撤回。
- 不要在撤回授權流程加任何「確定要撤回嗎？撤回後您將失去…」以外的文案。
- 不要引入重型 Agent 框架；runtime 在 `agents/runtime.py` 自建並可稽核。
