"""直接测试权限函数"""
import sys
sys.path.insert(0, 'C:/Users/sss208/Desktop/agent/supply-chain-qa/backend')

from app.api.tool import _is_tool_allowed, _get_allowed_tools

print("=== 测试 _is_tool_allowed ===")
print(f"finance -> query_inventory: {_is_tool_allowed('query_inventory', 'finance')}")
print(f"finance -> get_datetime: {_is_tool_allowed('get_datetime', 'finance')}")
print(f"purchase -> query_inventory: {_is_tool_allowed('query_inventory', 'purchase')}")

print()
print("=== 测试 _get_allowed_tools ===")
print(f"finance 可用: {_get_allowed_tools('finance')}")
print(f"purchase 可用: {_get_allowed_tools('purchase')}")
