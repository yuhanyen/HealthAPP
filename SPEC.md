# 健康大數據 App — 系統規格書

版本 0.1（2026-10-29）｜狀態：開發起始版｜用途：供 Claude Code 依本文件建置一期 PoC

---

## 0. 給 Claude Code 的閱讀指引

- 本文件是唯一規格來源。遇到規格未定義的細節，依「§13 預設決策」處理，並在 `docs/DECISIONS.md` 追加一筆。
- 所有外部介接（健保署健康存摺、HealthKit、Google Places、氣象署）在一期一律以 **Adapter 介面 + Mock 實作**完成；真實串接留 TODO 並標明對口單位。
- 五個 Agent 的行為規範在 `skills/a1…a5/SKILL.md`，程式碼中的 system prompt 直接載入該檔，不另外複製。
- 「可識別區」與「去識別化區」是兩個不同的資料庫 schema，程式層面不得有任何跨 schema 的 JOIN。這是本系統最重要的約束，違反即為缺陷。

---

## 1. 專案概述

### 1.1 目標

建置一個健康大數據 App 的一期可運作原型（PoC）：

1. 收集手機／穿戴步數等活動資料、社區據點量測資料（血壓、身高體重、體脂、血糖）、健保署健康存摺就醫紀錄
2. 每日 05:00 自動產生個人健康報告，07:00 推送，並依就醫紀錄與環境給運動提醒
3. 運動達標給予點數，可在在地特約商家兌換
4. 以五個 Agent（A1–A5）作為應用面的核心，機關側只讀去識別化資料
5. 提供公衛治理儀表板

### 1.2 範圍

| 一期（本規格） | 二期（不在本規格） |
|---|---|
| 六個資料來源的 Adapter（Mock 實作） | 真實串接健保署 SDK、HealthKit、Health Connect |
| 資料中台：介接閘門、同意管理、FHIR 庫、點數帳本、事件匯流、去識別化引擎、群體倉儲、輿情語料庫 | 異地備援、正式資安稽核 |
| 工具層：6 組工具 | 分析沙箱的強隔離（gVisor 等） |
| Agent：A1、A2、A3、A4（A5 只做介面與 stub） | A5 據點營運 Agent 完整功能 |
| 使用端：民眾 App（Web/PWA）、公衛儀表板（Web）、據點人員端（Web 簡版）、商家端（Web 簡版） | 原生 iOS／Android App、家庭守護圈獨立介面 |
| 推播：Web Push + LINE Notify（可選） | 語音 TTS／STT |

### 1.3 使用者角色

| 角色 | 介面 | 可見資料 |
|---|---|---|
| 民眾（本人） | 民眾 App | 本人全部資料（依授權） |
| 授權家屬 | 民眾 App（守護圈分頁） | 被照顧者摘要層 |
| 據點人員 | 據點人員端 | 本據點預約、代量測清單、關懷代號解碼 |
| 志工 | 據點人員端（任務視圖） | 任務單（無健康資料） |
| 商家 | 商家端 | 自家優惠、核銷、對帳 |
| 衛生局／衛生所承辦 | 公衛儀表板 | 去識別化群體統計、A2 對話 |
| 系統管理者 | Admin | 設定、稽核日誌、k 值、來源白名單 |

---

## 2. 系統架構

### 2.1 六層架構

```
L1 資料來源   手機穿戴 | 社區據點 | 健保署健康存摺 | 外部環境 | 在地商家 | 公開輿情
      ↓
L2 介接與同意  API Gateway + Adapters + 同意管理 + 身分驗證 + 推播 + 稽核   ┐
      ↓                                                                 ├ 健康資料中台
L3 資料平台   [可識別區] FHIR庫 + 點數帳本 | 事件匯流 | [去識別化區] 群體倉儲 + 輿情語料 ┘
      ↓
L4 工具層     daily_report | risk_score | exercise_rx | site_booking | points_engine | merchant_lookup
              cohort_analysis | care_rules | resource_sim | sandbox_sql/python | nlp_* | consent_mgmt
      ↓
L5 Agent      民眾側: A1 個人數位雙生 (→A5 據點營運, 二期)
              機關側: A2 公衛 (編排) → A3 數據分析, A4 社區輿情        ← 兩側無資料流
      ↓
L6 使用端     民眾App | 家庭守護圈 | 據點人員端 | 商家端 | 公衛儀表板
```

### 2.2 核心約束

