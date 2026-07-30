"""
SupplyChainRAG - MCP (Model Context Protocol) 客户端

实现 MCP 协议的工具调用接口，让 Agent 可以通过标准协议
调用外部 MCP Server 提供的工具。

MCP 协议要点：
- JSON-RPC 2.0 通信
- tools/list: 列出可用工具
- tools/call: 调用工具
- 支持 stdio 和 HTTP 传输

当前实现：HTTP 传输（Streamable HTTP）
参考：https://modelcontextprotocol.io/specification/2025-03-26
"""
import json
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)


class MCPTool:
    """MCP 工具描述"""
    def __init__(self, name: str, description: str, input_schema: dict, server_url: str):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.server_url = server_url

    def to_langchain_tool(self):
        """转换为 LangChain StructuredTool"""
        from langchain_core.tools import StructuredTool
        from pydantic import create_model

        # 从 MCP input_schema 构建 Pydantic 模型
        fields = {}
        properties = self.input_schema.get("properties", {})
        required = self.input_schema.get("required", [])
        for prop_name, prop_def in properties.items():
            field_type = str  # 默认字符串
            if prop_def.get("type") == "integer":
                field_type = int
            elif prop_def.get("type") == "number":
                field_type = float
            elif prop_def.get("type") == "boolean":
                field_type = bool

            default = ... if prop_name in required else None
            fields[prop_name] = (field_type, default)

        ArgsModel = create_model(f"{self.name}_args", **fields)
        tool_ref = self  # 闭包引用

        async def _call(**kwargs):
            return await mcp_call_tool(tool_ref.server_url, tool_ref.name, kwargs)

        return StructuredTool(
            name=self.name,
            description=self.description,
            args_schema=ArgsModel,
            coroutine=_call,
        )


class MCPClient:
    """MCP 协议客户端

    支持连接多个 MCP Server，列出和调用工具。
    """

    def __init__(self):
        self._servers: dict[str, list[MCPTool]] = {}  # url -> tools
        self._session_id: Optional[str] = None

    async def connect(self, server_url: str) -> list[MCPTool]:
        """连接 MCP Server 并获取工具列表

        Args:
            server_url: MCP Server 的 HTTP 端点

        Returns:
            可用工具列表
        """
        try:
            tools = await mcp_list_tools(server_url)
            self._servers[server_url] = tools
            logger.info(f"[MCP] 连接 {server_url} 成功，发现 {len(tools)} 个工具")
            return tools
        except Exception as e:
            logger.error(f"[MCP] 连接 {server_url} 失败: {e}")
            return []

    def get_all_tools(self) -> list[MCPTool]:
        """获取所有已连接 Server 的工具"""
        all_tools = []
        for tools in self._servers.values():
            all_tools.extend(tools)
        return all_tools

    def get_langchain_tools(self) -> list:
        """获取所有工具的 LangChain StructuredTool 版本"""
        return [t.to_langchain_tool() for t in self.get_all_tools()]


async def mcp_list_tools(server_url: str) -> list[MCPTool]:
    """调用 MCP tools/list 获取工具列表

    Args:
        server_url: MCP Server HTTP 端点

    Returns:
        MCPTool 列表
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            server_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")

    tools_data = data.get("result", {}).get("tools", [])
    return [
        MCPTool(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("inputSchema", {}),
            server_url=server_url,
        )
        for t in tools_data
    ]


async def mcp_call_tool(server_url: str, tool_name: str, arguments: dict) -> str:
    """调用 MCP 工具

    Args:
        server_url: MCP Server HTTP 端点
        tool_name: 工具名称
        arguments: 工具参数

    Returns:
        工具执行结果（字符串）
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            server_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

    if "error" in data:
        raise RuntimeError(f"MCP tool error: {data['error']}")

    result = data.get("result", {})
    content = result.get("content", [])
    # 合并所有 content block
    parts = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)


# 模块级单例
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
