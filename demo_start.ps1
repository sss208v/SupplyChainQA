<#
.SYNOPSIS
  SmartQA Pro — 一键启动脚本（面试演示用）
.DESCRIPTION
  1. 检查 Docker Desktop 运行状态
  2. 启动 docker-compose（Milvus + Redis + PostgreSQL）
  3. 等待所有服务健康
  4. 后台启动后端 uvicorn（端口 8001）
  5. 后台启动前端 vite（端口 3000）
  6. 打开浏览器到 http://localhost:3000
#>

$PROJECT_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BACKEND_DIR  = Join-Path $PROJECT_ROOT "backend"
$FRONTEND_DIR = Join-Path $PROJECT_ROOT "frontend"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  SmartQA Pro 一键启动" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. 检查 Docker Desktop ----
Write-Host "[1/6] 检查 Docker Desktop..." -ForegroundColor Yellow
try {
    $dockerInfo = docker info 2>&1 | Out-String
    if ($dockerInfo -match "Server Version") {
        Write-Host "  ✅ Docker Desktop 运行中" -ForegroundColor Green
    } else {
        Write-Host "  ❌ Docker Desktop 未运行，请先启动 Docker Desktop" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "  ❌ 无法连接 Docker。请确认 Docker Desktop 已启动" -ForegroundColor Red
    exit 1
}

# ---- 2. 启动 Docker Compose（仅基础设施：Milvus + Redis + PostgreSQL）----
Write-Host "[2/6] 启动 Docker 服务 (Milvus + Redis + PostgreSQL)..." -ForegroundColor Yellow
$composeFile = Join-Path $PROJECT_ROOT "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    Write-Host "  ❌ 找不到 docker-compose.yml: $composeFile" -ForegroundColor Red
    exit 1
}

docker-compose -f $composeFile up -d etcd minio milvus redis postgres 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ❌ docker-compose 启动失败，错误码: $LASTEXITCODE" -ForegroundColor Red
    Write-Host "  请尝试手动执行: docker-compose -f `"$composeFile`" up -d" -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✅ Docker 服务已启动" -ForegroundColor Green

# ---- 3. 等待服务健康 ----
Write-Host "[3/6] 等待基础设施就绪..." -ForegroundColor Yellow

# 等待 Milvus
$maxRetries = 30
$retryCount = 0
$milvusReady = $false
while ($retryCount -lt $maxRetries) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:19530/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($health -match "ok|ready|true") {
            $milvusReady = $true
            break
        }
    } catch {}
    $retryCount++
    Write-Host "  ." -NoNewline -ForegroundColor Gray
    Start-Sleep -Seconds 2
}
if ($milvusReady) {
    Write-Host ""
    Write-Host "  ✅ Milvus 就绪 (${retryCount}s)" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  ⚠️  Milvus 等待超时 (${maxRetries}s)，继续启动..." -ForegroundColor Yellow
}

# 等待 Redis
try {
    $redisOk = docker compose -f $composeFile exec redis redis-cli ping 2>$null
    if ($redisOk -match "PONG") { Write-Host "  ✅ Redis 就绪" -ForegroundColor Green }
    else { Write-Host "  ⚠️  Redis 未就绪" -ForegroundColor Yellow }
} catch { Write-Host "  ⚠️  Redis 检查失败" -ForegroundColor Yellow }

# 等待 PostgreSQL
try {
    $pgOk = docker compose -f $composeFile exec postgres pg_isready -U smartqa -p 15432 2>$null
    if ($pgOk -match "accepting connections") { Write-Host "  ✅ PostgreSQL 就绪" -ForegroundColor Green }
    else { Write-Host "  ⚠️  PostgreSQL 未就绪" -ForegroundColor Yellow }
} catch { Write-Host "  ⚠️  PostgreSQL 检查失败" -ForegroundColor Yellow }

# ---- 4. 启动后端 ----
Write-Host "[4/6] 启动后端 (uvicorn :8001)..." -ForegroundColor Yellow
$venvPython = Join-Path $BACKEND_DIR "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "  ⚠️  找不到虚拟环境 Python: $venvPython" -ForegroundColor Yellow
    Write-Host "  尝试系统 Python..." -ForegroundColor Yellow
    $pythonCmd = "python"
} else {
    $pythonCmd = $venvPython
}

$backendLog = Join-Path $BACKEND_DIR "backend.log"
$backendJob = Start-Job -ScriptBlock {
    param($dir, $cmd, $log)
    Set-Location $dir
    & $cmd -m uvicorn app.main:app --host 0.0.0.0 --port 8001 *>&1 | Out-File -FilePath $log -Encoding utf8
} -ArgumentList $BACKEND_DIR, $pythonCmd, $backendLog

Write-Host "  ✅ 后端启动中 (Job Id: $($backendJob.Id))" -ForegroundColor Green
Write-Host "     日志: $backendLog" -ForegroundColor Gray

# 等待后端就绪
Start-Sleep -Seconds 5
$backendReady = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8001/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($resp.status) {
            $backendReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if ($backendReady) {
    Write-Host "  ✅ 后端就绪 (${i}s)" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  后端可能未完全就绪，请检查日志: $backendLog" -ForegroundColor Yellow
    Write-Host "     手动命令: $pythonCmd -m uvicorn app.main:app --host 0.0.0.0 --port 8001" -ForegroundColor Gray
}

# ---- 5. 启动前端 ----
Write-Host "[5/6] 启动前端 (vite :3000)..." -ForegroundColor Yellow
$frontendLog = Join-Path $FRONTEND_DIR "frontend.log"
if (Test-Path (Join-Path $FRONTEND_DIR "node_modules\.bin\vite.cmd")) {
    $viteCmd = Join-Path $FRONTEND_DIR "node_modules\.bin\vite.cmd"
} elseif (Get-Command npx -ErrorAction SilentlyContinue) {
    $viteCmd = "npx vite"
} else {
    Write-Host "  ❌ 找不到 vite，请确保 node_modules 已安装" -ForegroundColor Red
    Write-Host "     手动安装: cd `"$FRONTEND_DIR`" && npm install" -ForegroundColor Yellow
    exit 1
}

$frontendJob = Start-Job -ScriptBlock {
    param($dir, $log)
    Set-Location $dir
    npx vite --port 3000 *>&1 | Out-File -FilePath $log -Encoding utf8
} -ArgumentList $FRONTEND_DIR, $frontendLog

Write-Host "  ✅ 前端启动中 (Job Id: $($frontendJob.Id))" -ForegroundColor Green
Write-Host "     日志: $frontendLog" -ForegroundColor Gray

Start-Sleep -Seconds 3

# ---- 6. 打开浏览器 ----
Write-Host "[6/6] 打开浏览器..." -ForegroundColor Yellow
try {
    Start-Process "http://localhost:3000"
    Write-Host "  ✅ 浏览器已打开 http://localhost:3000" -ForegroundColor Green
} catch {
    Write-Host "  ⚡ 请手动打开 http://localhost:3000" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  启动完成！" -ForegroundColor Green
Write-Host "  前端: http://localhost:3000" -ForegroundColor White
Write-Host "  后端: http://localhost:8001/docs" -ForegroundColor White
Write-Host "  健康: http://localhost:8001/health" -ForegroundColor White
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "停止服务: docker-compose -f `"$composeFile`" down" -ForegroundColor Gray
Write-Host "查看后端日志: Get-Content `"$backendLog`" -Tail 50" -ForegroundColor Gray
Write-Host "查看前端日志: Get-Content `"$frontendLog`" -Tail 50" -ForegroundColor Gray