| 編號 | 約束 | 驗證方式 |
|---|---|---|
| C-1 | 可識別區（`idn` schema）與去識別化區（`anon` schema）之間無任何 JOIN；資料只能經 `deidentify` 服務單向流動 | 靜態掃描 SQL；整合測試 |
| C-2 | 機關側 Agent（A2、A3、A4）的資料庫連線帳號只有 `anon` schema 權限 | 連線測試 |
| C-3 | 民眾側 Agent（A1）每次工具呼叫附 `consent_scope`，工具拒絕範圍外請求 | 單元測試 |
| C-4 | 任何回傳聚合結果的查詢，列對應人數 < k（預設 5）即遮蔽 | 查詢層中介軟體 + 測試 |
| C-5 | 高風險動作（預約、分享、兌換、撤回授權）需本人明確確認後才執行 | 狀態機測試 |
| C-6 | 每次 Agent 工具呼叫與每次代號解碼寫入稽核日誌（append-only） | 稽核表 trigger |
| C-7 | A1 輸出不得含疾病斷言詞（黑名單）；用詞停在健康促進 | 輸出過濾器 + 測試 |
| C-8 | 輿情採集於落地前去除帳號、名稱、頭像 | Adapter 單元測試 |

---

## 3. 技術選型（一期預設，可替換）

| 層 | 選型 | 理由 |
|---|---|---|
| 語言 | Python 3.12（後端、Agent、工具）；TypeScript（前端） | 資料與 LLM 生態 |
| 後端框架 | FastAPI | 自動 OpenAPI、非同步 |
| 資料庫 | PostgreSQL 16，兩個 schema：`idn`、`anon`；FHIR 資源以 JSONB 存於 `idn.fhir_resources` | 一期不引入獨立 FHIR server，降低複雜度 |
| 事件匯流 | Redis Streams | 輕量；二期可換 Kafka |
| 排程 | APScheduler（進程內）或 cron | 05:00 批次、每小時同步、每 6 小時爬蟲 |
| LLM | Anthropic Claude API（Messages + tool use） | Agent 與白話化 |
| Agent 框架 | 自建輕量 loop（`agents/runtime.py`），不引入重型框架 | 可控、可稽核 |
| 分析沙箱 | 子進程 + 資源限制 + 唯讀 DB 帳號（一期）；二期換容器隔離 | |
| 前端 | React + Vite + TypeScript；PWA | 一套程式碼，手機可安裝 |
| 推播 | Web Push（VAPID）；LINE Notify 為可選 | |
| 身分驗證 | OAuth 2.0 / OIDC（一期用 Keycloak 或自建簡版）；TW FidO 留介面 | |
| 部署 | Docker Compose（db、redis、api、worker、web） | 一指令啟動 |
| 測試 | pytest、Playwright | |

---

## 4. 資料模型

### 4.1 可識別區 `idn`

```sql
-- 使用者與授權
idn.users            (id uuid pk, national_id_hash text unique, name text, birth_date date, sex text, phone text, elder_mode bool, created_at)
idn.consents         (id uuid pk, user_id fk, source text, scope jsonb, granted_at, revoked_at null, version int)
                     -- source ∈ {wearable, site, nhi, env, merchant}; scope 例 {"read":["steps","bp","visits"],"share_summary_to":["<family_user_id>"],"allow_proxy_redeem":true}
idn.family_links     (id, owner_user_id fk, viewer_user_id fk, level text /* summary */, granted_at, revoked_at)

-- FHIR 資源（JSONB）
idn.fhir_resources   (id uuid pk, user_id fk, resource_type text, resource jsonb, source text, effective_at timestamptz, created_at, unique(user_id, resource_type, source, effective_at))
                     -- resource_type ∈ {Observation, Condition, MedicationStatement, Encounter, Immunization, AllergyIntolerance}

-- 點數帳本（append-only）
idn.points_ledger    (id bigserial pk, user_id fk, delta int, reason text, ref_type text, ref_id text, balance_after int, created_at)
idn.redemptions      (id uuid pk, user_id fk, merchant_id fk, offer_id fk, points int, qr_token text, status text, redeemed_at, verified_at)

-- 預約與任務
idn.bookings         (id uuid pk, user_id fk, site_id fk, slot_at timestamptz, purpose text, status text, created_by text /* user|a1 */)
idn.care_tasks       (id uuid pk, code text unique /* N-0417 */, user_id fk, site_id fk, task_type text, rule_id text, status text, created_at)
idn.decode_audit     (id bigserial, care_task_code text, decoded_by_user fk, site_id fk, task_ref text, at timestamptz)

-- 雙生快照與報告
idn.twin_snapshots   (id, user_id fk, as_of date, state jsonb, created_at)   -- 今日狀態、風險、偏好
idn.daily_reports    (id, user_id fk, report_date date, content jsonb, delivered_at, opened_at)
```

### 4.2 去識別化區 `anon`

```sql
anon.cohort_weekly   (week date, village_code text, age_band text, sex text, metric text, value numeric, n int)
                     -- metric ∈ {steps_avg, bp_uncontrolled_rate, measure_freq, site_usage, redeem_rate, risk_l2_rate ...}
anon.site_ops_weekly (week, site_id, visits int, bookings int, no_show int)
anon.merchant_weekly (week, merchant_id, redemptions int, points int)
anon.visit_outcomes  (week, village_code, task_type, reason_class text, n int)
anon.sentiment_items (id, collected_at, source_class text, source_url text, village_code null, topic text, sentiment text, intensity numeric, summary text)
anon.sentiment_alerts(id, village_code, topic, category, count int, baseline numeric, ratio numeric, created_at, status)
anon.app_metrics_daily (day, dau int, mau int, retention_7 numeric, retention_30 numeric, push_open_rate numeric, elder_mode_rate numeric)
```

