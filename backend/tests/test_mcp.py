"""
MCP Client 单元测试

测试：MCPTool、MCPClient、工具转换、协议调用。
所有外部 HTTP 调用使用 mock。
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


async def _call_mcp(mock_response, func):
    """在 mock httpx.AsyncClient 环境下执行 func()。"""
    mock_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance
        return await func()


class TestMCPTool:
    def test_create_tool(self):
        from app.core.mcp_client import MCPTool
        tool = MCPTool(
            name="get_weather",
            description="Get weather for a city",
            input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            server_url="http://localhost:8080",
        )
        assert tool.name == "get_weather"
        assert tool.description == "Get weather for a city"

    def test_to_langchain_tool(self):
        from app.core.mcp_client import MCPTool
        tool = MCPTool(
            name="calc",
            description="Calculate",
            input_schema={"type": "object", "properties": {"expr": {"type": "string"}}, "required": ["expr"]},
            server_url="http://localhost:8080",
        )
        lc_tool = tool.to_langchain_tool()
        assert lc_tool.name == "calc"
        assert lc_tool.description == "Calculate"


class TestMCPClient:
    def test_init(self):
        from app.core.mcp_client import MCPClient
        client = MCPClient()
        assert client.get_all_tools() == []
        assert client.get_langchain_tools() == []

    def test_get_all_tools_after_connect(self):
        from app.core.mcp_client import MCPClient, MCPTool
        client = MCPClient()
        tool = MCPTool("t1", "desc", {}, "http://s1")
        client._servers["http://s1"] = [tool]
        assert len(client.get_all_tools()) == 1

    @pytest.mark.asyncio
    async def test_connect_calls_list_tools(self):
        from app.core.mcp_client import MCPClient
        client = MCPClient()
        with patch("app.core.mcp_client.mcp_list_tools", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            result = await client.connect("http://test:8080")
            mock_list.assert_called_once_with("http://test:8080")
            assert result == []


class TestMCPListTools:
    @pytest.mark.asyncio
    async def test_parse_tools_response(self):
        from app.core.mcp_client import mcp_list_tools
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "tool1", "description": "First tool", "inputSchema": {"type": "object"}},
                    {"name": "tool2", "description": "Second tool", "inputSchema": {}},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        tools = await _call_mcp(mock_response, lambda: mcp_list_tools("http://test:8080"))
        assert len(tools) == 2
        assert tools[0].name == "tool1"


class TestMCPCallTool:
    @pytest.mark.asyncio
    async def test_parse_call_response(self):
        from app.core.mcp_client import mcp_call_tool
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "result data"}]
            }
        }
        mock_response.raise_for_status = MagicMock()
        result = await _call_mcp(mock_response, lambda: mcp_call_tool("http://test:8080", "tool1", {"arg": "val"}))
        assert result == "result data"

    @pytest.mark.asyncio
    async def test_error_response(self):
        from app.core.mcp_client import mcp_call_tool
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid request"}
        }
        mock_response.raise_for_status = MagicMock()
        with pytest.raises(RuntimeError, match="MCP tool error"):
            await _call_mcp(mock_response, lambda: mcp_call_tool("http://test:8080", "tool1", {}))


class TestSingleton:
    def test_returns_same_instance(self):
        from app.core.mcp_client import get_mcp_client
        a = get_mcp_client()
        b = get_mcp_client()
        assert a is b
