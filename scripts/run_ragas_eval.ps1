# Supply Chain QA - RAGAS 一键评估脚本
# 使用方法: 在 PowerShell 中执行: .\run_ragas_eval.ps1

$ErrorActionPreference = "Stop"
$backendDir = "$PSScriptRoot\backend"
$python = "$backendDir\venv\Scripts\python.exe"

Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Supply Chain QA - RAGAS 评估 (供应链知识库)" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan

# 检查 Python
if (!(Test-Path $python)) {
    Write-Host "ERROR: 找不到 Python venv: $python" -ForegroundColor Red
    exit 1
}

# 检查 Milvus
Write-Host "`n[检查] Milvus 连接..." -ForegroundColor Yellow
try {
    $milvusCheck = & $python -c "from pymilvus import connections; connections.connect(host='localhost', port='19530'); print('OK'); connections.disconnect('default')" 2>&1
    if ($milvusCheck -match "OK") {
        Write-Host "  Milvus: 已连接" -ForegroundColor Green
    } else {
        Write-Host "  Milvus: 连接失败 - $milvusCheck" -ForegroundColor Red
        Write-Host "  请确保 Milvus 正在运行 (docker ps)" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "  Milvus: 连接异常 - $_" -ForegroundColor Red
    exit 1
}

# 运行评估
Write-Host "`n[开始] RAGAS 完整评估 (20题)..." -ForegroundColor Yellow
Write-Host "  预计耗时: 10-15 分钟 (取决于 DeepSeek API 速度)" -ForegroundColor Gray
Write-Host ""

Set-Location $backendDir
& $python eval\run_ragas_full_sc.py

# 检查结果
$resultJson = "$backendDir\eval\eval_ragas_result_full_sc.json"
if (Test-Path $resultJson) {
    Write-Host ("`n" + "=" * 60) -ForegroundColor Green
    Write-Host "  评估完成!" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green

    $result = Get-Content $resultJson -Raw | ConvertFrom-Json
    Write-Host "`n  最终指标:" -ForegroundColor Cyan
    Write-Host "  faithfulness:      $($result.metrics.faithfulness)" -ForegroundColor White
    Write-Host "  answer_relevancy:  $($result.metrics.answer_relevancy)" -ForegroundColor White
    Write-Host "  context_precision: $($result.metrics.context_precision)" -ForegroundColor White
    Write-Host "  context_recall:    $($result.metrics.context_recall)" -ForegroundColor White

    Write-Host "`n  报告文件:" -ForegroundColor Cyan
    Write-Host "  JSON: eval\eval_ragas_result_full_sc.json" -ForegroundColor Gray
    Write-Host "  CSV:  eval\eval_result_full_sc.csv" -ForegroundColor Gray
    Write-Host "  MD:   eval\eval_report_full_sc_*.md" -ForegroundColor Gray
} else {
    Write-Host "`nERROR: 未找到结果文件，评估可能失败" -ForegroundColor Red
}
