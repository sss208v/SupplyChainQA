# Supply Chain QA - Windows 部署脚本
# 需要: Docker Desktop

Write-Host "========================================" -ForegroundColor Blue
Write-Host "  Supply Chain QA - 生产部署 (Windows)" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue

# 1. 检查 Docker
Write-Host "[1/4] 检查 Docker..." -ForegroundColor Yellow
try {
    docker --version | Out-Null
    Write-Host "  Docker 已就绪" -ForegroundColor Green
} catch {
    Write-Host "  请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
    exit 1
}

# 2. 配置 .env
Write-Host "[2/4] 检查配置..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Copy-Item "deploy\.env.production" ".env"
    Write-Host "  已创建 .env，请编辑填入 DEEPSEEK_API_KEY" -ForegroundColor Yellow
    Write-Host "  编辑后重新运行此脚本" -ForegroundColor Yellow
    notepad .env
    exit 0
}

# 3. 构建
Write-Host "[3/4] 构建镜像..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml build

# 4. 启动
Write-Host "[4/4] 启动服务..." -ForegroundColor Yellow
docker compose -f docker-compose.prod.yml up -d

Write-Host ""
Write-Host "========================================" -ForegroundColor Blue
Write-Host "  部署完成！" -ForegroundColor Green
Write-Host "  访问: http://localhost" -ForegroundColor Green
Write-Host "  默认账号: admin / admin123" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Blue
