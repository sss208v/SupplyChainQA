# start.ps1 - Supply Chain QA 一键启动（向日葵/本地演示用）
# 前置: Docker Desktop 已启动

Set-Location $PSScriptRoot

Write-Host "=== Supply Chain QA 启动 ===" -ForegroundColor Cyan

# Step 1: Docker 基础设施
Write-Host "[1/4] Docker 基础设施..." -ForegroundColor Yellow
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker 启动失败！" -ForegroundColor Red
    exit 1
}

# Step 2: 后端
Write-Host "[2/4] 启动后端 (port 8001)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "cd '$PSScriptRoot/backend'; Write-Host 'Supply Chain QA 后端 :8001' -ForegroundColor Green; ./venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload"

# Step 3: 前端
Write-Host "[3/4] 启动前端 (port 5173)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "cd '$PSScriptRoot/frontend'; Write-Host 'Supply Chain QA 前端 :5173' -ForegroundColor Green; npm run dev"

# Step 4: 等待就绪
Write-Host "[4/4] 等待服务就绪..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

Write-Host ""
Write-Host "=== 启动完成 ===" -ForegroundColor Green
Write-Host "  前端: http://localhost:5173"
Write-Host "  后端: http://localhost:8001/docs"
Write-Host "  Chat: http://localhost:5173/chat"
Write-Host ""
Write-Host "向日葵远程访问: 直接在台式机浏览器打开上述地址" -ForegroundColor Cyan