### 4.3 共用（非個資）

```sql
pub.sites            (id, name, type, village_code, lat, lng, hours jsonb, equipment jsonb, active bool)
pub.merchants        (id, name, category, village_code, lat, lng, hours jsonb, api_key_hash text, active bool)
pub.offers           (id, merchant_id, title, points_cost int, stock int, valid_from, valid_to)
pub.villages         (code pk, name, district, population int, geom)
pub.env_hourly       (hour timestamptz, district, temp numeric, heat_index numeric, aqi int, rain bool)
pub.source_whitelist (id, source_class, url_pattern, approved_by, approved_at, active)
sys.audit_log        (id bigserial, at, actor_type text /* user|agent|admin */, actor_id, action text, tool text, consent_scope jsonb, input_digest text, output_digest text, decision text, reason text)
sys.events           -- Redis Streams，非 SQL
```

### 4.4 去識別化規則

- `deidentify` 服務每日 05:00 由 `idn` 聚合寫入 `anon.cohort_weekly`；聚合鍵為（週、里、年齡層 10 歲一組、性別）。
- 任何聚合列 `n < k` 不寫入（k 由 `sys.config.k` 控制，預設 5）。
- 輿情 Adapter 在落地前執行 `strip_identity()`：移除帳號、顯示名稱、頭像 URL、@ 提及、電話、Email。
- `anon` 任何表不得含 `user_id`、`national_id_hash`、姓名、地址。

---

## 5. 資料來源 Adapter（L2）

所有 Adapter 實作 `adapters/base.py` 的介面：

```python
class SourceAdapter(Protocol):
    source: str                       # wearable | site | nhi | env | merchant | sentiment
    def fetch(self, since: datetime, ctx: FetchContext) -> list[RawRecord]: ...
    def normalize(self, raw: RawRecord) -> list[FhirResource] | list[AnonRecord]: ...
    def schedule(self) -> str         # cron 表達式
```

| Adapter | 一期實作 | 頻率 | 需授權 | 備註／對口 |
|---|---|---|---|---|
| `WearableAdapter` | Mock：從 `fixtures/wearable_*.json` 產生每小時步數、心率 | 每小時 | 是（wearable） | 真實：HealthKit／Health Connect，二期 |
| `SiteAdapter` | 真實：據點人員端表單 + 模擬藍牙上傳 API `POST /sites/{id}/measurements` | 即時 | 是（site） | 衛生局／衛生所 |
| `NhiAdapter` | Mock：`fixtures/nhi_bundle_*.json`（FHIR Bundle，TW Core IG 格式） | 每日 05:00 | 是（nhi）；撤回即停止並刪除 | 健保署資訊組、數發部 MyData |
| `EnvAdapter` | 真實可行：氣象署／環境部開放資料；無金鑰則 Mock | 每小時 | 否 | |
| `MerchantAdapter` | 真實：商家端 API | 即時 | 商家 API key | 經發局、商圈協會 |
| `SentimentAdapter` | Mock 來源 + 真實 Google Places 評論（有金鑰時）；內建 `strip_identity()` | 每 6 小時 | 否；來源須在 `pub.source_whitelist` | 法制審 |

### 5.1 同意管理 API

```
GET    /consents                     本人目前授權
POST   /consents                     {source, scope}  建立／更新
DELETE /consents/{id}                撤回 → 立即停止該來源拉取；排程刪除該來源資料（保留稽核）
GET    /consents/explain/{source}    白話說明該授權會讓誰看到什麼（供 A1 授權管家使用）
```

撤回流程：寫 `revoked_at` → 發事件 `consent.revoked` → Adapter 停止 → 刪除任務 24h 內執行 → 稽核記錄保留。

---

## 6. 事件匯流

Redis Streams，stream 名稱 `events`，訊息格式：

```json
{"type":"measurement.created","user_id":"…","source":"site","resource_id":"…","at":"…"}
```

| 事件 | 產生者 | 消費者 |
|---|---|---|
| `measurement.created` | 任一 Adapter | A1（即時異常檢查） |
| `nhi.updated` | NhiAdapter | A1 |
| `daily.batch` | 排程 05:00 | A1（全體使用者）、deidentify、A2 關懷清單 |
| `goal.achieved` | points_engine | A1 |
| `consent.revoked` | 同意管理 | Adapter、A1 |
| `sentiment.alert` | A4 | A2 |
| `care.list.ready` | A2 | 據點人員端／A5 |
| `redeem.verified` | 商家 API | points_engine |

---

## 7. 工具層（L4）

