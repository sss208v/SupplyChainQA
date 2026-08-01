"""
API Handlers 单元测试 — 纯 mock，无外部依赖

覆盖范围：
  1. handle_greeting: SSE 事件流、content 事件、DONE 事件
  2. handle_unclear: 有结果（LLM 回答）、无结果（引导建议）、异常降级
  3. handle_tool_call: 澄清检查、权限拒绝、审批拦截、幂等拦截、锁竞争失败、正常执行
  4. handle_graph_query: 正常（有结果）、无结果、查询异常、LLM 生成异常
"""
import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from helpers import collect_sse
from app.api.handlers.tool_call import handle_tool_call
from app.api.handlers.graph_query import handle_graph_query

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. TestHandleGreeting
# ---------------------------------------------------------------------------

class TestHandleGreeting:
    """handle_greeting: 问候意图直接返回预设回复。"""

    async def test_hello_returns_content_and_done(self):
        from app.api.handlers.greeting import handle_greeting
        events = await collect_sse(handle_greeting("你好"))
        assert len(events) == 2
        assert events[0]["type"] == "content"
        assert "供应链智能助手" in events[0]["content"]
        assert events[1]["type"] == "done"

    async def test_thanks_returns_appropriate_response(self):
        from app.api.handlers.greeting import handle_greeting
        events = await collect_sse(handle_greeting("谢谢"))
        assert events[0]["type"] == "content"
        assert "不客气" in events[0]["content"]

    async def test_unknown_greeting_returns_empty_content(self):
        from app.api.handlers.greeting import handle_greeting
        events = await collect_sse(handle_greeting("xyz_random"))
        # 未匹配的问候返回空 content
        assert events[0]["type"] == "content"
        assert events[0]["content"] == ""


# ---------------------------------------------------------------------------
# 2. TestHandleUnclear
# ---------------------------------------------------------------------------

class TestHandleUnclear:
    """handle_unclear: 意图不明 → 轻量检索 → 有结果/无结果/异常。"""

    @patch("app.api.handlers.unclear.LLMFactory")
    @patch("app.api.handlers.unclear.rag_agent")
    @patch("app.api.handlers.unclear.milvus_manager")
    @patch("app.api.handlers.unclear.get_settings")
    async def test_found_chunks_triggers_llm_stream(
        self, mock_settings, mock_milvus, mock_rag_agent, mock_llm_factory
    ):
        """搜到结果 → LLM 流式回答。"""
        mock_settings.return_value = MagicMock()
        mock_milvus.build_visibility_expr.return_value = ""
        mock_rag_agent.rag.search.return_value = {
            "results": [{"chunk_id": "c1", "content": "安全库存", "rerank_score": 0.8, "source": "doc1"}],
            "confidence": 0.8,
        }
        mock_rag_agent._format_context.return_value = ("安全库存内容", [{"source": "doc1"}])

        # Mock LLM stream
        async def _fake_stream(messages, callbacks=None):
            chunk = MagicMock()
            chunk.content = "这是回答"
            yield chunk

        mock_llm_factory.get_llm.return_value = MagicMock()
        mock_llm_factory.astream = _fake_stream

        from app.api.handlers.unclear import handle_unclear
        events = await collect_sse(handle_unclear("模糊问题", "admin"))

        # 第一个事件是 route_fallback
        assert events[0]["type"] == "route_fallback"
        # 有 sources 事件
        sources_events = [e for e in events if e.get("type") == "sources"]
        assert len(sources_events) == 1
        # 有 content 事件
        content_events = [e for e in events if e.get("type") == "content"]
        assert len(content_events) >= 1
        # 最后是 done
        assert events[-1]["type"] == "done"

    @patch("app.api.handlers.unclear.rag_agent")
    @patch("app.api.handlers.unclear.milvus_manager")
    @patch("app.api.handlers.unclear.get_settings")
    async def test_no_chunks_returns_guide_suggestions(
        self, mock_settings, mock_milvus, mock_rag_agent
    ):
        """没搜到结果 → 返回引导建议。"""
        mock_settings.return_value = MagicMock()
        mock_milvus.build_visibility_expr.return_value = ""
        mock_rag_agent.rag.search.return_value = {"results": [], "confidence": 0.0}

        from app.api.handlers.unclear import handle_unclear
        events = await collect_sse(handle_unclear("???", "purchase"))

        content_events = [e for e in events if e.get("type") == "content"]
        assert len(content_events) == 1
        assert "MAT-001" in content_events[0]["content"]
        assert "PO-20250601" in content_events[0]["content"]

    @patch("app.api.handlers.unclear.rag_agent")
    @patch("app.api.handlers.unclear.milvus_manager")
    @patch("app.api.handlers.unclear.get_settings")
    async def test_exception_returns_error_message(
        self, mock_settings, mock_milvus, mock_rag_agent
    ):
        """检索异常 → 返回错误提示。"""
        mock_settings.return_value = MagicMock()
        mock_milvus.build_visibility_expr.side_effect = RuntimeError("milvus down")

        from app.api.handlers.unclear import handle_unclear
        events = await collect_sse(handle_unclear("crash query", "admin"))

        content_events = [e for e in events if e.get("type") == "content"]
        assert any("暂时无法处理" in e["content"] for e in content_events)


