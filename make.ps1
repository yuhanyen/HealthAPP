<#
健康大數據 App — Windows 版常用指令（對應 Makefile）。
用法：.\make.ps1 <target>
  setup        建立 venv（~\.venvs\healthapp）並安裝相依套件
  up           一鍵啟動：PostgreSQL + Redis + migrate + API(:8000) + worker（背景執行）
  down         停止 API、worker、Redis、PostgreSQL
  status       各服務狀態
  infra        只啟動 PostgreSQL + Redis
  migrate      alembic upgrade head
  seed         載入示範資料
  reset        刪庫重建 + migrate + seed
  api          前景執行 API（--reload）
  worker       前景執行 worker
  test         check-schema + pytest（含八條約束測試）
  check-schema C-1 靜態掃描
  lint         ruff + mypy --strict
  perf         效能量測（SPEC §11），輸出 docs/perf-report.md
  web-install  安裝前端相依套件（pnpm）
  web-build    建置四個前端；API 會掛在 http://127.0.0.1:8000/app/<名稱>/
  web          前端開發模式（Vite，/api 代理到 :8000）
#>
param([string]$Target = "help")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Venv = Join-Path $env:USERPROFILE ".venvs\healthapp"
$Py = Join-Path $Venv "Scripts\python.exe"
$RunDir = Join-Path $env:USERPROFILE ".healthapp\run"
$LogDir = Join-Path $env:USERPROFILE ".healthapp\logs"
$env:PYTHONUTF8 = "1"

function Invoke-Backend([string[]]$PyArgs) {
    Push-Location $Backend
    # PowerShell 5.1 會把原生程式寫到 stderr 的日誌（如 alembic 的 INFO）當成錯誤；改以 exit code 判斷
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Py @PyArgs 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { throw "指令失敗（exit $LASTEXITCODE）：python $($PyArgs -join ' ')" }
    } finally {
        $ErrorActionPreference = $saved
        Pop-Location
    }
}

function Invoke-Web([string[]]$PnpmArgs) {
    # Node 24 的 fs.rmSync 在本專案路徑（全形字）會讓行程崩潰：預先載入 JS 版替代（見 web/scripts/rmsync-shim.cjs）
    $shim = (Join-Path $Root "web\scripts\rmsync-shim.cjs") -replace "\\", "/"
    $env:NODE_OPTIONS = "--require `"$shim`""
    Push-Location (Join-Path $Root "web")
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & pnpm @PnpmArgs 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { throw "指令失敗（exit $LASTEXITCODE）：pnpm $($PnpmArgs -join ' ')" }
    } finally {
        $ErrorActionPreference = $saved
        Pop-Location
    }
}

function Start-Background([string]$Name, [string[]]$PyArgs) {
    New-Item -ItemType Directory -Force $RunDir, $LogDir | Out-Null
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (Test-Path $pidFile) {
        $old = Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
        if ($old) { Write-Host "$Name 已在執行（PID $($old.Id)）"; return }
    }
    $p = Start-Process -FilePath $Py -ArgumentList $PyArgs -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") -RedirectStandardError (Join-Path $LogDir "$Name.err.log")
    Set-Content -Path $pidFile -Value $p.Id
    Write-Host "$Name 已啟動（PID $($p.Id)，日誌 $LogDir\$Name.*.log）"
}

function Stop-Background([string]$Name) {
    $pidFile = Join-Path $RunDir "$Name.pid"
    if (-not (Test-Path $pidFile)) { return }
    $proc = Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
    if ($proc) { Stop-Process -Id $proc.Id -Force; Write-Host "$Name 已停止" }
    Remove-Item $pidFile -Force
}

switch ($Target) {
    "setup" {
        if (-not (Test-Path $Py)) { python -m venv $Venv }
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -e "$Backend[dev]"
    }
    "infra" { Invoke-Backend @("-m", "scripts.localinfra", "start") }
    "migrate" { Invoke-Backend @("-m", "alembic", "upgrade", "head") }
    "seed" { Invoke-Backend @("-m", "scripts.seed") }
    "reset" { Invoke-Backend @("-m", "scripts.seed", "--reset") }
    "api" { Invoke-Backend @("-m", "uvicorn", "app.main:app", "--reload", "--port", "8000") }
    "worker" { Invoke-Backend @("-m", "app.worker") }
    "up" {
        Invoke-Backend @("-m", "scripts.localinfra", "start")
        Invoke-Backend @("-m", "alembic", "upgrade", "head")
        Start-Background "api" @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
        Start-Background "worker" @("-m", "app.worker")
        Write-Host ""
        Write-Host "API 文件（可直接試打）：http://127.0.0.1:8000/docs"
        Write-Host "民眾 App：http://127.0.0.1:8000/app/citizen/　公衛儀表板：/app/agency/　據點端：/app/site/　商家端：/app/merchant/"
        Write-Host "（第一次請先 .\make.ps1 web-install 與 web-build）"
    }
    "down" {
        Stop-Background "api"
        Stop-Background "worker"
        Invoke-Backend @("-m", "scripts.localinfra", "stop")
    }
    "status" {
        Invoke-Backend @("-m", "scripts.localinfra", "status")
        foreach ($name in "api", "worker") {
            $pidFile = Join-Path $RunDir "$name.pid"
            $alive = (Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)
            Write-Host ("{0}：{1}" -f $name, $(if ($alive) { "執行中" } else { "未啟動" }))
        }
    }
    "check-schema" { Invoke-Backend @("-m", "scripts.check_schema") }
    "test" {
        Invoke-Backend @("-m", "scripts.check_schema")
        Invoke-Backend @("-m", "pytest")
    }
    "lint" {
        Invoke-Backend @("-m", "ruff", "check", ".")
        Invoke-Backend @("-m", "ruff", "format", "--check", ".")
        Invoke-Backend @("-m", "mypy", "app", "scripts", "tests")
    }
    "perf" { Invoke-Backend @("-m", "scripts.perf") }
    "web-install" { Invoke-Web @("install") }
    "web-build" { Invoke-Web @("build") }
    "web" {
        # 開發模式：Vite（:5173 起）熱更新，/api 代理到 :8000；正式版請用 web-build，由 API 掛在 /app/<名稱>/
        Invoke-Web @("dev")
    }
    default { Get-Help $PSCommandPath; Write-Host "可用 target：setup up down status infra migrate seed reset api worker test check-schema lint perf web-install web-build web" }
}
