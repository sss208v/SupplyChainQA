@echo off
for /f "tokens=5" %%a in ('netstat -ano ^| find ":8001" ^| find "LISTENING"') do taskkill /F /PID %%a 2>nul
