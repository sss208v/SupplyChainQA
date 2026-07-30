# Kill existing backend on port 8001
$conn = Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "Killing existing process on port 8001..."
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Start backend
Write-Host "Starting backend..."
Set-Location "C:\Users\sss208\Desktop\agent\supply-chain-qa\backend"
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "cd C:\Users\sss208\Desktop\agent\supply-chain-qa\backend; & .\venv\Scripts\Activate.ps1; uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload"
Start-Sleep -Seconds 8

# Verify
if (Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue) {
    Write-Host "SUCCESS: Backend is running on port 8001"
} else {
    Write-Host "FAILURE: Backend is not running"
}
