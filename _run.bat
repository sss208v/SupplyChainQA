@echo off
cd /d C:\Users\sss208\Desktop\agent\supply-chain-qa\backend
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
venv\Scripts\python.exe -B -m uvicorn app.main:app --host 0.0.0.0 --port 8001
