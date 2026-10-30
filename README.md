# 健康大數據 App — 一期 PoC

規格：[SPEC.md](SPEC.md)｜工作指引：[CLAUDE.md](CLAUDE.md)｜決策紀錄：[docs/DECISIONS.md](docs/DECISIONS.md)
介面登錄表：[docs/interfaces.md](docs/interfaces.md)｜DPIA 骨架：[docs/DPIA.md](docs/DPIA.md)｜效能報告：[docs/perf-report.md](docs/perf-report.md)

目前進度：**M1–M8 完成**。六個資料來源（Mock）＋公開活動公告與商家評分 → 健康資料中台（idn／anon 兩區）→ 37 個工具 → A1–A4 Agent（A5 為二期 stub）→ 四個使用端。M8（SPEC §17）：近日社區活動推薦與報名、商家／餐廳推薦、民眾健康儀表板與月報。

## 快速開始（Windows，不需 Docker）

```powershell
.\make.ps1 setup        # 第一次：建立 ~/.venvs/healthapp 並安裝後端套件
.\make.ps1 web-install  # 第一次：安裝前端套件（pnpm）
.\make.ps1 web-build    # 建置四個前端
.\make.ps1 up           # 啟動 PostgreSQL + Redis + migrate + API + worker（背景）
.\make.ps1 seed         # 第一次：載入示範資料（1,200 位虛構居民、12 週歷程，已含 M8）
```

已經在用的開發資料庫要升級到 M8（不重建、保留既有資料）：

```powershell
.\make.ps1 migrate
cd backend; & "$env:USERPROFILE\.venvs\healthapp\Scripts\python.exe" -X utf8 -m scripts.seed_m8
```

| 網址 | 內容 |
|---|---|
| http://127.0.0.1:8000/app/citizen/ | **民眾 App**（PWA，手機尺寸最佳）：今日報告、健康數據儀表板與月報、近日活動（推薦、報名）、問小幫手（A1）、點數兌換與推薦店家、推薦偏好、守護圈、授權管理、長者模式 |
| http://127.0.0.1:8000/app/agency/ | **公衛儀表板**：KPI、熱區地圖、趨勢、輿情警訊、問 A2（委派 A3／A4）、談參、KPI 月報、活動（審核公開公告、上架、取消） |
| http://127.0.0.1:8000/app/site/ | **據點人員端**：今日工作板、掃 QR 代量測、活動（上架、報名名單、掃 QR 報到發點）、關懷代號解碼（需任務編號）、訪視回報 |
| http://127.0.0.1:8000/app/merchant/ | **商家端**：掃碼核銷、優惠上架、月成效 |
| http://127.0.0.1:8000/docs | API 文件（Swagger），右上角 Authorize 輸入示範帳密即可試打 |
| http://127.0.0.1:8000/console | M1 驗收台（開發環境限定）：八條約束結果、撤回流程 |

其他指令：`status`、`down`、`reset`（刪庫重建＋示範資料）、`test`、`lint`、`web`（前端開發模式）、`perf`（效能量測）。
Linux／macOS 用 `make <target>`（[Makefile](Makefile)）。PostgreSQL 與 Redis 資料在 `~/.healthapp/`，日誌在 `~/.healthapp/logs/`。

> 沒有設定 `ANTHROPIC_API_KEY` 時，A1–A4 以規則式 planner 運作（畫面標「示範模式」），工具呼叫、確認、稽核、過濾流程與接上 Claude 時完全相同。設定金鑰後自動改用 `claude-opus-5-5`。

## 示範帳號（密碼一律 `Demo2026!`，僅限開發環境）

| 帳號 | 角色 | 說明 |
|---|---|---|
| `wang.xiuying` | 民眾 | 王秀英，73 歲：血壓連三次偏高、步數下降、有健保就醫授權（情境 1–3 主角） |
| `wang.yijun` | 民眾 | 王怡君，王秀英的女兒（守護圈：只看摘要） |
| `citizen03` … `citizen50` | 民眾 | 其餘 48 位虛構民眾 |
| `site.s01` … `site.s06`、`volunteer.s03` | 據點人員／志工 | 6 個據點 |
| `merchant.m01` … `merchant.m18` | 商家 | 18 家特約商家（M13–M18 為餐飲；M18 刻意沒有評分） |
| `agency.north`、`agency.bureau` | 衛生所／衛生局 | 機關側 |
| `admin` | 系統管理者 | 稽核、設定、點數對帳與防作弊審核（API） |

