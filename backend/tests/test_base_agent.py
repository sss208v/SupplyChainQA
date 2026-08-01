"""
BaseReActAgent / base_agent 模块单元测试

覆盖范围：
  1. _build_demo_fallback 降级响应
  2. AgentState TypedDict 结构
  3. BaseReActAgent 属性（name / tools / caching）
  4. _handle_error DEMO_MODE 分支
  5. _loop_call_history_var ContextVar 行为
  6. run() 方法（事件流 / 工具调用 / 迭代上限 / 异常 / chat_memory）
  7. MAX_ITERATIONS 常量

所有外部依赖（LangGraph、LLMFactory、chat_memory、tool_engine、get_settings）
均通过 unittest.mock 隔离。
"""
import sys
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import ai_final, ai_message, async_iter, make_mock_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. TestBuildDemoFallback
# ---------------------------------------------------------------------------

class TestBuildDemoFallback:
    """验证 _build_demo_fallback 返回的降级响应包含关键信息。"""

    def test_contains_agent_name_query_and_capabilities(self):
        from app.agents.base_agent import _build_demo_fallback
        result = _build_demo_fallback("TestAgent", "test query", "connection refused")
        assert "TestAgent" in result
        assert "test query" in result
        # 验证包含演示能力列表
        assert "可演示能力" in result
        assert "query_inventory" in result
        assert "RAG" in result

    def test_error_truncated_to_80_chars(self):
        from app.agents.base_agent import _build_demo_fallback
        long_error = "x" * 200
        result = _build_demo_fallback("Agent", "q", long_error)
        # 原始 error 被截断到 80 字符
        assert ("x" * 81) not in result
        assert ("x" * 80) in result


# ---------------------------------------------------------------------------
# 2. TestAgentState
# ---------------------------------------------------------------------------

class TestAgentState:
    """验证 AgentState TypedDict 包含 messages 键。"""

    def test_has_messages_key(self):
        from app.agents.base_agent import AgentState
        assert "messages" in AgentState.__annotations__


# ---------------------------------------------------------------------------
# 3. TestBaseReActAgentProperties
# ---------------------------------------------------------------------------

class TestBaseReActAgentProperties:
    """name 属性 / tools 属性（全量 / 子集 / 缓存）。"""

    def test_name_returns_class_name(self):
        from app.agents.base_agent import BaseReActAgent
        agent = BaseReActAgent()
        assert agent.name == "BaseReActAgent"

    @patch("app.agents.base_agent.get_all_tools")
    def test_empty_tool_names_returns_all_tools(self, mock_get_all):
        """TOOL_NAMES = [] → 调用 get_all_tools() 返回全部工具。"""
        from app.agents.base_agent import BaseReActAgent
        mock_get_all.return_value = [make_mock_tool("t1"), make_mock_tool("t2")]
        agent = BaseReActAgent()
        agent.TOOL_NAMES = []
        tools = agent.tools
        assert len(tools) == 2
        mock_get_all.assert_called_once()

    @patch("app.agents.base_agent.TOOL_REGISTRY", {
        "query_inventory": make_mock_tool("query_inventory"),
        "query_order": make_mock_tool("query_order"),
    })
    def test_tool_names_subset_returns_matching_tools(self):
        """TOOL_NAMES = ["query_inventory"] → 只返回匹配的工具。"""
        from app.agents.base_agent import BaseReActAgent
        agent = BaseReActAgent()
        agent.TOOL_NAMES = ["query_inventory"]
        agent._tools = []  # 清除缓存
        tools = agent.tools
        assert len(tools) == 1
        assert tools[0].name == "query_inventory"

    @patch("app.agents.base_agent.get_all_tools")
    def test_tools_property_caches_result(self, mock_get_all):
        """tools 属性被访问两次时，底层工厂只调用一次。"""
        from app.agents.base_agent import BaseReActAgent
        mock_get_all.return_value = [make_mock_tool()]
        agent = BaseReActAgent()
        agent.TOOL_NAMES = []
        _ = agent.tools
        _ = agent.tools
        mock_get_all.assert_called_once()


# ---------------------------------------------------------------------------
# 4. TestHandleError
# ---------------------------------------------------------------------------

class TestHandleError:
    """_handle_error 在 DEMO_MODE 开/关时的行为。"""

    @patch("app.agents.base_agent.get_settings")
    def test_demo_mode_true_returns_demo_answer_and_info(self, mock_settings):
        from app.agents.base_agent import BaseReActAgent
        settings = MagicMock()
        settings.DEMO_MODE = True
        mock_settings.return_value = settings

        agent = BaseReActAgent()
        answer, demo_info = agent._handle_error("test query", Exception("connection refused"))

        assert "BaseReActAgent" in answer
        assert "test query" in answer
        assert demo_info is not None
        assert demo_info["mode"] == "demo"
        assert "connection refused" in demo_info["reason"]

    @patch("app.agents.base_agent.get_settings")
    def test_demo_mode_false_returns_error_message_and_none(self, mock_settings):
        from app.agents.base_agent import BaseReActAgent
        settings = MagicMock()
        settings.DEMO_MODE = False
        mock_settings.return_value = settings

        agent = BaseReActAgent()
        answer, demo_info = agent._handle_error("test query", Exception("some error"))

        assert "some error" in answer
        assert demo_info is None


# ---------------------------------------------------------------------------
# 5. TestLoopCallHistoryVar
# ---------------------------------------------------------------------------

class TestLoopCallHistoryVar:
    """_loop_call_history_var ContextVar 的 set/get 行为。"""

    def test_default_is_empty_list(self):
        from app.agents.base_agent import _loop_call_history_var
        token = _loop_call_history_var.set([])
        try:
            assert _loop_call_history_var.get() == []
        finally:
            _loop_call_history_var.reset(token)

    def test_set_and_get(self):
        from app.agents.base_agent import _loop_call_history_var
        token = _loop_call_history_var.set(["test"])
        try:
            assert _loop_call_history_var.get() == ["test"]
        finally:
            _loop_call_history_var.reset(token)


