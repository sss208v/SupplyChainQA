"""测试工具权限 - 直接验证意图路由和权限拦截"""
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
        return json.loads(resp.read())["token"]

def test_chat_stream(token, query):
    """测试流式接口"""
    req = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=json.dumps({"query": query, "stream": True}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            line = line.decode("utf-8", errors="replace").rstrip()
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    t = event.get("type")
                    if t in ("route", "tool_blocked", "tool_status"):
                        print(f"  [{t}] {event}")
                except:
                    pass

# Test 1: Finance user asking about inventory - should be BLOCKED
print("=" * 60)
print("测试 Finance 用户 - '查一下物料 MAT-001 的库存'")
print("Finance 可用工具: get_datetime, get_knowledge")
print("预期: 权限拦截")
print("=" * 60)
token = login("finance", "123456")
test_chat_stream(token, "查一下物料 MAT-001 的库存")

print()
print("=" * 60)
print("测试 Finance 用户 - '现在几点'")
print("预期: get_datetime 允许调用")
print("=" * 60)
test_chat_stream(token, "现在几点")

print()
print("=" * 60)
print("测试 Purchase 用户 - '查一下物料 MAT-001 的库存'")
print("Purchase 可用工具: 全部5个")
print("预期: 正常调用")
print("=" * 60)
token2 = login("purchase", "123456")
test_chat_stream(token2, "查一下物料 MAT-001 的库存")