每個工具是一個純函式／服務，**明確輸入輸出、可單獨測試、可被排程或 Agent 呼叫**。工具透過 `tools/registry.py` 註冊，並以 JSON Schema 暴露給 Agent runtime。

### 7.1 民眾側工具（需 `consent_scope`）

| 工具 | 輸入 | 輸出 | 規則摘要 |
|---|---|---|---|
| `daily_report` | user_id, date, scope | 指標表（今日／上週／同齡）、白話解讀、語音稿 | 同齡比較只用 `anon.cohort_weekly`；用詞經黑名單過濾 |
| `risk_score` | user_id, scope | level ∈ {L0,L1,L2,L3}, triggers[] | 規則：血壓連續 3 次 ≥140/90 → L2；步數 14 日均值較前期降 ≥40% → L1；L2+未回診 30 日 → L3。單筆不觸發，需連續性 |
| `exercise_rx` | user_id, scope, env | intensity, minutes, venue, contraindications | 有 Condition（心血管、糖尿病、腎病）→ 保守版；AQI>100 或熱指數>32 → 室內 |
| `site_booking` | user_id, site_id?, purpose | slots[] / booking_id | `mode=query` 只查；`mode=book` 需 `confirmed=true` |
| `points_engine` | user_id, action | balance, expiring[], granted | 達標：步數 ≥ 個人目標（預設 6000，依處方調整）；防作弊：單小時 >3000 步或日 >40000 步標記待審；地理圍欄驗證據點打卡 |
| `merchant_lookup` | lat,lng, radius, points | merchants[], offers[] | |
| `consent_mgmt` | user_id, action | scope / explanation / revoke result | 撤回不得附加任何挽留文案 |
| `notify` | user_id, channel, payload | status | 07:00 推送；異常即時 |

### 7.2 機關側工具（只連 `anon`）

| 工具 | 輸入 | 輸出 |
|---|---|---|
| `cohort_analysis` | village_codes[], metrics[], weeks | 時序表、熱區（GeoJSON） |
| `care_rules` | date | care_tasks[]（代號、任務類型、規則編號；無健康內容）— 規則在 `idn` 執行，**只輸出代號**到 `anon` 事件 |
| `resource_sim` | candidate_sites[], radius | 覆蓋率變化、假設清單 |
| `sandbox_sql` | sql | rows, query_id, masked_rows | 唯讀帳號；k-匿名中介軟體 |
| `sandbox_python` | code, inputs | stdout, artifacts[] | 子進程、timeout 60s、無網路 |
| `chart_render` | spec | png/svg path | |
| `report_build` | sections, appendix | html path | |
| `nlp_sentiment` / `nlp_topic` / `nlp_entity` | texts[] | labels | 一期可用 Claude API 批次完成 |
| `trend_detect` | topic, village, window | ratio, is_alert | 基線 4 週均值，閾值 3 倍且來源多樣性 ≥ 2 |
| `briefing_pack` | topic, facts[], stance | 談參 markdown | 事實／立場分段 |

### 7.3 據點側工具（A5，一期 stub）

`site_schedule`、`decode_care_list`（必附 task_ref，寫 `idn.decode_audit`）、`dispatch`、`visit_guide`、`visit_report`、`equipment`、`event_mgmt`。一期實作 `decode_care_list` 與 `visit_guide`，其餘回傳 `NotImplemented` 並記錄。

---

## 8. Agent 層（L5）

### 8.1 Runtime

`agents/runtime.py`：

```
loop:
  messages ← system(SKILL.md) + context + history
  resp ← claude.messages.create(tools=registry.schemas(agent.allowed_tools))
  for tool_use in resp:
      assert tool in agent.allowed_tools
      assert scope_check(agent, tool, args)        # C-2 / C-3
      result ← registry.call(tool, args)
      audit.write(agent, tool, args, result)       # C-6
      if tool in HIGH_RISK and not args.confirmed: return ask_user(...)   # C-5
  until stop
output ← output_filter(resp)                        # C-7（A1）
```

### 8.2 Agent 定義

| Agent | SKILL | DB 帳號 | allowed_tools | 觸發 |
|---|---|---|---|---|
| A1 個人數位雙生 | `skills/a1-personal-digital-twin/SKILL.md` | `idn_user_scoped` | 7.1 全部 | `daily.batch`、`measurement.created`、`nhi.updated`、對話 |
| A2 公衛 | `skills/a2-public-health-orchestrator/SKILL.md` | `anon_reader` | cohort_analysis, care_rules, resource_sim, briefing_pack, delegate(A3), delegate(A4) | 對話、`daily.batch`、`sentiment.alert` |
| A3 數據分析 | `skills/a3-data-analysis/SKILL.md` | `anon_reader` | sandbox_sql, sandbox_python, chart_render, report_build, schema_catalog | A2 委派、排程 |
| A4 社區輿情 | `skills/a4-community-sentiment/SKILL.md` | `anon_reader` + 爬蟲 | crawl_schedule, deidentify, nlp_*, trend_detect, alert_emit | 每 6 小時、A2 委派 |
| A5 據點營運 | `skills/a5-site-operations/SKILL.md` | `idn_site_scoped` | 7.3 | 二期（一期 stub） |

