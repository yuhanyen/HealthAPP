# 介面登錄表

健康大數據 App 一期 PoC｜依 SPEC §12 編號：S（資料來源）、D（資料中台內部）、T（第三方服務）、U（使用端）
最後更新：2026-10-30｜狀態欄：**真實**＝已可對真實服務運作、**已寫未驗**＝程式完成但未以真實金鑰驗證、**Mock**＝一期以模擬資料運作

## 總覽

| 編號 | 介面 | 方向 | 狀態 | 對口單位 |
|---|---|---|---|---|
| S1 | 手機／穿戴裝置活動資料 | 入 | Mock | 手機平台（Apple HealthKit、Google Health Connect）、App 端 |
| S2 | 社區據點量測 | 入 | 真實（API）／設備模擬 | 衛生所、社區據點、量測設備廠商 |
| S3 | 健保署健康存摺 | 入 | Mock | 衛福部中央健康保險署 |
| S4 | 外部環境（天氣、空品） | 入 | 已寫未驗 | 中央氣象署、環境部 |
| S5 | 在地特約商家 | 入／出 | 真實（API） | 特約商家、商家管理承辦 |
| S6 | 公開輿情 | 入 | 已寫未驗（Google Places）／Mock | Google、地方媒體；來源白名單由法制審查 |
| S7 | 公開活動公告（M8） | 入 | 已寫未驗（RSS／Atom）／Mock；欄位擷取已以 Claude 實測 | 區公所、里辦公處公開公告；來源白名單由法制審查 |
| S8 | 商家公開評分（M8） | 入 | 已寫未驗（Google Places）／Mock | Google |
| D1 | 去識別化通道（idn → anon） | 內 | 真實 | 系統內部 |
| D2 | 事件匯流（Redis Streams） | 內 | 真實 | 系統內部 |
| T1 | 大型語言模型（Claude API） | 出 | 已寫未驗（無金鑰時規則式） | Anthropic |
| T2 | 推播（Web Push／LINE） | 出 | Mock（App 收件匣為真實） | 瀏覽器推播服務、LINE |
| U1 | 民眾 App（含守護圈） | 出入 | 真實 | 民眾、家屬 |
| U2 | 公衛儀表板 | 出入 | 真實 | 衛生局、衛生所承辦 |
| U3 | 據點人員端、商家端 | 出入 | 真實 | 據點人員、志工、商家 |

## S — 資料來源（L1 → L2）

所有來源都經 Adapter 介面（`backend/app/adapters/`），寫入可識別區只能經 `fhir/writer.ingest`：同一交易內鎖住授權、只寫授權範圍內的項目、以自然鍵 upsert。沒有金鑰時自動使用 `adapters/mocks/`，日誌標 `[MOCK]`。

### S1 手機／穿戴裝置
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/wearable.py`、`adapters/mocks/wearable.py`、`adapters/runner.py:sync_wearable` |
| 資料 | 每小時／每日步數、心率、睡眠（FHIR Observation，LOINC） |
| 授權 | `wearable.steps`、`wearable.heart_rate`、`wearable.sleep` |
| 頻率 | 每小時 :05（worker 排程） |
| 落地 | `idn.fhir_resources`（source = wearable）；事件 `measurement.created`（每批一則） |
| Mock | `fixtures/wearable_profiles.json`（含防作弊樣本 DEMO000010） |
| 待辦 | App 端以 HealthKit／Health Connect 讀取後上傳（二期原生 App） |

### S2 社區據點量測
| 項目 | 內容 |
|---|---|
| 端點 | `POST /api/v1/sites/{site_id}/measurements`，body：`{user_qr, items: {bp, weight, height, body_fat, glucose}}` |
| 身分 | 據點人員帳號（只能上傳到所屬據點）；民眾出示 10 分鐘有效的簽章 QR（不含姓名或健康資料） |
| 授權 | `site.bp`、`site.weight`、`site.height`、`site.body_fat`、`site.glucose`；未授權即拒絕且不儲存 |
| 落地 | `idn.fhir_resources`（source = site）；事件 `measurement.created`（每筆一則，A1 據此判斷異常） |
| 待辦 | 藍牙量測設備直接上傳（目前以表單模擬） |

### S3 健保署健康存摺
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/nhi.py`、`adapters/mocks/nhi.py`、`runner.py:sync_nhi` |
| 資料 | 就醫（Encounter）、診斷（Condition，ICD-10）、用藥（MedicationStatement，ATC）、檢驗、疫苗、過敏 |
| 授權 | `nhi.visits`、`nhi.conditions`、`nhi.medications`、`nhi.labs`、`nhi.immunizations`、`nhi.allergies` |
| 頻率 | 每日 04:45（05:00 批次前） |
| 落地 | 只存臨床資源；Bundle 中的 Patient 不落地，以身分證字號雜湊比對使用者 |
| Mock | `fixtures/nhi_bundle_DEMO000001/4/7.json` |
| 待辦 | 健保署健康存摺 SDK／API 申請與介接 |

