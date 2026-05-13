"""精确测试权限逻辑"""
import sys
sys.path.insert(0, 'C:/Users/sss208/Desktop/agent/supply-chain-qa/backend')

# 直接测试权限检查逻辑
from app.api.tool import _is_tool_allowed, ROLE_TOOLS

print("=== ROLE_TOOLS ===")
for role, tools in ROLE_TOOLS.items():
    print(f"  {role}: {tools}")

print()
print("=== _is_tool_allowed ===")
print(f"  finance + query_inventory = {_is_tool_allowed('query_inventory', 'finance')}")
print(f"  finance + get_datetime = {_is_tool_allowed('get_datetime', 'finance')}")

# 模拟 chat.py 的逻辑
import asyncio
from starlette.requests import Request
from starlette.datastructures import Headers

async def test_role_extraction():
    # 模拟获取 token
    import urllib.request
    import json

    login_req = urllib.request.Request(
        "http://localhost:8001/api/v1/auth/login",
        data=json.dumps({"username": "finance", "password": "123456"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(login_req) as resp:
        token = json.loads(resp.read())["token"]

    print(f"\nToken: {token}")

    # 模拟 chat.py 的角色获取
    from app.core.auth import get_current_user_full

    # 构造假请求
    class FakeRequest:
        def __init__(self, token):
            self.headers = Headers({"authorization": f"Bearer {token}"})

    fake_request = FakeRequest(token)
    user = await get_current_user_full(fake_request)
    print(f"get_current_user_full result: {user}")
    if user:
        role = user.get("role", "purchase")
        print(f"Extracted role: {role}")
        print(f"Can call query_inventory? {_is_tool_allowed('query_inventory', role)}")

asyncio.run(test_role_extraction())
