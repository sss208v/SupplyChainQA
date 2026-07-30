# start-dev.ps1 - 一键启动 Supply Chain QA 开发环境
# 用法: 在项目根目录执行 .\start-dev.ps1
# 前置条件: Docker Desktop 已启动、backend/venv 已创建、依赖已安装

Set-Location $PSScriptRoot

$backendPort = 8001

function Test-BackendHealth {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$backendPort/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Host "Step 1/3: 启动 Docker 基础设施（10 个服务）..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose up 失败，请检查 Docker 服务" -ForegroundColor Red
    exit 1
}

Write-Host "Step 2/3: 等待后端健康检查（约 60-90 秒）..." -ForegroundColor Cyan
$maxWait = 120
$elapsed = 0
while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds 5
    $elapsed += 5
    if (Test-BackendHealth) {
        Write-Host "后端健康检查通过 http://localhost:$backendPort/health" -ForegroundColor Green
        break
    }
}
if (-not (Test-BackendHealth)) {
    Write-Host "后端未在 $maxWait 秒内就绪，请运行 docker compose ps 排查" -ForegroundColor Red
    exit 1
}

Write-Host "Step 3/3: 启动后端开发服务..." -ForegroundColor Cyan
Set-Location backend
Start-Process powershell -ArgumentList "-NoExit","-Command","./venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port $backendPort --reload"

Write-Host "启动完成。API 文档: http://localhost:$backendPort/docs" -ForegroundColor Green
