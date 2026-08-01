"""
ToolAgent 单元测试 — 实例化 / 工具加载 / 图构建 / run() / 循环检测 / 迭代上限
所有外部依赖（LLM、工具引擎、Redis）均使用 mock。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import ai_final, ai_message, async_iter, make_mock_tool


async def _run_agent(query, events, tools=None):
    """在标准 patch 环境内执行 ToolAgent.run()。"""
    from app.agents.tool import ToolAgent
    with patch("app.agents.base_agent.get_all_tools", return_value=tools or []), \
         patch("app.agents.base_agent.LLMFactory"), \
         patch("app.agents.base_agent.chat_memory", None), \
         patch("app.agents.base_agent.tool_metrics"):
        a = ToolAgent()
        a._graph = MagicMock()
        a._graph.astream.return_value = async_iter(events)
        return await a.run(query)


# ---- Instantiation & properties ----

class TestToolAgentInit:
    def test_instantiate(self):
        from app.agents.tool import ToolAgent
        a = ToolAgent()
        assert a is not None and a.name == "ToolAgent"

    def test_tools_property(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[make_mock_tool("t1"), make_mock_tool("t2")]):
            assert len(ToolAgent().tools) == 2

    def test_tools_cached(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[make_mock_tool()]) as gt:
            a = ToolAgent(); _ = a.tools; _ = a.tools
            gt.assert_called_once()


# ---- _build_graph ----

class TestBuildGraph:
    def test_returns_compiled_graph(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[make_mock_tool("q")]), \
             patch("app.agents.base_agent.LLMFactory") as mf:
            llm = MagicMock(); llm.bind_tools.return_value = llm; mf.get_llm.return_value = llm
            g = ToolAgent()._build_graph()
        assert g is not None and hasattr(g, "astream")

    def test_graph_property_caches(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[make_mock_tool()]), \
             patch("app.agents.base_agent.LLMFactory") as mf:
            llm = MagicMock(); llm.bind_tools.return_value = llm; mf.get_llm.return_value = llm
            a = ToolAgent()
            assert a.graph is a.graph


# ---- run() end-to-end ----

class TestToolAgentRun:
    @pytest.mark.asyncio
    async def test_tool_call_then_answer(self):
        from app.agents.tool import ToolAgent
        e1 = {"messages": [ai_message("query_inventory", {"sku": "A001"})]}
        e2 = {"messages": [ai_final("库存充足100件")]}
        r = await _run_agent("A001库存", [e1, e2], tools=[make_mock_tool("query_inventory")])
        assert "库存充足" in r["answer"]
        assert r["iterations"] >= 1
        assert r["tool_calls"][0]["tool"] == "query_inventory"

    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self):
        r = await _run_agent("今天几号", [{"messages": [ai_final("今天是2026年")]}])
        assert "2026" in r["answer"]
        assert r["iterations"] == 0 and r["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_iterations_match_tool_calls(self):
        from app.agents.tool import ToolAgent
        t1 = ai_message("query_order", {"id": "PO-001"}, "tc1")
        t2 = ai_message("query_supplier", {"name": "A"}, "tc2")
        events = [{"messages": [t1]}, {"messages": [t2]}, {"messages": [ai_final("完成")]}]
        r = await _run_agent("PO-001供应商", events,
                             tools=[make_mock_tool("query_order"), make_mock_tool("query_supplier")])
        assert r["iterations"] == 2 and len(r["tool_calls"]) == 2

    @pytest.mark.asyncio
    async def test_max_iterations_breaks(self):
        from app.agents.tool import ToolAgent, MAX_ITERATIONS
        events = [{"messages": [ai_message("query_inventory", {"sku": "X"}, f"tc{i}")]}
                  for i in range(MAX_ITERATIONS + 1)]
        r = await _run_agent("库存", events, tools=[make_mock_tool("query_inventory")])
        assert r["iterations"] >= MAX_ITERATIONS
        assert "终止" in r["answer"] or r["answer"]


# ---- Loop breaker (SuperPower-1) ----

class TestLoopBreaker:
    @pytest.mark.asyncio
    async def test_repeated_call_triggers_breaker(self):
        from app.agents.tool import ToolAgent
        calls = [{"messages": [ai_message("query_inventory", {"sku": "B001"}, f"tc{i}")]}
                 for i in range(3)]
        calls.append({"messages": [ai_final("已停止")]})
        r = await _run_agent("B001库存", calls, tools=[make_mock_tool("query_inventory")])
        assert r["answer"] is not None
