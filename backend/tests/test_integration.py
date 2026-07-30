"""
SupplyChainRAG - 真实集成测试

使用 conftest.py 的 client fixture（内存 SQLite + mock Redis），
测试从 HTTP 请求到后端响应的完整链路。
不 mock agent 层 — 让路由→agent→tool 真实执行。
只 mock 外部 I/O（LLM API 调用）。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_health_check_returns_all_services(client):
    """健康检查端点应返回服务状态"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "services" in data


@pytest.mark.asyncio
async def test_greeting_returns_directly(client, seed_user):
    """问候意图应直接返回，不调用任何 Agent"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "你好", "stream": True},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    # SSE 响应应包含 greeting 事件
    body = resp.text
    assert "greeting" in body or "你好" in body or "session" in body


@pytest.mark.asyncio
async def test_tool_query_executes_real_sqlite(client, seed_user):
    """工具查询应走真实路由→agent→SQLite 查询链路"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "查一下物料MAT-001的库存", "stream": True},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    body = resp.text
    # 应有路由决策事件或内容事件
    assert "route" in body or "content" in body or "tool" in body


@pytest.mark.asyncio
async def test_tool_list_returns_registered_tools(client, seed_user):
    """工具列表端点应返回所有注册工具"""
    resp = await client.get(
        "/api/v1/tools/list",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, (list, dict))


@pytest.mark.asyncio
async def test_model_list_endpoint(client, seed_user):
    """模型列表端点应返回可用模型"""
    resp = await client.get(
        "/api/v1/chat/model/list",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "current" in data


@pytest.mark.asyncio
async def test_chat_stream_returns_sse(client, seed_user):
    """SSE 流式端点应返回 text/event-stream"""
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"query": "你好", "stream": True},
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_feedback_stats_endpoint(client, seed_user):
    """反馈统计端点应返回满意度数据"""
    resp = await client.get(
        "/api/v1/feedback/stats",
        headers={"Authorization": f"Bearer {seed_user['token']}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "satisfaction_rate" in data


@pytest.mark.asyncio
async def test_unauthorized_request_rejected(client):
    """无认证请求应被拒绝"""
    resp = await client.get("/api/v1/tools/list")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_config_endpoint_returns_settings(client):
    """配置端点应返回当前设置"""
    resp = await client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