# ---------------------------------------------------------------------------
# 3. TestHandleToolCall
# ---------------------------------------------------------------------------

class TestHandleToolCall:
    """handle_tool_call: 澄清/权限/审批/幂等/锁/正常执行。"""

    def _make_body(self, approved=False, approved_tool=None):
        from app.api.chat_helpers import ChatRequest
        return ChatRequest(
            query="查库存",
            session_id="sess-1",
            approved=approved,
            approved_tool=approved_tool,
        )

    async def _invoke(self, query, tool_name, body=None, redis=None,
                      user_role="finance", **kw):
        """以统一参数调用 handle_tool_call 并收集 SSE 事件。"""
        return await collect_sse(handle_tool_call(
            safe_query=query,
            tool_name=tool_name,
            session_id="sess-1",
            user_id="user-1",
            agent_type=None,
            body=body if body is not None else self._make_body(),
            langfuse_callbacks=None,
            redis=redis if redis is not None else MagicMock(),
            user_role=user_role,
            needs_clarify=False,
            **kw,
        ))

    @patch("app.api.handlers.tool_call.check_needs_clarification")
    async def test_clarification_needed_yields_clarify_event(self, mock_clarify):
        """参数不足 → 发送 clarify 事件。"""
        clarify_result = MagicMock()
        clarify_result.needs_clarification = True
        clarify_result.question = "请提供物料编码"
        clarify_result.missing_params = ["material_code"]
        mock_clarify.return_value = clarify_result

        mock_redis = MagicMock()
        body = self._make_body()

        events = await self._invoke("查库存", "query_inventory", body=body, redis=mock_redis)

        clarify_events = [e for e in events if e.get("type") == "clarify"]
        assert len(clarify_events) == 1
        assert "物料编码" in clarify_events[0]["question"]

    @patch("app.api.handlers.tool_call.check_needs_clarification")
    @patch("app.api.handlers.tool_call._is_tool_allowed")
    async def test_permission_denied_yields_tool_blocked(self, mock_allowed, mock_clarify):
        """权限不足 → 发送 tool_blocked 事件。"""
        mock_clarify.return_value = None
        mock_allowed.return_value = False

        mock_redis = MagicMock()
        body = self._make_body()

        events = await self._invoke("创建工单", "create_ticket", body=body, redis=mock_redis)

        blocked_events = [e for e in events if e.get("type") == "tool_blocked"]
        assert len(blocked_events) == 1
        assert "无权" in blocked_events[0]["reason"]

    @patch("app.api.handlers.tool_call.check_needs_clarification")
    @patch("app.api.handlers.tool_call._is_tool_allowed")
    async def test_write_tool_without_approval_yields_approval_request(
        self, mock_allowed, mock_clarify
    ):
        """写操作未审批 → 发送 approval_request 事件。"""
        mock_clarify.return_value = None
        mock_allowed.return_value = True

        mock_redis = MagicMock()
        mock_redis.is_connected = False  # 跳过 Redis 部分
        body = self._make_body(approved=False)

        events = await self._invoke("创建紧急工单", "create_ticket",
                                    body=body, redis=mock_redis, user_role="admin")

        approval_events = [e for e in events if e.get("type") == "approval_request"]
        assert len(approval_events) == 1
        assert approval_events[0]["tool"] == "create_ticket"

    @patch("app.api.handlers.tool_call.check_needs_clarification")
    @patch("app.api.handlers.tool_call._is_tool_allowed")
    async def test_idempotent_duplicate_yields_error(self, mock_allowed, mock_clarify):
        """幂等拦截 → 发送 error 事件。"""
        mock_clarify.return_value = None
        mock_allowed.return_value = True

        mock_redis = MagicMock()
        mock_redis.is_connected = True
        mock_redis.try_begin_idempotent = AsyncMock(return_value="completed")

        body = self._make_body(approved=True, approved_tool="query_inventory")

        events = await self._invoke("查MAT-001库存", "query_inventory",
                                    body=body, redis=mock_redis, user_role="admin")

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "已成功执行" in error_events[0]["message"]

    @patch("app.api.handlers.tool_call.check_needs_clarification")
    @patch("app.api.handlers.tool_call._is_tool_allowed")
    async def test_lock_not_acquired_yields_error(self, mock_allowed, mock_clarify):
        """锁竞争失败 → 发送 error 事件。"""
        mock_clarify.return_value = None
        mock_allowed.return_value = True

        mock_redis = MagicMock()
        mock_redis.is_connected = True
        mock_redis.try_begin_idempotent = AsyncMock(return_value="acquired")
        mock_redis.acquire_lock = AsyncMock(return_value=None)
        mock_redis.cancel_idempotent = AsyncMock()

        body = self._make_body(approved=True, approved_tool="query_inventory")

        events = await self._invoke("查MAT-001库存", "query_inventory",
                                    body=body, redis=mock_redis, user_role="admin")

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "正在处理中" in error_events[0]["message"]


