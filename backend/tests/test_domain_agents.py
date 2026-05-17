"""
SmartQA Domain Agent 单元测试

测试专域 Agent 的工具绑定、路由分发、基本结构。
不测试 LangGraph graph.stream()（MagicMock 不兼容，已知限制）。
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDomainAgents:
    """专域 Agent 工具绑定测试"""

    def test_purchase_agent_tools(self):
        from app.agents.purchase_agent import purchase_agent
        tool_names = [t.name for t in purchase_agent.tools]
        assert "query_order" in tool_names
        assert "query_supplier" in tool_names
        assert "query_inventory" not in tool_names
        assert len(tool_names) == 2

    def test_inventory_agent_tools(self):
        from app.agents.inventory_agent import inventory_agent
        tool_names = [t.name for t in inventory_agent.tools]
        assert "query_inventory" in tool_names
        assert len(tool_names) == 1

    def test_quality_agent_tools(self):
        from app.agents.quality_agent import quality_agent
        tool_names = [t.name for t in quality_agent.tools]
        assert "get_knowledge" in tool_names
        assert "create_ticket" in tool_names
        assert len(tool_names) == 2

    def test_production_agent_tools(self):
        from app.agents.production_agent import production_agent
        tool_names = [t.name for t in production_agent.tools]
        assert "create_ticket" in tool_names
        assert "query_inventory" in tool_names
        assert len(tool_names) == 2

    def test_all_agents_have_name(self):
        from app.agents.purchase_agent import purchase_agent
        from app.agents.inventory_agent import inventory_agent
        from app.agents.quality_agent import quality_agent
        from app.agents.production_agent import production_agent

        for agent in [purchase_agent, inventory_agent, quality_agent, production_agent]:
            assert agent.name, f"{agent.__class__.__name__} missing name"
            assert len(agent.tools) > 0, f"{agent.name} has no tools"

    def test_agent_graph_builds(self):
        """验证每个 Agent 可以构建 LangGraph 图（不运行）"""
        from app.agents.purchase_agent import purchase_agent
        graph = purchase_agent.graph
        assert graph is not None


class TestAgentRouter:
    """Agent 路由器测试"""

    def test_route_query_inventory(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("query_inventory")
        assert agent.name == "InventoryAgent"

    def test_route_query_order(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("query_order")
        assert agent.name == "PurchaseAgent"

    def test_route_query_supplier(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("query_supplier")
        assert agent.name == "PurchaseAgent"

    def test_route_get_knowledge(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("get_knowledge")
        assert agent.name == "QualityAgent"

    def test_route_create_ticket(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("create_ticket")
        assert agent.name == "ProductionAgent"

    def test_route_get_datetime_fallback(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("get_datetime")
        assert agent.name == "ToolAgent"

    def test_route_unknown_tool_fallback(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool("nonexistent_tool")
        assert agent.name == "ToolAgent"

    def test_route_no_tool_fallback(self):
        from app.agents.agent_router import get_agent_for_tool
        agent = get_agent_for_tool(None)
        assert agent.name == "ToolAgent"

    def test_all_domain_agents_registered(self):
        from app.agents.agent_router import ALL_DOMAIN_AGENTS
        names = [a.name for a in ALL_DOMAIN_AGENTS]
        assert "PurchaseAgent" in names
        assert "InventoryAgent" in names
        assert "QualityAgent" in names
        assert "ProductionAgent" in names
        assert len(ALL_DOMAIN_AGENTS) == 4


class TestChatRouting:
    """chat.py Agent 路由集成测试"""

    def test_get_tool_agent_imports(self):
        from app.api.chat import _get_tool_agent
        agent = _get_tool_agent(tool_name="query_inventory")
        assert agent.name == "InventoryAgent"

    def test_get_tool_agent_fallback(self):
        from app.api.chat import _get_tool_agent
        agent = _get_tool_agent(None, "get_datetime")
        assert agent.name == "ToolAgent"
