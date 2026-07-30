"""路由模块单元测试 — 使用真实 router_agent"""
import pytest
from unittest.mock import patch, AsyncMock


class TestRuleMatching:
    """测试真实 router_agent 的规则匹配"""

    @pytest.mark.asyncio
    async def test_greeting_routed_correctly(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("你好")
        assert result["intent"] == IntentType.GREETING

    @pytest.mark.asyncio
    async def test_tool_query_routed_correctly(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("帮我查一下物料MAT-001的库存")
        assert result["intent"] in (IntentType.TOOL_CALL, IntentType.GRAPH_QUERY)

    @pytest.mark.asyncio
    async def test_rag_query_routed_correctly(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("供应商准入需要什么资质")
        assert result["intent"] in (IntentType.RAG_ANSWER, IntentType.GRAPH_QUERY)

    @pytest.mark.asyncio
    async def test_graph_query_routed_correctly(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("MAT-001 缺货会影响哪些物料")
        assert result["intent"] == IntentType.GRAPH_QUERY

    @pytest.mark.asyncio
    async def test_intent_type_enum_values(self):
        from app.agents.router import IntentType
        assert IntentType.GREETING.value == "greeting"
        assert IntentType.RAG_ANSWER.value == "rag_answer"
        assert IntentType.TOOL_CALL.value == "tool_call"
        assert IntentType.GRAPH_QUERY.value == "graph_query"
        assert IntentType.GOAL.value == "goal"
        assert IntentType.UNCLEAR.value == "unclear"

    @pytest.mark.asyncio
    async def test_route_returns_required_keys(self):
        from app.agents.router import router_agent
        result = await router_agent.route("你好")
        assert "intent" in result
        assert "method" in result

    @pytest.mark.asyncio
    async def test_method_field_valid(self):
        from app.agents.router import router_agent
        result = await router_agent.route("你好")
        assert result["method"] in ("rule", "semantic", "llm")


class TestGraphQueryRouting:
    """测试 GRAPH_QUERY 路由的边界情况"""

    @pytest.mark.asyncio
    async def test_pure_concept_not_graph(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("什么是安全库存")
        assert result["intent"] != IntentType.GRAPH_QUERY

    @pytest.mark.asyncio
    async def test_entity_no_relation_not_graph(self):
        from app.agents.router import router_agent, IntentType
        result = await router_agent.route("查 MAT-001 库存")
        # "查库存" 没有关系词，应走 TOOL_CALL 而非 GRAPH_QUERY
        assert result["intent"] != IntentType.GRAPH_QUERY


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
