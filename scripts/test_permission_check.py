"""测试 - 专门检查权限拦截"""
import urllib.request
import json

BASE_URL = "http://localhost:8001/api/v1"

def login(username, password):
    req = urllib.request.Request(
        f"{BASE_URL}/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        print(f"Login: role={data['user']['role']}, dept={data['user']['department']}")
        return data["token"]

def test(query):
    token = login("finance", "123456")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=json.dumps({"query": query, "stream": True}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST"
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
                        print(f"  BLOCKED: {e}")
                        blocked = True
                    elif t == "tool_status":
                        print(f"  tool_status: {e}")
                    elif t == "route":
                        print(f"  route: intent={e['intent']}, tool={e.get('tool_name', 'N/A')}")
                except: pass
        if not blocked:
            print("  NOT BLOCKED - permission check FAILED")

print("Test: Finance user calling '查一下库存 MAT-001'")
print("Expected: BLOCKED (finance cannot call query_inventory)")
test("查一下库存 MAT-001")
