# Test backend import first
Set-Location "C:\Users\sss208\Desktop\agent\supply-chain-qa\backend"
Write-Host "Testing Python import..."
& ".\venv\Scripts\python.exe" -c "from app.main import app; print('import success')" 2>&1

Write-Host "Starting uvicorn..."
& ".\venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
