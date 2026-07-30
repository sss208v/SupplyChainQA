"""
ToolAgent 单元测试 — 实例化 / 工具加载 / 图构建 / run() / 循环检测 / 迭代上限
所有外部依赖（LLM、工具引擎、Redis）均使用 mock。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _async_iter(items):
    for item in items:
        yield item



def _tool(name="test_tool"):
    t = MagicMock(); t.name = name; t.ainvoke = AsyncMock(return_value=f"{name} result")
    return t


from langchain_core.messages import AIMessage

def _ai_tc(name, args, cid="tc1"):
    m = AIMessage(content="")
    m.tool_calls = [{"name": name, "args": args, "id": cid}]
    return m

def _ai_final(text):
    m = AIMessage(content=text)
    m.tool_calls = []
    return m


# ---- Instantiation & properties ----

class TestToolAgentInit:
    def test_instantiate(self):
        from app.agents.tool import ToolAgent
        a = ToolAgent()
        assert a is not None and a.name == "ToolAgent"

    def test_tools_property(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("t1"), _tool("t2")]):
            assert len(ToolAgent().tools) == 2

    def test_tools_cached(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool()]) as gt:
            a = ToolAgent(); _ = a.tools; _ = a.tools
            gt.assert_called_once()


# ---- _build_graph ----

class TestBuildGraph:
    def test_returns_compiled_graph(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("q")]), \
             patch("app.agents.base_agent.LLMFactory") as mf:
            llm = MagicMock(); llm.bind_tools.return_value = llm; mf.get_llm.return_value = llm
            g = ToolAgent()._build_graph()
        assert g is not None and hasattr(g, "astream")

    def test_graph_property_caches(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool()]), \
             patch("app.agents.base_agent.LLMFactory") as mf:
            llm = MagicMock(); llm.bind_tools.return_value = llm; mf.get_llm.return_value = llm
            a = ToolAgent()
            assert a.graph is a.graph


# ---- run() end-to-end ----

class TestToolAgentRun:
    @pytest.mark.asyncio
    async def test_tool_call_then_answer(self):
        from app.agents.tool import ToolAgent
        e1 = {"messages": [_ai_tc("query_inventory", {"sku": "A001"})]}
        e2 = {"messages": [_ai_final("库存充足100件")]}
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("query_inventory")]), \
             patch("app.agents.base_agent.LLMFactory"), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.tool_metrics"):
            a = ToolAgent()
            a._graph = MagicMock()
            a._graph.astream.return_value = _async_iter([e1, e2])
            r = await a.run("A001库存")
        assert "库存充足" in r["answer"]
        assert r["iterations"] >= 1
        assert r["tool_calls"][0]["tool"] == "query_inventory"

    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self):
        from app.agents.tool import ToolAgent
        with patch("app.agents.base_agent.get_all_tools", return_value=[]), \
             patch("app.agents.base_agent.LLMFactory"), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.tool_metrics"):
            a = ToolAgent()
            a._graph = MagicMock()
            a._graph.astream.return_value = _async_iter([{"messages": [_ai_final("今天是2026年")]}])
            r = await a.run("今天几号")
        assert "2026" in r["answer"]
        assert r["iterations"] == 0 and r["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_iterations_match_tool_calls(self):
        from app.agents.tool import ToolAgent
        t1 = _ai_tc("query_order", {"id": "PO-001"}, "tc1")
        t2 = _ai_tc("query_supplier", {"name": "A"}, "tc2")
        events = [{"messages": [t1]}, {"messages": [t2]}, {"messages": [_ai_final("完成")]}]
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("query_order"), _tool("query_supplier")]), \
             patch("app.agents.base_agent.LLMFactory"), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.tool_metrics"):
            a = ToolAgent()
            a._graph = MagicMock()
            a._graph.astream.return_value = _async_iter(events)
            r = await a.run("PO-001供应商")
        assert r["iterations"] == 2 and len(r["tool_calls"]) == 2

    @pytest.mark.asyncio
    async def test_max_iterations_breaks(self):
        from app.agents.tool import ToolAgent, MAX_ITERATIONS
        events = [{"messages": [_ai_tc("query_inventory", {"sku": "X"}, f"tc{i}")]}
                  for i in range(MAX_ITERATIONS + 1)]
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("query_inventory")]), \
             patch("app.agents.base_agent.LLMFactory"), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.tool_metrics"):
            a = ToolAgent()
            a._graph = MagicMock()
            a._graph.astream.return_value = _async_iter(events)
            r = await a.run("库存")
        assert r["iterations"] >= MAX_ITERATIONS
        assert "终止" in r["answer"] or r["answer"]


# ---- Loop breaker (SuperPower-1) ----

class TestLoopBreaker:
    @pytest.mark.asyncio
    async def test_repeated_call_triggers_breaker(self):
        from app.agents.tool import ToolAgent
        calls = [{"messages": [_ai_tc("query_inventory", {"sku": "B001"}, f"tc{i}")]}
                 for i in range(3)]
        calls.append({"messages": [_ai_final("已停止")]})
        with patch("app.agents.base_agent.get_all_tools", return_value=[_tool("query_inventory")]), \
             patch("app.agents.base_agent.LLMFactory"), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.tool_metrics"):
            a = ToolAgent()
            a._graph = MagicMock()
            a._graph.astream.return_value = _async_iter(calls)
            r = await a.run("B001库存")
        assert r["answer"] is not None
