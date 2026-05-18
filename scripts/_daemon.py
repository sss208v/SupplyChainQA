import subprocess, time, urllib.request, os, sys

BACKEND = r"C:\Users\sss208\Desktop\agent\supply-chain-qa\backend"
PYTHON = os.path.join(BACKEND, "venv", "Scripts", "python.exe")

# Clear pycache
for root, dirs, files in os.walk(BACKEND):
    if "__pycache__" in dirs:
        cache = os.path.join(root, "__pycache__")
        for f in os.listdir(cache):
            os.remove(os.path.join(cache, f))
        os.rmdir(cache)

# Start
proc = subprocess.Popen(
    [PYTHON, "-B", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"],
    cwd=BACKEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
print(f"PID: {proc.pid}")

# Wait
for i in range(30):
    try:
        r = urllib.request.urlopen("http://localhost:8001/health", timeout=3)
        if r.status == 200:
            print("READY")
            break
    except:
        time.sleep(2)
else:
    print("TIMEOUT")

# Keep alive
proc.wait()