所有人名、電話、地址皆為虛構。

## 驗收示範情境（SPEC §15）

| # | 情境 | 怎麼看 |
|---|---|---|
| 1 | 每日報告 → 回「好」→ 預約 | 民眾 App 以 `wang.xiuying` 登入：今日報告「待你決定」；或到「小幫手」問「我最近狀況怎麼樣？」再回「好」，按確認卡片 |
| 2 | 家屬視角 | `wang.yijun` 登入 → 守護圈 → 看今天狀態（只有狀態句、風險等級、待辦，沒有數值） |
| 3 | 撤回授權 | 民眾 App → 更多 → 授權管理 → 撤回（只有「確定要撤回嗎？撤回後您將失去：…」） |
| 4 | 機關問答 | 公衛儀表板 → 問 A2 →「北區高血壓未控制率為什麼上升？」（A3 附查詢編號與報告、A4 附來源、需承辦核定） |
| 5 | 關懷閉環 | 據點端 `site.s01` → 關懷任務 → 輸入任務編號解碼 → 訪視回報（每日彙整進 `anon.visit_outcomes`，n ≥ k 才公開） |
| 6 | 輿情預警 | 公衛儀表板 → 輿情警訊（光明里、中正里「據點開放」突增，A2 已對照數據） |
| 7 | 兌換 | 民眾 App → 點數 → 兌換 → 出示 QR；商家端核銷；月成效無個人 |
| 8 | 約束測試 | `.\make.ps1 test`；結果也在 `/console` 與 `GET /api/v1/admin/constraints` |
| 9 | 活動推薦（SPEC §17） | 公衛儀表板 → 活動：審核 3 則公開公告後上架；民眾 App（`wang.xiuying`）→ 活動：看推薦理由與依據 → 我要報名 → 確認；據點端 `site.s03` → 活動 → 報到與名單 → 貼上民眾「我的量測 QR」報到，點數入帳 |
| 10 | 商家推薦 | 民眾 App → 點數 →「推薦店家（評分 4.0 以上）」：只列評分達門檻的店，並說明略過幾家；門檻與偏好在「更多 → 推薦偏好」 |
| 11 | 健康儀表板 | 民眾 App → 數據：7／30／90 天圖表與統計、與前期及同齡比較（人數不足不比較）、產生月報（可列印）；撤回某來源後該項改顯示「未授權」 |

每日排程（worker）：04:30 商家評分、04:45 健保同步、05:00 每日批次（A1 報告＋去識別化）、05:30 關懷清單、07:00 推送（每日報告＋最推薦的一場活動，每週最多 2 則）、每小時穿戴／環境同步、每 6 小時 A4 輿情與公開活動公告匯入（待審）。

## 測試

```powershell
.\make.ps1 test      # check-schema（C-1 靜態掃描）+ pytest（含八條約束）
.\make.ps1 lint      # ruff + mypy --strict
```

- `backend/tests/constraints/`：SPEC §2.2 八條核心約束（C-1～C-8），打真的 PostgreSQL（角色、RLS、trigger 都是 DB 層行為）。
- 每次測試會重建 `healthapp_test` 資料庫，不影響開發資料；測試一律不呼叫外部 LLM。

## 目錄

```
backend/app/
  db/          模型（idn／anon／pub／sys）、migrations、角色 engine、跨區 guard
  adapters/    六個資料來源 Adapter（真實 client＋Mock）
  fhir/        FHIR 資源建構與唯一寫入入口（授權閘門）
  consent/     同意管理（撤回 → 事件 → 24h 內刪除）
  actions/     高風險動作確認狀態機（C-5）
  deidentify/  idn → anon 單向聚合、k-匿名與次要遮蔽（C-4）、strip_identity（C-8）
  tools/       工具層（每工具一檔，registry 統一做授權範圍、稽核）
  agents/      runtime（自建工具迴圈）、A1／A2／A3／A4、委派協定、輸出過濾（C-7）
  services/    健康資料、風險、點數、預約、守護圈、商家、對帳、KPI、沙箱
  api/         民眾、守護圈、據點、商家、機關、管理 API；SSE 對話；速率限制
backend/scripts/  localinfra、seed／simulate、check_schema、perf
web/              pnpm workspace：apps/citizen、agency、site、merchant；packages/ui（共用元件與長者模式 tokens）
skills/           五個 Agent 的 SKILL.md（就是 system prompt）
i18n/zh-TW.json   所有使用者可見文字
```
