"""验证权限检查生效"""
import urllib.request
import json
import sys

# Login as finance
token = json.loads(urllib.request.urlopen(urllib.request.Request(
    "http://localhost:8001/api/v1/auth/login",
    data=json.dumps({"username": "finance", "password": "123456"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST"
)).read())["token"]

req = urllib.request.Request(
    "http://localhost:8001/api/v1/chat/stream",
    data=json.dumps({"query": "查一下物料 MAT-001 的库存", "stream": True}).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST"
)
with urllib.request.urlopen(req) as resp:
    for line in resp:
        line = line.decode("utf-8", errors="replace").rstrip()
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]": break
            try:
                e = json.loads(data)
                t = e.get("type")
                if t == "tool_blocked":
                    print(f"RESULT: BLOCKED - Finance cannot call {e['tool']}")
                    sys.exit(0)
                elif t == "tool_call":
                    print(f"RESULT: ALLOWED - Finance CAN call {e['tool']} (FAIL)")
                    sys.exit(1)
                elif t == "route":
                    print(f"Route: {e['intent']}")
            except:
                pass
    print("RESULT: No tool event received")
    sys.exit(2)
