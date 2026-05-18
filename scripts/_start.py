"""后端启动器 — Windows CREATE_NEW_PROCESS_GROUP 确保端口绑定独立"""
import subprocess, sys, time

BACKEND = r"C:\Users\sss208\Desktop\agent\supply-chain-qa\backend"
PYTHON = fr"{BACKEND}\venv\Scripts\python.exe"

proc = subprocess.Popen(
    [PYTHON, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"],
    cwd=BACKEND,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True,
)
print(f"PID: {proc.pid}")

# Wait for startup
import threading
def watch():
    for line in proc.stdout:
        line = line.strip()
        if line:
            print(line)
        if "Application startup complete" in line:
            print(">>> STARTUP COMPLETE <<<")
            break

t = threading.Thread(target=watch, daemon=True)
t.start()
t.join(timeout=60)

# Check health
import urllib.request
try:
    resp = urllib.request.urlopen("http://localhost:8001/health", timeout=5)
    print(f"HEALTH: {resp.read().decode()[:200]}")
except Exception as e:
    print(f"HEALTH FAIL: {e}")

sys.exit(0)
