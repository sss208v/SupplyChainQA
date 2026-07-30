"""
tests/test_l3_tool_cache.py — 工具层 L3 缓存与失效验证（Spec 阶段四.4 / 2.5）

- 只读工具查询结果 read-through 命中/回源
- create_ticket 写成功后失效 tool 命名空间（防脏读）
- /tools/schema 端点：后端 TOOL_REGISTRY 单一事实来源（Spec 3.4）
- 恶意 doc_ids 在请求模型层被拒绝（Spec 阶段四.5，Pydantic 校验 → 422）
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestToolL3Cache:
    @pytest.mark.asyncio
    async def test_query_inventory_cache_hit_skips_db(self):
        """L3 命中 → 直接返回缓存 JSON，不触发 SQLite 查询"""
        from app.core import tool_engine
        cached = json.dumps({"material_code": "MAT-001", "quantity": 99}, ensure_ascii=False)

        mock_cm = MagicMock()
        mock_cm.l3_get_or_set = AsyncMock(return_value=cached)

        with patch("app.core.cache_manager.cache_manager", mock_cm):
            result = await tool_engine.query_inventory.ainvoke({"material_code": "MAT-001"})

        assert json.loads(result)["quantity"] == 99
        mock_cm.l3_get_or_set.assert_awaited_once()
        # 命名空间与 TTL 配置正确
        args = mock_cm.l3_get_or_set.await_args
        assert args.args[0] == "tool"

    @pytest.mark.asyncio
    async def test_error_result_not_cached_by_predicate(self):
        """cache_if 谓词：含 error 的工具结果不允许缓存"""
        from app.core.tool_engine import _l3_tool_cache

        captured = {}

        async def fake_l3(namespace, key, ttl, loader, cache_if=None):
            value = await loader()
            captured["cacheable"] = cache_if(value) if cache_if else True
            return value

        mock_cm = MagicMock()
        mock_cm.l3_get_or_set = AsyncMock(side_effect=fake_l3)

        async def error_loader():
            return json.dumps({"error": "未找到物料"}, ensure_ascii=False)

        with patch("app.core.cache_manager.cache_manager", mock_cm):
            await _l3_tool_cache("query_inventory:BAD", error_loader)

        assert captured["cacheable"] is False

    @pytest.mark.asyncio
    async def test_create_ticket_invalidates_tool_namespace(self):
        """create_ticket 写成功 → l3_invalidate('tool') 被调用（防脏读）"""
        from app.core import tool_engine

        mock_cm = MagicMock()
        mock_cm.l3_invalidate = AsyncMock(return_value=3)

        with patch("app.core.cache_manager.cache_manager", mock_cm):
            result = await tool_engine.create_ticket.ainvoke({
                "title": "缓存失效测试",
                "description": "验证写后失效",
                "priority": "低",
            })

        data = json.loads(result)
        assert "ticket_id" in data
        mock_cm.l3_invalidate.assert_awaited_once_with("tool")


class TestToolSchemaEndpoint:
    """GET /tools/schema — 后端 TOOL_REGISTRY 为单一事实来源（Spec 3.4）"""

    @pytest.mark.asyncio
    async def test_schema_returns_all_admin_tools(self, client, admin_user):
        """admin 可见全部 11 个注册工具的输入 Schema"""
        resp = await client.get(
            "/api/v1/tools/schema",
            headers={"Authorization": f"Bearer {admin_user['token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        from app.core.tool_engine import TOOL_REGISTRY
        from app.api.tool import ROLE_TOOLS
        expected = set(TOOL_REGISTRY) & ROLE_TOOLS["admin"]
        assert set(data["schemas"].keys()) == expected

        inv = data["schemas"]["query_inventory"]
        assert inv["inputs"][0]["name"] == "material_code"
        assert inv["description"]
        assert "admin" in inv["allowed_roles"]

    @pytest.mark.asyncio
    async def test_schema_filtered_by_role(self, client, seed_user):
        """非 admin 只能看到自身角色允许的工具"""
        resp = await client.get(
            "/api/v1/tools/schema",
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 200
        from app.api.tool import ROLE_TOOLS
        assert set(resp.json()["schemas"].keys()) <= ROLE_TOOLS["purchase"]

    @pytest.mark.asyncio
    async def test_schema_requires_auth(self, client):
        resp = await client.get("/api/v1/tools/schema")
        assert resp.status_code in (401, 403)


class TestMaliciousDocIdsRejectedAtRequestLayer:
    """恶意 doc_ids 在 Pydantic 请求模型层拦截（SSE 开始后无法返回错误状态码）"""

    @pytest.mark.asyncio
    async def test_chat_stream_rejects_injection_doc_id(self, client, seed_user):
        resp = await client.post(
            "/api/v1/chat/stream",
            json={"query": "测试", "doc_ids": ['x"] or id >= 0 or doc_id in ["x']},
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 422  # Pydantic 校验失败

    @pytest.mark.asyncio
    async def test_chat_ask_rejects_injection_doc_id(self, client, seed_user):
        resp = await client.post(
            "/api/v1/chat/ask",
            json={"question": "测试", "doc_ids": ["d1] or [d2"]},
            headers={"Authorization": f"Bearer {seed_user['token']}"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_doc_ids_pass_validation(self):
        """合法 UUID 前缀 doc_ids 通过请求模型校验"""
        from app.api.chat_helpers import ChatRequest
        req = ChatRequest(query="测试", doc_ids=["a1b2c3d4-e5f6"])
        assert req.doc_ids == ["a1b2c3d4-e5f6"]