### 8.3 委派協定（A2 → A3／A4）

```json
{"task_id":"…","from":"A2","to":"A3","spec":{"villages":[…],"metrics":[…],"weeks":12,"outputs":["line","corr_matrix"]}}
→ {"task_id":"…","findings":[{"claim":"…","evidence":"Q-002,fig1","confidence":"high"}],"queries":[…],"charts":[…],"data_cutoff":"…","k":5,"masked_rows":0}
```

A2 驗證：`queries` 非空（A3）／每個 item 有 `source_url`（A4），否則退回。

### 8.4 雙生（A1 狀態模型）

`idn.twin_snapshots.state`：

```json
{"as_of":"2026-10-29","vitals":{"bp_last3":[[142,91],[145,93],[139,90]],"steps_14d_avg":3200,"steps_prev14d_avg":5100},
 "conditions":["I10"],"medications":[…],"risk":{"level":"L2","triggers":["bp_consecutive_high","steps_drop"]},
 "prefs":{"elder_mode":true,"channel":"line","exercise_time":"morning"},
 "consent_scope":{…},"last_dialog_summary":"…"}
```

每日批次先更新快照，再執行 SKILL 流程；對話時讀最新快照。

---

## 9. API 規格（主要端點）

基底 `/api/v1`，JSON，Bearer token。完整 OpenAPI 由 FastAPI 產生。

### 民眾
```
GET  /me/twin                       雙生快照（本人）
GET  /me/reports?date=              每日報告
POST /me/chat                       {message} → A1 回覆（SSE 串流）
POST /me/actions/{action_id}/confirm  確認高風險動作
GET  /me/points                     餘額、到期、流水
POST /me/redeem                     {offer_id} → qr_token
GET  /me/family                     守護圈設定
POST /me/family                     {viewer_user_id, level:"summary"}
GET  /family/{owner_id}/summary     家屬視角（只回摘要層）
GET/POST/DELETE /consents           §5.1
```

### 據點
```
GET  /sites/{id}/today              今日工作板
POST /sites/{id}/measurements       量測上傳 {user_qr, items:{bp:[..],weight:..}}
GET  /sites/{id}/care-tasks         代號清單
POST /sites/{id}/care-tasks/{code}/decode   {task_ref} → 姓名、聯絡、訪綱（寫稽核）
POST /sites/{id}/care-tasks/{code}/report   訪視回報
```

### 商家
```
POST /merchants/{id}/offers
POST /merchants/{id}/verify         {qr_token} → 核銷
GET  /merchants/{id}/report?month=  成效（聚合）
```

### 機關
```
POST /agency/chat                   {message} → A2（SSE）
GET  /agency/cohort?metric=&weeks=  群體統計
GET  /agency/alerts                 輿情警訊
GET  /agency/kpi?period=
POST /agency/briefing               {occasion, topic} → 談參
```

### 系統
```
GET  /admin/audit?actor=&since=
GET/PUT /admin/config               k、黑名單、白名單
```

---

## 10. 使用端

| 端 | 技術 | 必要畫面（一期） |
|---|---|---|
| 民眾 App（PWA） | React | 登入／授權引導、今日報告、與 A1 對話（含確認卡片）、點數與兌換 QR、守護圈、授權管理（含撤回）、長者模式切換（大字、高對比、語音播報 stub） |
| 公衛儀表板 | React + 地圖（MapLibre） | 熱區地圖、KPI 卡、趨勢圖、輿情警訊列表、A2 對話框、談參產生 |
| 據點人員端 | React（簡版） | 今日工作板、掃 QR 代量測表單、關懷代號清單與解碼（需輸入任務編號）、訪視回報 |
| 商家端 | React（簡版） | 優惠上架、掃碼核銷、月成效 |

UI 原則：民眾 App 以長者可用為基準（最小字級 18px、單手操作、每頁一個主要動作）。

---

## 11. 非功能需求

| 項目 | 需求 |
|---|---|
| 資安 | 傳輸 TLS；`idn` 敏感欄位（name、phone、resource 中的識別欄）以應用層加密（AES-GCM，金鑰在環境變數／KMS）；密碼 bcrypt；API 速率限制 |
| 個資 | 特種個資明確同意（§5.1）；資料最小化；撤回即刪；DPIA 文件在 `docs/DPIA.md`（骨架由本規格產生） |
| 稽核 | `sys.audit_log` append-only，DB trigger 禁止 UPDATE/DELETE；保存 ≥ 2 年 |
| 效能 | 每日批次 10,000 使用者在 60 分鐘內完成（A1 平行度可配置）；對話首字回應 < 3 秒 |
| 可用性 | Docker Compose 單機；資料庫每日備份 |
| 可解釋 | A1 每個建議附「依據」；A2 每個結論附查詢編號或來源 |
| 合規邊界 | 輸出不得構成診斷（C-7）；系統定位為健康促進工具，不申請 SaMD |