### S4 外部環境
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/env.py`（RealEnvClient）、`adapters/mocks/env.py` |
| 來源 | 中央氣象署 O-A0003-001（觀測）、環境部 aqx_p_432（AQI）；熱指數由溫濕度計算 |
| 金鑰 | `CWA_API_KEY`、`MOENV_API_KEY`（未設定即 Mock） |
| 頻率 | 每小時；落地 `pub.env_hourly`（非個資） |
| 授權 | 民眾端以 `env.district` 決定是否依所在區調整運動建議 |

### S5 在地特約商家
| 項目 | 內容 |
|---|---|
| 端點 | `POST /merchants/{id}/offers`（上架）、`POST /merchants/{id}/verify`（核銷）、`GET /merchants/{id}/report?month=`（月成效） |
| 身分 | `X-Merchant-Key`（系統對接，只存雜湊）或商家帳號登入 |
| 揭露 | 核銷只回品項與點數；月成效只有聚合，年齡層人數 < k 遮蔽；商家看不到兌換者任何資料 |
| 授權 | 民眾兌換需 `merchant.redemptions` 授權，且為高風險動作（需本人確認） |

### S6 公開輿情
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/sentiment.py`、`adapters/sentiment_land.py`、A4 工具 `crawl_schedule` |
| 來源 | 白名單（`pub.source_whitelist`）：Google 商家評論（Places API）、地方新聞、論壇；不在白名單一律不採 |
| 去識別 | 落地前 `strip_identity()` 移除帳號、名稱、頭像、@提及、電話、Email（C-8） |
| 頻率 | 每 6 小時（A4 排程：採集 → 分類 → 突增偵測 → 警訊） |
| 落地 | `anon.sentiment_items`（以連結＋內容去重） |
| 金鑰 | `PLACES_API_KEY`（未設定即 Mock：`fixtures/sentiment_*.json`） |

