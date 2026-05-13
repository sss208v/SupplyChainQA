"""测试 Chat 接口权限校验 - 打印所有事件"""
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
    """测试流式接口，打印所有事件"""
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
                    print("  [DONE]")
                    break
                try:
                    event = json.loads(data)
                    print(f"  [{event.get('type')}] {event}")
                except:
                    print(f"  [raw] {line}")

print("=" * 60)
print("测试 Finance 用户调用 query_inventory")
print("=" * 60)
token = login("finance", "123456")
test_chat_stream(token, "查一下物料 MAT-001 的库存")