---

## 12. 專案結構

```
health-app/
├── CLAUDE.md                  # Claude Code 工作指引（見附件）
├── SPEC.md                    # 本文件
├── docker-compose.yml
├── .env.example
├── skills/
│   ├── a1-personal-digital-twin/SKILL.md
│   ├── a2-public-health-orchestrator/SKILL.md
│   ├── a3-data-analysis/SKILL.md
│   ├── a4-community-sentiment/SKILL.md
│   └── a5-site-operations/SKILL.md
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI
│   │   ├── config.py
│   │   ├── db/                # migrations (alembic), models_idn.py, models_anon.py, models_pub.py
│   │   ├── auth/
│   │   ├── consent/
│   │   ├── adapters/          # base.py, wearable.py, site.py, nhi.py, env.py, merchant.py, sentiment.py, mocks/
│   │   ├── events/            # redis streams producer/consumer
│   │   ├── deidentify/        # aggregate.py, strip_identity.py, k_anon.py
│   │   ├── tools/             # registry.py + 每個工具一檔
│   │   ├── agents/            # runtime.py, a1.py … a5.py, delegate.py, output_filter.py
│   │   ├── scheduler/         # jobs.py
│   │   ├── api/               # routers: me, family, sites, merchants, agency, admin
│   │   └── audit/
│   ├── fixtures/              # wearable_*.json, nhi_bundle_*.json, sentiment_*.json, villages.geojson
│   └── tests/
├── web/
│   ├── apps/citizen/          # 民眾 PWA
│   ├── apps/agency/           # 公衛儀表板
│   ├── apps/site/             # 據點人員端
│   ├── apps/merchant/         # 商家端
│   └── packages/ui/           # 共用元件（長者模式 tokens）
└── docs/
    ├── DECISIONS.md
    ├── DPIA.md
    └── interfaces.md          # 介面登錄表（S1–S6, D1–D2, T1–T2, U1–U3）
```

---

## 13. 預設決策（規格未明時）

1. k = 5；年齡層 10 歲一組；週起始為週一。
2. 個人步數目標預設 6,000，`exercise_rx` 可在 3,000–10,000 間調整。
3. 風險規則閾值見 §7.1；可在 `sys.config` 調整，變更寫稽核。
4. 高風險動作確認有效期 24 小時，逾時作廢。
5. 家屬摘要層固定欄位：`status_sentence`、`risk_level`、`pending_action`；不含數值。
6. 一期語言：繁體中文；所有使用者可見文字集中於 `i18n/zh-TW.json`。
7. 任何外部 API 無金鑰時自動切換 Mock 並在日誌標示。
8. 疾病斷言黑名單初始值：「您有」「您患有」「診斷為」「罹患」「確診」＋疾病名組合；允許「偏高」「建議複測」「建議諮詢」。
9. LLM 模型名由 `.env` 的 `CLAUDE_MODEL` 指定；temperature：A1 對話 0.3，A2/A3/A4 0.1。

---

## 14. 開發里程碑

| 里程碑 | 內容 | 驗收 |
|---|---|---|
| M1 基礎 | Compose、DB 兩 schema 與 migrations、auth、同意管理 API、事件匯流、稽核表 | C-1、C-6 測試通過；撤回流程 e2e |
| M2 資料進 | 六個 Adapter（Mock）、fixtures、FHIR 寫入、deidentify 批次、k-匿名中介軟體 | C-4、C-8 測試；`anon.cohort_weekly` 有資料 |
| M3 工具層 | §7.1、§7.2 全部工具 + 單元測試 | 每個工具可獨立呼叫並回傳規格格式 |
| M4 A1 | runtime、A1 每日批次、對話、確認狀態機、輸出過濾、民眾 PWA 最小畫面 | C-3、C-5、C-7；10 位 Mock 使用者 07:00 收到報告 |
| M5 機關側 | A2、A3、A4、委派協定、公衛儀表板、關懷代號清單、據點端解碼 | C-2；「北區未控制率」示範問題可完整跑通 |
| M6 商家與點數 | 商家端、核銷、對帳、防作弊 | 兌換 e2e；作弊 fixture 被標記 |
| M7 收尾 | DPIA 骨架、介面登錄表、談參產生、KPI 報表、效能測試 | §11 指標 |
| M8 擴充 | §17：近日社區活動（公開公告匯入＋人工上架、健康導向推薦、報名確認、報到給點）、商家／餐廳推薦（偏好＋評分門檻）、民眾健康儀表板與月報 | §17.8 情境 9–11；八條約束仍全綠 |

---

## 15. 驗收示範情境（必須全部可跑）

