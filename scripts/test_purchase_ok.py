"""验证 purchase 用户可以正常调用 query_inventory"""
import urllib.request
import json

token = json.loads(urllib.request.urlopen(urllib.request.Request(
    "http://localhost:8001/api/v1/auth/login",
    data=json.dumps({"username": "purchase", "password": "123456"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST"
)).read())["token"]
print("Purchase token: role=purchase")

req = urllib.request.Request(
    "http://localhost:8001/api/v1/chat/stream",
    data=json.dumps({"query": "查一下物料 MAT-001 的库存", "stream": True}).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST"
)
with urllib.request.urlopen(req) as resp:
    blocked = False
    for line in resp:
        line = line.decode("utf-8", errors="replace").rstrip()
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]": break
            try:
                e = json.loads(data)
                t = e.get("type")
                if t == "tool_blocked":
                    print(f"BLOCKED: {e}")
                    blocked = True
                elif t == "tool_call":
                    obs = e.get("observation", "N/A")[:60]
                    print(f"tool_call: tool={e['tool']} observation={obs}")
            except:
                pass
    if not blocked:
        print("Allowed - permission check OK for purchase user")