### S7 公開活動公告（SPEC §17.2）
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/events.py`、`adapters/events_land.py:sync_events`、`adapters/mocks/events.py` |
| 來源 | `EVENT_FEEDS`（RSS／Atom 網址 JSON 陣列）；只收 `pub.source_whitelist` 中 `source_class = 'event_notice'` 的網址 |
| 去識別 | 落地前 `strip_identity()`（與 S6 同一套 C-8 規則） |
| 擷取 | 有 `ANTHROPIC_API_KEY` 用 Claude 結構化輸出，否則規則式（[MOCK]）；日期時間抽不出來或已過期的不收 |
| 頻率 | 每 6 小時 :15；管理端可 `POST /api/v1/admin/events/sync` 立即執行 |
| 落地 | `pub.events`（`pending_review`，以來源＋名稱＋時間去重）；只用 `anon_writer` 連線，不接觸 idn |
| 上架 | 機關人員在公衛儀表板「活動」核對後上架，才會推薦給民眾 |
| Mock | `fixtures/event_notices.json`（含發文者、電話、Email、@提及，用來驗證 C-8；2 則不在白名單） |

### S8 商家公開評分（SPEC §17.2）
| 項目 | 內容 |
|---|---|
| 程式 | `adapters/merchant_rating.py`、`adapters/events_land.py:sync_merchant_ratings` |
| 來源 | Google Places（`rating`、`userRatingCount`）；`GOOGLE_PLACES_API_KEY` ＋ `PLACES_MERCHANTS`（商家編號 → place_id） |
| 頻率 | 每日 04:30；管理端可 `POST /api/v1/admin/merchants/ratings/sync` |
| 落地 | `pub.merchants` 的評分欄位（`anon_writer` 只有這幾欄的更新權限）；不存任何評論者資料 |
| Mock | `fixtures/merchant_ratings.json`（M18 刻意沒有評分） |

## D — 資料中台內部

### D1 去識別化通道（唯一的 idn → anon 路徑）
| 項目 | 內容 |
|---|---|
| 程式 | `deidentify/aggregate.py`（`run_deidentify`）、`deidentify/suppress.py`、`deidentify/k_anon.py` |
| 觸發 | 事件 `daily.batch`（05:00） |
| 做法 | 以 idn_app 連線在資料庫內聚合，回到應用層只剩聚合格；再以 anon_writer 連線寫入。兩條連線、兩個角色，沒有任何 SQL 同時碰兩區（C-1，靜態掃描＋執行期攔截＋DB 權限三道防線） |
| 輸出 | `anon.cohort_weekly`（主要＋次要遮蔽）、`site_ops_weekly`、`merchant_weekly`、`visit_outcomes`、`app_metrics_daily` |
| 規則 | 每格 n ≥ k（預設 5，`sys.config.k`）才發布；加總與子格之差落在 1..k-1 時再遮蔽 |

### D2 事件匯流
| 事件 | 產生者 | 消費者 | 可識別欄位 |
|---|---|---|---|
| `consent.revoked` | 同意管理 | adapters（立即刪除）、a1 | user_id（idn 側） |
| `measurement.created` | Adapter | a1（異常提醒） | user_id（idn 側） |
| `nhi.updated` | NhiAdapter | — | user_id（idn 側） |
| `daily.batch` | 排程 05:00 | deidentify、a1 | 無 |
| `goal.achieved` | points_engine | — | user_id（idn 側） |
| `care.list.ready` | care_rules | 據點端 | 無（只有代號） |
| `sentiment.alert` | A4 alert_emit | a2（對照分析） | 無 |

事件於資料庫 commit 後才發布（rollback 即丟棄）；去識別化側事件禁止夾帶 user_id 等欄位（`events/bus.py:IDENTIFYING_FIELDS`）。

## T — 第三方服務

### T1 大型語言模型
| 項目 | 內容 |
|---|---|
| 服務 | Anthropic Claude Messages API，模型 `claude-opus-5-5`（`.env` 的 `CLAUDE_MODEL`） |
| 使用者 | A1 對話、A2／A3／A4 分析、輿情分類；A1 每日批次不呼叫 LLM |
| 送出內容 | SKILL.md、執行期情境（年齡層、授權範圍、雙生快照）與工具結果；**不含**姓名、電話、地址、身分證 |
| 控制 | 工具定義不含 user_id／consent_scope（由 runtime 注入）；A1 輸出逐句過 C-7 黑名單；伺服器端備援模型；prompt cache |
| 金鑰 | `ANTHROPIC_API_KEY`（未設定即規則式 planner，流程與稽核完全相同） |
| 待辦 | 以正式金鑰驗證並量測首字回應時間；資料處理協議（DPA） |

### T2 推播
| 項目 | 內容 |
|---|---|
| 現況 | App 收件匣（`idn.notifications`）為真實；外部推播為 MockPush |
| 待辦 | Web Push（VAPID 金鑰、HTTPS）；LINE 為 feature flag |

## U — 使用端

| 編號 | 使用端 | 位置 | API | 角色 |
|---|---|---|---|---|
| U1 | 民眾 App（PWA，含守護圈） | `/app/citizen/` | `/me/*`、`/consents/*`、`/family/*` | citizen |
| U2 | 公衛儀表板 | `/app/agency/` | `/agency/*`（只讀去識別化區）；`/events`（活動審核上架，pub 非個資；報名數 < k 遮蔽、看不到名單） | agency、admin |
| U3 | 據點人員端 | `/app/site/` | `/sites/*`；`/events`（本據點活動、報名名單〔姓名遮罩、查看留稽核〕、報到） | site_staff |
| U3 | 商家端 | `/app/merchant/` | `/merchants/*` | merchant |
| — | 管理（API 文件／驗收台） | `/docs`、`/console`（僅開發環境） | `/admin/*` | admin |

所有端點皆 Bearer token（自建簡版，OIDC 介面保留於 `auth/oidc.py`）；登入與 Agent 對話有速率限制。完整規格見 `/docs`（OpenAPI）。