1. **每日報告**：Mock 使用者「王○○」血壓連三天偏高 → 05:00 批次 → A1 產生報告，含「建議到據點複測」與明早時段 → 07:00 推送 → 使用者回「好」→ 預約建立 → 稽核可查。
2. **家屬視角**：王○○授權女兒「摘要」→ 女兒端只看到狀態句與風險等級，無數值。
3. **撤回授權**：王○○撤回 nhi → Adapter 停止、資料 24h 內刪除、A1 後續報告不再引用就醫資料、無挽留文案。
4. **機關問答**：承辦問「北區高血壓未控制率為什麼上升」→ A2 委派 A3（附 SQL）與 A4（附來源）→ 合成建議標示「需承辦核定」。
5. **關懷閉環**：A2 輸出代號 → 據點端輸入任務編號解碼 → 任務單無健康資料 → 志工回報 → 去識別化後進 `anon.visit_outcomes`。
6. **輿情預警**：fixture 注入北區抱怨突增 → A4 發警訊 → A2 收到並對照數據。
7. **兌換**：達標發點 → 商家端核銷 → 帳本平衡 → 月成效報告無個人。
8. **約束測試**：任何試圖 JOIN `idn` 與 `anon` 的查詢被靜態檢查擋下；A3 帳號查 `idn` 被 DB 拒絕；n<5 的列被遮蔽。

---

## 16. 開放問題（需使用者決定，開發不阻塞）

1. 身分驗證一期用自建簡版還是 Keycloak？（預設：自建簡版，介面留 OIDC）
2. 推播一期是否接 LINE？（預設：Web Push，LINE 為 feature flag）
3. 地圖圖資來源（預設：OpenStreetMap + MapLibre）
4. 學員所屬機關層級，影響 `pub.villages` 範圍與示範資料（預設：單一區，10 個里）

---

## 17. M8 擴充：近日社區活動、商家推薦、健康儀表板（2026-10-30 使用者決定）

使用者決定：活動來源要**抓公開公告**；推薦活動**要參考個人健康狀況**；推薦商家／餐廳**依個人喜好與評分門檻**；有發點的活動要**顯示點數誘因**；民眾端補上**健康數據儀表板／報告／統計**。資料庫維持 PostgreSQL。

### 17.1 資料模型

```sql
pub.events  (id text pk, title, category, description, organizer, site_id fk null, village_code fk null,
             venue, lat, lng, start_at, end_at, capacity int null, points_reward int default 0,
             tags jsonb,             -- 例 ["bp","glucose","steps","indoor","outdoor","gentle","free"]
             source text,            -- site | agency | crawl
             source_url null, dedupe_key unique null, extracted_by null /* claude|rules */,
             status text,            -- pending_review | published | rejected | cancelled
             created_by, reviewed_by null, reviewed_at null, created_at)
             -- category ∈ {measure, walk, exercise, education, nutrition, vaccine, social}
pub.merchants 增加 rating numeric(2,1), rating_count, rating_source, rating_updated_at, tags jsonb
             -- tags 例 ["low_salt","low_sugar","vegetarian","healthy_meal","elder_friendly"]
idn.user_prefs (user_id pk, event_categories jsonb, merchant_categories jsonb, dietary jsonb,
             min_rating numeric(2,1) null, max_distance_km numeric null, event_push bool, updated_at)
idn.event_registrations (id, user_id, event_id fk, status /* registered|cancelled|attended */,
             created_by /* user|a1 */, registered_at, checked_in_at null, checked_in_by null,
             points_granted int, unique(user_id, event_id))
```

- 活動本身是公開資訊，放 `pub`；誰報名、誰報到是個資，放 `idn`（「某人參加血壓講座」本身透露健康狀況）。
- 參與統計只以 `anon.cohort_weekly` 的新指標 `event_attend_rate`（有 App 帳號者中，當週至少報到一場的比例）發布，n < k 遮蔽。
- 機關端看得到每場活動的報名數，但 < k 時只顯示「少於 k」；報名名單只有該據點人員看得到（報到用）。

### 17.2 活動來源與匯入（`adapters/events.py`，每 6 小時）

1. 據點與機關在各自端上架：直接 `published`。
2. 公開公告：區公所、里辦公處公開公告等，來源網址須在 `pub.source_whitelist`（`source_class = 'event_notice'`）。
   - 落地前先 `strip_identity()`（與輿情同一套 C-8 規則）。
   - 欄位擷取：有 ANTHROPIC_API_KEY 用 Claude（結構化輸出）；否則規則式擷取，日誌標 `[MOCK]`。
   - 一律寫成 `pending_review`，**經機關人員審核上架後才推給民眾**；日期抽不出或已過期的不收。
   - 只用 `anon_writer` 連線（只碰 `pub`），完全不接觸 `idn`；不可直接拿 A4 的輿情語料推給民眾。
3. 商家評分：Google Places（有金鑰時，`PLACES_MERCHANTS` 對照）；無金鑰用 `fixtures/merchant_ratings.json`（`[MOCK]`）。

