# 決策紀錄

## 現在在做
M1–M8 全部完成（2026-10-30；394 測試全綠）。後續：以正式來源驗證活動公告 RSS 與 Google Places、Web Push、A5 據點營運 Agent（二期）。

## 紀錄
| 日期 | 決定 | 理由 |
|---|---|---|
| 2026-10-29 | 本機無 Docker：PostgreSQL 16 改用 `pgserver` 套件內附執行檔、Redis 用 Windows 版 5.0.14，由 `scripts/localinfra.py` 啟停；資料放 `~/.healthapp/`。`docker-compose.yml` 依 SPEC 寫好，但本機尚未實測 | 開發機沒有 Docker；資料目錄放純 ASCII、非 OneDrive 路徑，避免同步與全形路徑問題 |
| 2026-10-29 | Python venv 放 `~/.venvs/healthapp`，不放專案資料夾 | 避免 OneDrive 同步上萬個套件檔 |
| 2026-10-29 | 每個 DB 角色一個 engine：`idn_app`、`idn_user_scoped`、`idn_site_scoped`、`anon_reader`、`anon_writer`、`audit_writer`；沒有任何應用角色同時擁有 idn 與 anon | C-1 的第三道防線：就算繞過程式檢查，DB 也不可能執行跨區 JOIN |
| 2026-10-29 | C-1 三道防線：執行期 guard（每個 engine 的 before_cursor_execute）＋靜態掃描（`make check-schema`，含「同模組 import 兩區模型」規則）＋DB 權限 | SPEC 寫「靜態掃描 SQL；整合測試」，ORM 產生的 SQL 只有執行期攔得到 |
| 2026-10-29 | `anon_reader` 可唯讀 `pub`（里、據點、優惠、環境、白名單、商家公開欄位），不給 `merchants.api_key_hash` | C-2「只有 anon 權限」解讀為不得碰 idn／sys；pub 不含個資，A2 的 resource_sim 需要據點與里資料 |
| 2026-10-29 | A1 用 `idn_user_scoped` + RLS（`app.user_id`）；A5 用 `idn_site_scoped` + RLS（`app.site_id`），且只能讀 users 的 id／姓名／電話／地址 | C-3 在 DB 層多一道防線；A5 解碼只拿做事需要的欄位 |
| 2026-10-29 | `idn.users` 增加 `village_code`（去識別化聚合鍵需要里）與 `address`（據點解碼需地址）；name／phone／address 以 AES-GCM 應用層加密 | SPEC §4.1 沒列但 §4.4、§7.3 需要；§11 要求敏感欄位加密 |
| 2026-10-29 | 新增 `idn.pending_actions`（C-5 確認狀態機）與 `idn.deletion_jobs`（撤回後刪除任務） | 確認有效期 24h、刪除 24h SLA 都需要可查、可補做的狀態 |
| 2026-10-29 | 新增 `sys.accounts`（自建簡版登入：bcrypt + HS256 JWT，OIDC 介面留在 `auth/oidc.py`）與 `sys.config`（k、閾值；變更寫稽核） | SPEC §16-1 預設自建簡版；§4.4 提到 sys.config 但未定義 |
| 2026-10-29 | 稽核 `actor_type` 擴充為 user／staff／agent／admin／system；輸入輸出只存 SHA-256 摘要 | 據點人員解碼、排程刪除需要對應身分；稽核本身不能變成另一份個資 |
| 2026-10-29 | 稽核寫入方式：被拒絕的呼叫用 `audit_writer` 另開交易立即 commit；成功的解碼與授權變更跟業務資料同一交易 | 被拒絕也必須留痕（呼叫端會 rollback）；成功的動作要與資料一致 |
| 2026-10-29 | `sys.audit_log`、`idn.points_ledger`、`idn.decode_audit` 以 trigger 禁止 UPDATE／DELETE／TRUNCATE（連超級使用者也擋），應用角色也不授予這些權限 | SPEC §11 append-only |
| 2026-10-29 | 授權更新採版本化：舊版本標 `superseded_at`，新增 version+1 | 保留每一版同意的證據 |
| 2026-10-29 | 撤回授權屬高風險動作：`DELETE /consents/{id}` 不帶 `confirmed` 回 202 與待確認動作；App 已顯示確認文案時可帶 `confirmed=true`，同樣經狀態機並留稽核。確認文案只有「確定要撤回嗎？撤回後您將失去：…」，完成訊息只有「已撤回。」 | C-5 + CLAUDE.md「不得加挽留文案」 |
| 2026-10-29 | Agent 傳入的 `confirmed`／`action_id` 一律由工具層剝除；只有本人呼叫 `/me/actions/{id}/confirm` 才會執行 | 防止 LLM 自行宣稱「已確認」 |
| 2026-10-29 | 撤回後：`consent.revoked` 由 adapters 群組立即刪除該來源的 FHIR 資料與雙生快照（衍生資料，下次批次重建）；worker 每 10 分鐘補做未完成任務；點數帳本不刪 | 「24h 內刪除」以立即刪除＋排程保底達成；帳本為 append-only 財務紀錄 |
| 2026-10-29 | 事件在 DB commit 之後才發布（outbox-lite），rollback 則丟棄；去識別化側事件（sentiment.alert、care.list.ready）禁止夾帶 user_id 等可識別欄位 | 避免發出「資料其實沒寫進去」的事件；兩區在事件層也不交流可識別資料 |
| 2026-10-29 | k-匿名中介軟體以結果的 `n` 欄判斷；聚合查詢沒輸出 `n` 一律拒絕；遮蔽時清空數值欄與 n、保留維度欄並標 `_masked` | 讓呈現端能照實標「已遮蔽（< k）」，不補值 |
| 2026-10-29 | `pub.villages.geom` 以 GeoJSON（JSONB）存，一期不裝 PostGIS | pgserver 與 postgres:16 映像都沒有 PostGIS；M5 的 MapLibre 直接吃 GeoJSON |
| 2026-10-29 | M1 一併實作八條約束所需的最小元件（工具層 registry、確認狀態機、k_anon、strip_identity、output_filter、解碼稽核、SentimentAdapter.normalize） | CLAUDE.md 要求每個里程碑結束時八條約束全綠；完整功能仍依原里程碑交付 |
| 2026-10-29 | 示範資料：虛構「北區」10 里（N01–N10）、50 位民眾（含示範主角王秀英與女兒王怡君）、6 據點、12 商家，固定亂數種子；anon 不塞假資料，由 M2 deidentify 產生 | SPEC §16-4 預設單一區 10 里；anon 必須來自單向聚合 |
| 2026-10-29 | redis-py 一律 `protocol=2`；本機 Redis 綁 127.0.0.1 並開 protected-mode | Windows 版 Redis 5 不支援 RESP3；只收本機連線 |
| 2026-10-29 | 新增 `/console` M1 驗收台（開發環境限定，prod 回 404），文案在 `i18n/zh-TW.json` 的 `console` 區段 | 讓非技術人員也能看見約束測試結果與撤回流程；正式前端於 M4 |
| 2026-10-30 | `idn.fhir_resources` 加 `code`（主要編碼）並把唯一鍵改為 (user, type, source, **code**, effective_at)，約束名 `uq_fhir_resources_natural_key` | SPEC 原唯一鍵讓同一小時的步數與心率、同一次量測的血壓與體重互撞；慣例名稱超過 PG 63 字元上限 |
| 2026-10-30 | 所有 Adapter 只能經 `fhir/writer.ingest` 寫 idn：同交易內 `FOR SHARE` 鎖住授權、只寫授權範圍內的項目、upsert 冪等、同批去重 | 撤回與寫入的競態：撤回會等寫入完成，之後的刪除任務一併刪除；撤回先完成則整批不寫 |
| 2026-10-30 | 健保 Bundle 的 `Patient` 不落地，FHIR 庫只存臨床資源；Mock 以 Patient.identifier（身分證字號）雜湊比對使用者 | 身分只存在 idn.users（加密），避免 FHIR 庫成為第二份身分資料 |
| 2026-10-30 | 事件：site 每筆量測一則 `measurement.created`；wearable 每次同步一則（附 count）；nhi 每次同步一則 `nhi.updated`；事件流保留約 20 萬則 | 每小時穿戴同步若逐筆發事件會淹沒 A1 消費者；事件流不是長期儲存 |
| 2026-10-30 | 健保同步排在 04:45，05:00 再發 `daily.batch` | 讓 A1 與 deidentify 在批次時看到當天最新就醫紀錄 |
| 2026-10-30 | 群體統計除最細格（里×年齡層×性別）外，另發布 `ALL` 加總列（各里、各年齡層、各性別、全區），並做**次要遮蔽**：每個已發布加總的每種拆分，被遮蔽子格人數合計須為 0 或 ≥ k | 只刪 n<k 的格子時，「加總 − 已發布子格」可推回被刪的人；有加總列才能回答「北區整體」這類問題 |
| 2026-10-30 | 群體指標：steps_avg、steps_goal_rate、bp_uncontrolled_rate（高血壓者近 4 週最近一次 ≥140/90 的比例）、bp_high_rate、measure_freq、site_usage、redeem_rate；risk_l2_rate 待 M3 risk_score | SPEC 只列名稱未定義；以「目前授權」為分母，歷史週為近似 |
| 2026-10-30 | `anon.site_ops_weekly`／`merchant_weekly`：到訪（兌換）人數 ≥ k 才寫入；據點另要求預約人數為 0 或 ≥ k | 同樣適用 k 規則，避免小據點的少數人被辨識 |
| 2026-10-30 | 示範資料改為 50 位可登入示範民眾＋約 1,150 位不可登入的背景居民（`--population` 可調），並加入情境：光明里、中正里近 4 週據點使用下降、高血壓族群血壓上升 | 50 人分到 140 格幾乎全被 k=5 遮蔽，公衛端無資料可看；情境支撐 SPEC §15 情境 4、6 的示範 |
| 2026-10-30 | 示範資料的健康歷程也經 `fhir/writer.ingest`（照樣受授權範圍限制）；健保經 NhiAdapter、近 72 小時穿戴經 WearableAdapter | 不繞過正式管道，示範資料與真實資料的形狀與規則一致 |
| 2026-10-30 | pgserver 的 Windows 版沒附時區資料庫：`localinfra` 自動把 Python `tzdata` 的 TZif 檔複製到 `share/postgresql/timezone` | 否則 `AT TIME ZONE 'Asia/Taipei'` 失敗；Docker 的 postgres:16 不需要 |
| 2026-10-30 | EnvAdapter 真實串接（氣象署 O-A0003-001、環境部 aqx_p_432）與 Google Places 評論的解析已寫並有單元測試，但開發機無金鑰、**未以真實金鑰驗證**；無金鑰時自動 Mock 並標 [MOCK] | SPEC §5「真實可行」；誠實標示驗證程度 |
| 2026-10-30 | 民眾量測 QR 為 10 分鐘有效的簽章 token（不含姓名或健康資料）；據點人員只能上傳到自己的據點，回應只顯示遮罩姓名「王○英」 | 據點端最小揭露 |
| 2026-10-30 | 工具層 30 個工具，每檔一個；A5 的二期 stub（site_schedule、dispatch、visit_report、equipment、event_mgmt）合在 `site_stubs.py`，呼叫回 `not_implemented` 並照常稽核 | CLAUDE.md「每工具一檔」的例外：stub 沒有邏輯，拆五檔只是噪音 |
| 2026-10-30 | 工具的授權範圍可依參數決定（`required_scope` 可為函式），例如 points_engine 只有 redeem 需要 merchant.redemptions | 同一工具不同動作需要的資料不同；一律要求最大範圍會讓授權少的民眾連餘額都看不到 |
| 2026-10-30 | A1 的 `idn_user_scoped` 增加 notifications、points_reviews、points_ledger 的 INSERT 與 notifications.read_at 的 UPDATE（皆受 RLS 限本人）；通知一次寫入最終狀態，不事後改 status | A1 要能發點、記防作弊待審、送通知；不給 UPDATE 其他欄位，避免改寫歷史 |
| 2026-10-30 | 據點時段佔用與 care_rules 規則運算用 `idn_app` 服務連線（不經 A1／機關側連線），只把「時段剩餘數」或「代號清單」交回工具 | A1 的 RLS 看不到別人的預約；care_rules 屬機關側工具但規則必須在 idn 執行，結果只含代號 |
| 2026-10-30 | 民眾日報的同齡比較改用 anon_reader 另開連線查 `anon.cohort_weekly`（經 k-匿名），取最近一個完整週 | A1 不得 JOIN anon；用已發布的去識別化結果，被遮蔽就不比較 |
| 2026-10-30 | sandbox_sql：只允許單一 SELECT／WITH、黑名單關鍵字、`statement_timeout` 10 秒、外包 LIMIT 5001、經 k-匿名；每次查詢以 audit_writer 寫 `sys.analysis_queries`（Q-xxxx）供 A2 引用 | A3 須能自由分析又不能繞過 k；查詢編號讓報告可重現 |
| 2026-10-30 | sandbox_python：一期以子行程（`python -I -X utf8`）＋停用 socket＋60 秒逾時＋獨立暫存目錄隔離，不給 DB 連線（資料由 sandbox_sql 結果以檔案傳入）；二期換容器 | 開發機無 Docker；誠實標示這不是強隔離 |
| 2026-10-30 | 分析產物（SVG／HTML／CSV）存 `backend/var/artifacts/`，以 `GET /api/v1/agency/artifacts/{name}` 下載，限 agency／admin，回應一律 `Content-Security-Policy: sandbox` | 沙箱輸出可能是任意 HTML，不能在本站來源執行腳本 |
| 2026-10-30 | trend_detect 在 Python 端計數（近 7 日 vs 前 28 日週均），告警條件：倍數 ≥ 3、≥ 2 種來源類別、≥ 3 則 | 輿情資料量小；SKILL A4「單一來源不告警」 |
| 2026-10-30 | nlp_sentiment／topic／entity：有金鑰時用 Claude 結構化輸出（`output_config.format` json_schema），無金鑰時以規則字典（[MOCK]） | 測試與離線示範不依賴外部 API |
| 2026-10-30 | LLM 呼叫一律 `claude-opus-5-5`、不設 temperature 等取樣參數（模型不接受），改以 `output_config.effort` 分級（A1／A4／NLP low、A2／A3 medium）；啟用 server-side fallbacks（beta `server-side-fallback-2026-07-01`） | 新模型的 API 規則；民眾端求快、分析端求準；主模型忙碌時自動降級不中斷 |
| 2026-10-30 | 風險分級 L1／L2／L3 規則（見 `services/risk.py`）閾值可由 `sys.config.risk_rules` 調整；每條規則只在對應授權項目存在時才評估 | SPEC 只寫「連續性」；沒授權的資料不能拿來判斷 |
| 2026-10-30 | A1 每日批次依 SKILL「每日批次」1–9 步以固定流程經工具層組報告，**不呼叫 LLM**；LLM 只用在對話 | 1 萬人每天跑 LLM 成本與延遲過高；步驟本來就固定，規則化更可稽核、輸出也一定過 C-7；實測 50 人 6 秒 |
| 2026-10-30 | A1 只服務有 App 帳號的民眾（sys.accounts 角色 citizen）；背景居民不跑 A1 | 背景居民只用於群體統計示範，沒有人會看報告 |
| 2026-10-30 | Agent runtime 自建（agents/runtime.py）：planner 介面兩種實作——ClaudePlanner（串流、eager_input_streaming、SKILL.md 區塊加 prompt cache）與規則式 mock（無金鑰，[MOCK]）；兩者走同一條工具迴圈、確認、稽核、過濾 | CLAUDE.md 禁用重型框架；無金鑰也能完整示範且路徑一致 |
| 2026-10-30 | A1 工具呼叫的 user_id、consent_scope 由 runtime **注入並覆寫**模型給的值，且不出現在給模型的工具定義；registry 仍逐次比對本人實際授權（撤回後立即失效） | 模型無法冒用他人或擴大授權；SKILL「授權範圍是你的身分」 |
| 2026-10-30 | 對話中本人說「好」只會建立待確認動作並出現確認卡片，按「確認」（/me/actions/{id}/confirm）才執行 | C-5：自然語言的「好」可能被誤判，確認必須是明確的介面操作 |
| 2026-10-30 | 對話紀錄存 idn.chat_messages（內容 AES-GCM 加密、RLS 限本人），只存文字回合；撤回任何授權時清除對話，並刪除 sources_used 含該來源的每日報告 | 無法判斷舊回覆引用了什麼；SPEC 情境 3「A1 後續報告不再引用就醫資料」 |
| 2026-10-30 | 送往 LLM 的情境只含年齡層、長者模式、授權範圍、雙生快照，不含姓名、電話、地址、身分證 | 資料最小化；外部 API 不需要識別資訊 |
| 2026-10-30 | A1 串流回覆逐句過 C-7（StreamingFilter）：句子完整才送出，半句留在緩衝 | 斷言詞可能被切在兩個串流片段之間 |
| 2026-10-30 | 異常事件只對據點量測（measurement.created source=site）觸發，同一天最多提醒一次；家屬同步通知**未實作** | 穿戴每小時同步也發此事件；A1 的 RLS 連線寫不了家屬的收件匣，需另設服務流程（列為後續） |
| 2026-10-30 | 推送一期為 App 收件匣（idn.notifications）＋ MockPush；Web Push（VAPID）與 LINE 為後續 | SPEC §16-2 預設 Web Push，但需金鑰與 HTTPS；收件匣已能驗收 07:00 推送 |
| 2026-10-30 | 守護圈：家屬產生 30 分鐘有效的「守護碼」（簽章 token），本人輸入後建立分享（高風險，需確認）；本人收回立即生效、不需確認；家屬摘要固定三欄且移除數字，讀取寫稽核 | 不暴露 user_id、無法列舉他人；收回只會減少揭露；SPEC §13-5 |
| 2026-10-30 | POST /me/bookings、/me/redeem、/me/family 與撤回授權同一模式：不帶 confirmed 回 202＋待確認動作；App 已顯示確認卡片時可帶 confirmed=true，同樣經狀態機與稽核 | 一致的 C-5 介面，App 可以一個確認畫面完成 |
| 2026-10-30 | 前端為 pnpm workspace（web/apps/*、web/packages/ui），hash 路由，build 後由 API 掛在 /app/<名稱>/（同源、免 CORS）；開發時 make web 用 Vite 代理 /api | 一鍵啟動即可看到 App；靜態檔不需要伺服器端路由 |
| 2026-10-30 | Node 24 的 fs.rmSync（C++ 實作）在本專案路徑會讓行程崩潰（0xC0000409）：pnpm／Vite 一律以 NODE_OPTIONS=--require web/scripts/rmsync-shim.cjs 執行（make.ps1 已處理），dist 由 scripts/clean.mjs 清空 | 專案位於含全形字的 OneDrive 路徑；只換掉這一個函式，不更動 Node 版本 |
| 2026-10-30 | 前端套件固定版本且發布超過兩週（vite 7.3.7、react 19.1.10、typescript 5.9.3、pnpm 10.34.7） | 供應鏈風險：不追最新版 |
| 2026-10-30 | 民眾 App 最小字級 18px、長者模式 22px＋高對比（<html data-elder>）；語音播報用瀏覽器 speechSynthesis（stub）；PWA manifest 名稱由 build 時從 i18n 產生 | SPEC §10、CLAUDE.md；所有可見文字仍來自 i18n |
| 2026-10-30 | 委派協定（§8.3）以工具 delegate_a3／delegate_a4 實作（A2 專屬），A3／A4 在委派內以各自 Agent 身分（A3:D-…）呼叫工具；agents/delegate.py 驗證：A3 要有查詢編號且每個結論有依據、A4 每條事件有來源連結，否則 status=rejected、A2 不採用；每次委派寫稽核 | 稽核可追到「誰委派、誰執行哪條 SQL」；SKILL A2「交證據不交結論」 |
| 2026-10-30 | 無金鑰時 A3／A4 以固定流程執行 SKILL 標準步驟（A3：查結構 → 每指標一條 SQL → 沙箱 Python 算相關 → 畫圖 → 報告附全部 SQL 與 Python；A4：篩語料 → 時間軸 → 突增偵測）；有金鑰時由 Claude 用同一組工具，最後回傳 JSON 再經同樣驗證 | 示範不依賴外部 API，且產出可重跑 |
| 2026-10-30 | A3 分析只用完整週（排除本週）；資料截止＝最後完整週的週日 | 本週未結束，比例會失真 |
| 2026-10-30 | A2 合成逐里對照：只有「數字變化＋量測下降＋該里在地事件」三者都有的里才寫「可近性下降」，其餘里標「需人工了解」 | SKILL A2 守門規則 5：不挑對自己有利的一邊、不以偏概全 |
| 2026-10-30 | 新增 A4 工具 sentiment_search（篩去識別化語料、回來源連結與主題分佈）；SPEC §8.2 未列，但「委派查詢」產時間軸必需 | 既有工具只能算則數，無法列事件 |
| 2026-10-30 | A4 每 6 小時排程＝crawl_schedule＋逐（里, 主題）trend_detect＋alert_emit；同里同主題 3 天內不重發；A2 消費 sentiment.alert 後委派 A3（該里 8 週數字）＋A4（同期事件），對照結果寫回 anon.sentiment_alerts.analysis | SKILL A2「預警事件」；避免同一事件洗版 |
| 2026-10-30 | 示範輿情改為相對日期 fixture（days_ago），重建資料時北區光明里、中正里「據點關閉」抱怨永遠落在最近一週；落地時以（連結, 內容）去重 | 情境 6 在任何一天都可重現；轉貼與重抓不重複計數 |
| 2026-10-30 | 關懷清單 05:30 由 A2 呼叫 care_rules；各據點／類型數量只在 n ≥ k 時寫入 anon.care_list_daily（不含代號） | SKILL A2 每日批次第 4 步；anon 不寫 n < k 的列 |
| 2026-10-30 | 訪視回報存 idn.visit_reports（備註加密），每日 deidentify 聚合成 anon.visit_outcomes（週×里×任務類型×原因）；里的格子 n < k 不發布，且「全區 − 已發布里」落在 1..k-1 時再遮蔽最小的里 | 情境 5「去識別化後進 anon.visit_outcomes」；避免相減推回 |
| 2026-10-30 | 據點端一期由據點人員直接操作 API（A5 為二期 stub）：解碼必附任務編號、限本據點、逐次稽核；任務單只回姓名、電話、地址、訪綱 | SPEC §8.2 A5 二期；§10 據點人員端一期畫面 |
| 2026-10-30 | report_build 把圖表以 data URI 內嵌，報告自成一檔；下載時仍以 CSP sandbox 提供 | 報告不需再向外載入資源，也不能在本站執行腳本 |
| 2026-10-30 | 公衛儀表板地圖用 MapLibre 5.24＋OSM 點陣底圖，里界為示範用方格；被遮蔽的里一律灰色、點擊只顯示「已遮蔽（< k）」 | SPEC §16-3 預設 OSM＋MapLibre；示範資料沒有真實里界 |
| 2026-10-30 | KPI 以全區 ALL 列、完整週計算（週：最近一週 vs 前一週；月：近 4 週 vs 前 4 週，人數加權）；未控制率越低越好，其餘越高越好 | §9 /agency/kpi 未定義計算方式 |
| 2026-10-30 | 核銷 POST /merchants/{id}/verify：只能核銷本店、只能一次、兌換後 30 天內有效；成功只回品項與點數，被拒也寫稽核 | 商家看不到兌換者任何資料；SPEC 未定有效期，取 30 天 |
| 2026-10-30 | 商家月成效：自家交易筆數與品項（自己的交易紀錄）＋同類商家中位數（anon 週聚合）＋兌換者年齡層（人數 < k 遮蔽）；不含任何個人欄位 | SKILL A3「商家成效報告」；情境 7「月成效報告無個人」 |
| 2026-10-30 | 點數對帳 GET /admin/points/reconcile 只檢查不修改：逐人累加＝balance_after、每張券恰一筆等額扣點、發點−扣點＝總餘額；結果只有筆數與問題類型 | 帳本 append-only，錯誤以補償分錄處理而非改寫 |
| 2026-10-30 | 防作弊標記由管理者 POST /admin/points/reviews/{id} 放行（當天達標才補發點）或駁回；清單姓名遮罩 | SPEC §7.1 points_engine「標記待審」需要人工出口 |
| 2026-10-30 | 示範資料：模擬歷史只發到前天以前的點數，最近兩天由 seed 以正式 points.evaluate_day 判定（含防作弊），DEMO000010 的兩天作弊樣本因此被標記待審 | M6 驗收「作弊 fixture 被標記」；模擬不繞過正式規則 |
| 2026-10-30 | 介面登錄表編號：S1–S6＝六個資料來源、D1＝idn→anon 去識別化通道、D2＝事件匯流、T1＝LLM、T2＝推播、U1＝民眾 App、U2＝公衛儀表板、U3＝據點與商家端 | SPEC §12 只列編號範圍未定義內容 |
| 2026-10-30 | App 使用指標（DAU／MAU、7／30 日留存、推播開啟率、長者模式比例）由 deidentify 每日聚合進 anon.app_metrics_daily；活躍＝打開報告、和 A1 對話或讀通知；月活躍 < k 的日子不發布 | SKILL A3 KPI 清單；只發布全區總數與比例 |
| 2026-10-30 | KPI 目標為示範值（services/kpi.py DEFAULT_TARGETS，可由 sys.config kpi_targets 覆寫）；狀態：達標／未達標／達標但趨勢惡化＝需關注；POST /agency/kpi/report 產生 HTML 月報（附 SQL） | SKILL A3「每項附本期、上期、目標值、達成狀態」 |
| 2026-10-30 | API 速率限制以 Redis 固定視窗計數（多行程共用）：登入每帳號＋來源 IP 每分鐘 10 次、Agent 對話每帳號每分鐘 20 次；Redis 不可用時放行並記錄 | SPEC §11；防暴力猜密碼與 LLM 費用失控，不讓限流本身造成中斷 |
| 2026-10-30 | 效能量測以 scripts/perf.py 對全部 1,200 位模擬居民實跑每日批次後推估 10,000 人；首字回應以規則式 planner 量測（接上 Claude 需複測）；報告寫入 docs/perf-report.md | 開發機無法建立 1 萬人資料；以實測吞吐推估並誠實標示前提 |
| 2026-10-30 | 開發環境 `.env` 的 ANTHROPIC_API_KEY 取自使用者其他專案（經使用者同意；只比對雜湊、未顯示金鑰），已以最小呼叫驗證可用；測試仍強制清空金鑰，不呼叫外部 LLM | 使用者授權；`.env` 在 .gitignore |
| 2026-10-30 | 接上 Claude 實測 A1 對話：首字 11–13 秒（模型先思考與查工具才開口）；執行期情境改為「先用一句話說明要查什麼再呼叫工具、雙生快照已有的數字不必再查」後降到 2.8–7.4 秒，仍未穩定達到 SPEC §11 的 3 秒 | 誠實記錄；後續可讓 A1 對話改用較快的模型（CLAUDE_MODEL 可設定）或預先載入每日報告 |
| 2026-10-30 | 資料庫維持 PostgreSQL 16（使用者決定），不改 Firebase／Firestore；正式環境改用雲端 PostgreSQL 時只換 `.env` 連線並重跑 migration（需可建立角色的管理帳號） | idn／anon 分 schema、各角色 GRANT、RLS、k 遮蔽與八條約束測試都依賴 PostgreSQL；Firestore 無對應機制 |
| 2026-10-30 | 新增 M8（SPEC §17）：活動來源抓公開公告、推薦參考個人健康狀況、商家依偏好與評分門檻、有點數的活動顯示誘因、民眾健康儀表板（使用者決定）；一期就做，不等二期 A5 | 使用者明確要求；活動管理先由據點人員在工作台直接操作（同解碼），A5 的 `event_mgmt` 仍是二期 stub |
| 2026-10-30 | 活動放 `pub.events`、報名報到放 `idn.event_registrations`、偏好放 `idn.user_prefs`；參與統計只經 deidentify 以 `event_attend_rate` 進 `anon.cohort_weekly` | 活動是公開資訊；「誰參加血壓講座」本身透露健康狀況，屬個資 |
| 2026-10-30 | 公告匯入與商家評分同步只用 `anon_writer`（另授權 `pub.events` 寫入、`pub.merchants` 評分欄位更新），不接觸 `idn`；公告一律 `pending_review`，機關人員上架後才推給民眾 | 外部資料落地與輿情同一條低權限路徑；擷取錯日期讓長輩白跑一趟，傷害比不推更大 |
| 2026-10-30 | 活動報名列為高風險動作（`event_register`，走 C-5 確認）；取消報名不需確認 | 報名會把姓名與電話交給主辦單位，等同一次分享 |
| 2026-10-30 | 推薦的健康理由只在 App 內顯示並經 C-7 過濾；推播標題與內文只有活動名稱、時間、地點、點數；每人每週活動推播上限 2 則（`sys.config.event_push_weekly_cap`），可在偏好關閉 | 鎖定畫面旁人看得到；避免推播疲勞讓長輩把通知整個關掉 |
| 2026-10-30 | 商家評分門檻：本人設定優先，否則 `sys.config.merchant_min_rating`（預設 4.0）；無評分的店家不推薦；回覆附「已略過幾家」 | 使用者要求評分門檻；說明略過家數讓推薦可解釋 |
| 2026-10-30 | 示範商家由 12 家增為 18 家（新增 6 家餐飲：便當、素食、麵店等），各附評分與健康標籤 | 使用者要推薦餐廳，原 12 家多為非餐飲；需要有低於門檻的店家才能示範篩選 |
| 2026-10-30 | 民眾 App 分頁改為「今天／數據／活動／小幫手／點數／更多」，守護圈移入「更多」；圖表字級 18px | 新增兩個主要功能但不讓分頁超過 6 個；遵守最小字級 18px |
| 2026-10-30 | 活動報到給點：據點人員掃民眾 QR，限活動開始前 1 小時至結束後 2 小時，同一場只發一次 | 防作弊：報到需本人在現場出示 10 分鐘有效 QR |
| 2026-10-30 | 名額計算用 `idn.event_seats_taken()`（SECURITY DEFINER，只回人數，只授權 idn_app、idn_user_scoped） | A1 的 RLS 連線看不到別人的報名，又不能讓機關側角色碰到 idn |
| 2026-10-30 | 活動參與率的分母是「有 App 帳號者」；確認後執行失敗時，帶 i18n key 的業務錯誤（名額已滿等）把白話原因放進 `result.message`，其他錯誤不外露 | 背景居民沒有帳號、不可能報名；民眾需要知道為什麼報名失敗 |
| 2026-10-30 | 既有開發資料庫以 `scripts/seed_m8.py` 補上 M8 示範資料（可重跑、不重建）；新資料庫的 `make seed` 已內含 | 不清掉使用者在介面上已操作的資料 |
| 2026-10-30 | 雲端示範部署到 Render：根目錄 multi-stage Dockerfile（node 建四個前端＋python 後端，取代 `backend/Dockerfile`）、`render.yaml` Blueprint（PostgreSQL 16＋Key Value＋Docker 網站，區域 singapore，免費方案）；`DATABASE_URL` 覆蓋 PG_*；容器啟動時 migrate、資料庫為空才背景 seed（600 人、8 週）、同容器背景跑 worker | 使用者以 Render 部署，第一次因根目錄沒有 Dockerfile 失敗；免費方案沒有 shell、pre-deploy 與背景 worker |
| 2026-10-30 | `pyproject.toml` 補上 `anthropic`、`truststore`（原本只裝在本機 venv）；seed 結束時產生當天 A1 報告；正式環境（APP_ENV=prod）首頁導向民眾 App | 容器內呼叫 Claude 會缺套件；雲端新部署一打開就要有內容 |
| 2026-10-30 | 事件流長度上限改為設定 `EVENT_STREAM_MAXLEN`（預設 20 萬則）；Render 設 2 萬則 | 免費 Key Value 容量很小且為 noeviction，滿了事件會寫不進去；消費者即時處理，2 萬則足夠 |