# ---------------------------------------------------------------------------
# 4. TestHandleGraphQuery
# ---------------------------------------------------------------------------

class TestHandleGraphQuery:
    """handle_graph_query: 图谱查询的正常/无结果/异常场景。"""

    async def _invoke(self, query, engine, memory=None):
        """以统一参数调用 handle_graph_query 并收集 SSE 事件。"""
        return await collect_sse(handle_graph_query(
            safe_query=query,
            session_id="sess-1",
            user_id="user-1",
            graph_engine=engine,
            memory=memory,
        ))

    async def test_query_exception_yields_error_event(self):
        """graph_engine.query 抛异常 → error 事件。"""
        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(side_effect=RuntimeError("neo4j down"))

        events = await self._invoke("MAT-001 的供应商", mock_engine)

        assert events[0]["type"] == "graph_query_start"
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "neo4j down" in error_events[0]["message"]

    async def test_error_in_result_yields_content_with_error_message(self):
        """graph_result 含 error 字段 → content 包含错误消息。"""
        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(return_value={"error": "模板不匹配", "rows": []})

        events = await self._invoke("MAT-001 相关", mock_engine)

        content_events = [e for e in events if e.get("type") == "content"]
        assert any("模板不匹配" in e["content"] for e in content_events)

    async def test_no_rows_yields_not_found_message(self):
        """无结果 → 提示未找到相关实体关系。"""
        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(return_value={"rows": [], "pattern": None, "entities": []})

        events = await self._invoke("MAT-999 的供应商", mock_engine)

        content_events = [e for e in events if e.get("type") == "content"]
        assert any("未在供应链图谱中找到" in e["content"] for e in content_events)

    @patch("app.api.handlers.graph_query.LLMFactory")
    async def test_with_rows_yields_graph_result_and_llm_answer(self, mock_llm_factory):
        """有结果 → 发 graph_result 事件 + LLM 生成回答。"""

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "MAT-001 由供应商 SUP-001 供货"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.get_llm.return_value = mock_llm

        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(return_value={
            "rows": [{"entity": "MAT-001", "supplier": "SUP-001"}],
            "pattern": "material_supplier",
            "entities": ["MAT-001"],
        })
        mock_engine.format_results.return_value = "MAT-001 -> SUP-001"

        events = await self._invoke("MAT-001 的供应商", mock_engine)

        graph_events = [e for e in events if e.get("type") == "graph_result"]
        assert len(graph_events) == 1
        assert graph_events[0]["pattern"] == "material_supplier"
        assert graph_events[0]["row_count"] == 1

        content_events = [e for e in events if e.get("type") == "content"]
        assert any("SUP-001" in e["content"] for e in content_events)

    @patch("app.api.handlers.graph_query.LLMFactory")
    async def test_llm_failure_falls_back_to_raw_context(self, mock_llm_factory):
        """LLM 生成失败 → 回退输出原始图谱上下文。"""

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        mock_llm_factory.get_llm.return_value = mock_llm

        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(return_value={
            "rows": [{"entity": "MAT-001"}],
            "pattern": "test",
            "entities": ["MAT-001"],
        })
        mock_engine.format_results.return_value = "raw graph data"

        events = await self._invoke("MAT-001", mock_engine)

        content_events = [e for e in events if e.get("type") == "content"]
        assert any("raw graph data" in e["content"] for e in content_events)

    @patch("app.api.handlers.graph_query.LLMFactory")
    async def test_memory_save_called_on_success(self, mock_llm_factory):
        """成功时保存对话记忆。"""

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "回答内容"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.get_llm.return_value = mock_llm

        mock_engine = MagicMock()
        mock_engine.query = AsyncMock(return_value={
            "rows": [{"x": 1}], "pattern": "p", "entities": ["e"],
        })
        mock_engine.format_results.return_value = "ctx"

        mock_memory = MagicMock()
        mock_memory.add_message = AsyncMock()

        events = await self._invoke("test query", mock_engine, memory=mock_memory)

        assert mock_memory.add_message.call_count == 2