### 17.3 工具（民眾側，皆需 `consent_scope`）

| 工具 | 輸入 | 輸出 | 規則摘要 |
|---|---|---|---|
| `event_recommend` | days, limit, category? | events[]（理由、依據、點數、名額、距離）| 只看授權範圍內的資料決定健康理由；理由文字經 C-7；推播用標題不含健康理由 |
| `event_register` | event_id, action | registration / summary | `register` 為高風險動作（報名會把姓名交給主辦單位），`confirmed=true` 才執行；`cancel` 直接執行 |
| `merchant_recommend` | category?, radius_km?, min_rating? | merchants[]（評分、理由、可兌換優惠）、略過家數 | 評分低於門檻（本人設定，否則 `sys.config.merchant_min_rating`，預設 4.0）或無評分者不推薦 |
| `health_summary` | period ∈ {week, month, quarter} | 各指標時序與統計、與前期比較、同齡比較（k 遮蔽）、未授權項目 | 只回授權範圍內的指標；同齡比較只用 `anon.cohort_weekly` |

健康理由對照（只在對應項目已授權時才會出現）：

| 訊號 | 需要的授權 | 偏好的活動標籤／商家標籤 |
|---|---|---|
| 最近血壓量測連續偏高 | `site.bp` | `bp`／`low_salt` |
| 健保就醫紀錄有血壓、血糖相關慢性病 | `nhi.conditions` | `bp`、`glucose`／`low_salt`、`low_sugar` |
| 最近血糖量測偏高 | `site.glucose` | `glucose`／`low_sugar` |
| 步數下降或未達目標 | `wearable.steps` | `steps` |
| 運動處方為保守版、或今天空品／天氣不適合戶外 | 依 `exercise_rx` | `gentle`、`indoor` |

### 17.4 推播

- 併入 07:00：每日報告加一段「這週附近」（App 內可看理由）；另以 `kind = 'event'` 推播最推薦的一場。
- 推播標題與內文只有活動名稱、時間、地點、點數，**不含任何健康理由**（鎖定畫面旁人看得到）。
- 每人每週活動推播上限 `sys.config.event_push_weekly_cap`（預設 2）；本人可在偏好設定關掉活動推播。
- 活動取消時通知已報名者。

### 17.5 點數

- `points_reward > 0` 的活動，在推薦清單、活動頁、推播都顯示「參加可得 N 點」。
- 報到由據點人員掃民眾 QR（10 分鐘有效）完成，活動開始前 1 小時至結束後 2 小時內有效；報到後發點（帳本 `reason = event.attend`、`ref_type = event`），同一場只發一次。

### 17.6 API

```
GET  /me/health?period=week|month|quarter     健康儀表板（經 health_summary）
GET  /me/prefs/recommend   PUT /me/prefs/recommend   偏好（活動興趣、商家類別、飲食、評分門檻、距離、活動推播）
GET  /me/events            推薦活動＋我的報名
POST /me/events/{id}/register   {confirmed?}   高風險：202 待確認
POST /me/events/{id}/cancel
GET  /me/merchants/recommend?category=
GET  /events?status=…   POST /events   PATCH /events/{id}   POST /events/{id}/review   POST /events/{id}/cancel
GET  /events/{id}/registrations        據點人員（本據點）才可看名單（姓名遮罩）
POST /events/{id}/checkin {user_qr}     據點人員報到＋發點
POST /admin/events/sync                 立即匯入公開公告（管理）
```

### 17.7 使用端

- 民眾 App：底部分頁改為「今天／數據／活動／小幫手／點數／更多」（守護圈移到「更多」）。
  - 數據：期間切換（7／30／90 天）、各指標圖表與統計卡、與前期與同齡比較、未授權項目提示、可列印月報。
  - 活動：推薦清單（理由、點數）、報名確認、我的報名。
  - 點數：新增「推薦店家」（評分與理由）。
  - 更多：新增「推薦偏好」。
- 機關儀表板：新增「活動」分頁（待審公告、上架、取消；報名數 < k 只顯示「少於 k」）。
- 據點工作台：新增「活動」分頁（建立活動、報名名單、掃 QR 報到）。

### 17.8 驗收示範情境

9. **活動推薦**：公開公告 fixture 匯入 → 待審 → 機關上架 → 王秀英的活動頁推薦「血壓量測與衛教」並說明理由（依據據點血壓）→ 推播只含活動名稱與時間 → 報名需確認 → 據點掃 QR 報到 → 點數入帳。
10. **商家推薦**：王秀英偏好早餐與少鹽、門檻 4.0 → 推薦清單不含評分低於 4.0 的店家，並顯示略過家數。
11. **健康儀表板**：王秀英的「數據」頁顯示 30 天步數、血壓、體重、血糖圖表與統計；撤回 site 授權後血壓等項目改顯示「未授權」。