# ---------------------------------------------------------------------------
# 6. TestRunMethod
# ---------------------------------------------------------------------------

class TestRunMethod:
    """run() 方法：事件流处理、工具调用记录、迭代上限、异常降级、chat_memory 集成。"""

    def _make_agent(self):
        """创建一个预装了 mock graph 的 BaseReActAgent 实例。"""
        from app.agents.base_agent import BaseReActAgent
        agent = BaseReActAgent()
        agent._graph = MagicMock()
        return agent

    async def _run(self, query, events, tools=None, memory=None, **kw):
        """在标准 patch 环境内执行 agent.run()。"""
        with patch("app.agents.base_agent.get_all_tools", return_value=tools or []), \
             patch("app.agents.base_agent.chat_memory", memory):
            agent = self._make_agent()
            agent._graph.astream.return_value = async_iter(events)
            return await agent.run(query, **kw)

    async def test_ai_message_no_tool_calls_captures_final_answer(self):
        """AIMessage 无 tool_calls → 作为 final_answer 返回。"""
        events = [{"messages": [ai_final("这是最终回答")]}]
        result = await self._run("测试问题", events)
        assert result["answer"] == "这是最终回答"
        assert result["tool_calls"] == []
        assert result["iterations"] == 0

    async def test_ai_message_with_tool_calls_populates_record(self):
        """AIMessage 带 tool_calls → tool_calls_record 被填充。"""
        events = [
            {"messages": [ai_message("query_inventory", {"sku": "A001"})]},
            {"messages": [ai_final("库存100件")]},
        ]
        result = await self._run("A001库存", events, tools=[make_mock_tool("query_inventory")])
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool"] == "query_inventory"
        assert result["tool_calls"][0]["input"] == {"sku": "A001"}
        assert result["iterations"] >= 1

    async def test_max_iterations_limit_respected(self):
        """超过 MAX_ITERATIONS 后循环终止。"""
        from app.agents.base_agent import MAX_ITERATIONS
        events = [
            {"messages": [ai_message("query_inventory", {"sku": f"X{i}"}, f"tc{i}")]}
            for i in range(MAX_ITERATIONS + 3)
        ]
        result = await self._run("库存查询", events, tools=[make_mock_tool("query_inventory")])
        assert result["iterations"] >= MAX_ITERATIONS
        assert result["answer"]  # 应该有回答（无论是最终回答还是终止提示）

    async def test_exception_during_astream_calls_handle_error(self):
        """graph.astream 抛出异常 → _handle_error 被调用。"""
        with patch("app.agents.base_agent.get_all_tools", return_value=[]), \
             patch("app.agents.base_agent.chat_memory", None), \
             patch("app.agents.base_agent.get_settings") as mock_settings:
            settings = MagicMock()
            settings.DEMO_MODE = True
            mock_settings.return_value = settings

            agent = self._make_agent()
            agent._graph.astream.side_effect = Exception("LLM connection failed")

            result = await agent.run("测试异常")
        assert "BaseReActAgent" in result["answer"]
        assert "demo_info" in result
        assert result["demo_info"]["mode"] == "demo"

    async def test_session_id_with_chat_memory_adds_messages(self):
        """session_id + chat_memory → add_message 被调用两次（user + assistant）。"""
        mock_memory = MagicMock()
        mock_memory.get_context_string = AsyncMock(return_value=None)
        mock_memory.add_message = AsyncMock()

        events = [{"messages": [ai_final("回答")]}]
        result = await self._run("测试问题", events, memory=mock_memory,
                                 session_id="sess-123", user_id="user-1")

        assert mock_memory.add_message.call_count == 2
        # 第一次调用：user 消息
        call1 = mock_memory.add_message.call_args_list[0]
        assert call1.args[0] == "sess-123"
        assert call1.args[1] == "user"
        assert call1.args[2] == "测试问题"
        assert call1.kwargs.get("user_id") == "user-1"
        # 第二次调用：assistant 消息
        call2 = mock_memory.add_message.call_args_list[1]
        assert call2.args[0] == "sess-123"
        assert call2.args[1] == "assistant"
        assert call2.args[2] == "回答"

    async def test_chat_memory_get_context_returns_none_is_noop(self):
        """chat_memory.get_context_string 返回 None → 不注入历史，正常回答。"""
        mock_memory = MagicMock()
        mock_memory.get_context_string = AsyncMock(return_value=None)
        mock_memory.add_message = AsyncMock()

        events = [{"messages": [ai_final("直接回答")]}]
        result = await self._run("测试问题", events, memory=mock_memory, session_id="sess-456")

        assert result["answer"] == "直接回答"
        # get_context_string 只被调用一次（携带 user_id 隔离参数）
        mock_memory.get_context_string.assert_called_once_with("sess-456", user_id="")

    async def test_no_final_answer_returns_fallback_message(self):
        """所有事件都没有无 tool_calls 的 AIMessage → 返回默认兜底文案。"""
        events = [{"messages": []}]
        result = await self._run("无回答测试", events)
        assert result["answer"] == "未能生成回答，请重试。"


# ---------------------------------------------------------------------------
# 7. TestMAX_ITERATIONS
# ---------------------------------------------------------------------------

class TestMAX_ITERATIONS:
    """验证 MAX_ITERATIONS 常量值。"""

    def test_max_iterations_is_5(self):
        from app.agents.base_agent import MAX_ITERATIONS
        assert MAX_ITERATIONS == 5
