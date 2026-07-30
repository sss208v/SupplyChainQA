# Supply Chain QA - ngrok 演示启动
# 用法: .\demo_ngrok.ps1
# 手机访问 ngrok 输出的公网 URL 即可

$root = $PSScriptRoot
Set-Location $root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Supply Chain QA - ngrok 手机演示" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ---- 检查 ngrok ----
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Host "ngrok 未安装，正在用 winget 安装..." -ForegroundColor Yellow
    winget install ngrok
}
Write-Host ("[OK] ngrok " + ((ngrok version 2>&1 | Select-Object -First 1) -replace 'ngrok version ','')) -ForegroundColor Green

# ---- 启动 Docker 基础设施（如果没运行） ----
Write-Host ""
Write-Host "[1/4] Docker 基础设施..." -ForegroundColor Yellow
$dockerRunning = $false
try {
    docker ps 2>&1 | Out-Null
    $dockerRunning = $true
} catch {
    Write-Host "  Docker 未运行，请先启动 Docker Desktop" -ForegroundColor Red
    Write-Host "  然后运行: docker-compose up -d etcd minio milvus redis postgres neo4j" -ForegroundColor Yellow
    Write-Host "  或跳过数据库，以 DEMO_MODE 启动后端（离线模式）" -ForegroundColor Yellow
}

# ---- 启动后端 ----
Write-Host ""
Write-Host "[2/4] 启动后端 (FastAPI)..." -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8001/health" -TimeoutSec 2 -UseBasicParsing
    Write-Host "  后端已运行" -ForegroundColor Green
} catch {
    Write-Host "  启动中..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell" -ArgumentList "-Command", "cd '$root\backend'; .\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001" -WindowStyle Minimized
    Start-Sleep -Seconds 5
}

# ---- 启动前端 ----
Write-Host ""
Write-Host "[3/4] 启动前端 (Vue3)..." -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri "http://localhost:3000" -TimeoutSec 2 -UseBasicParsing
    Write-Host "  前端已运行" -ForegroundColor Green
} catch {
    Write-Host "  启动中..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell" -ArgumentList "-Command", "cd '$root\frontend'; npm run dev" -WindowStyle Minimized
    Start-Sleep -Seconds 5
}

# ---- 启动 ngrok ----
Write-Host ""
Write-Host "[4/4] 启动 ngrok 隧道..." -ForegroundColor Yellow
Write-Host ""

$lanIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\.168\.' } | Select-Object -First 1).IPAddress
if ($lanIP) {
    Write-Host "  WiFi 局域网: http://${lanIP}:3000" -ForegroundColor Gray
}

Write-Host "========================================" -ForegroundColor Green
Write-Host "  下方出现 Forwarding 地址后，"
Write-Host "  手机浏览器打开该地址即可演示"
Write-Host "  按 Ctrl+C 停止"
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

ngrok http http://localhost:3000 --log=stdout
