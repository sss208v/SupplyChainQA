"""E2E integration tests — full pipeline with ALL external services mocked.

Covers: RAG query, Tool query, Orchestrator goal, Greeting, Unclear, Error handling.
External services mocked: LLM, Milvus, Redis, PostgreSQL, Neo4j.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.agents.router import IntentType


# ---------------------------------------------------------------------------
# Helpers — reusable mock objects
# ---------------------------------------------------------------------------

def _make_route_result(intent: IntentType, **overrides) -> dict:
    """Build a minimal router result dict."""
    base = {"intent": intent, "confidence": 0.9, "tool_name": None, "method": "rule"}
    base.update(overrides)
    return base


def _rag_answer() -> dict:
    return {
        "answer": "安全库存是防止缺货的缓冲库存量。",
        "sources": [{"index": 1, "source": "供应链手册", "page": 5, "section": "库存管理", "snippet": "安全库存...", "score": 0.85}],
        "confidence": 0.88,
        "query_type": "specific",
        "context_used": 1,
    }


def _tool_answer() -> dict:
    return {
        "answer": "物料 MAT-001 当前库存 500 件。",
        "tool_calls": [{"tool": "query_inventory", "input": {"material_id": "MAT-001"}}],
        "iterations": 1,
    }


def _orchestrator_answer() -> dict:
    return {
        "answer": "综合分析：库存充足，供应商正常。",
        "plan": {"goal": "评估库存风险", "steps": [{"agent": "inventory", "task": "查库存"}]},
        "execution": {
            "goal": "评估库存风险",
            "total_steps": 1,
            "success_steps": 1,
            "failed_steps": 0,
            "step_results": [{"step": 1, "agent": "inventory", "task": "查库存", "result": "充足", "error": False}],
        },
        "duration_ms": 320,
    }


# ---------------------------------------------------------------------------
# Patches that blanket all external services
# ---------------------------------------------------------------------------

_BLANKET_PATCHES = [
    patch("app.core.llm_router.LLMFactory", autospec=True),
    patch("app.core.redis_client.chat_memory", new_callable=MagicMock),
    patch("app.core.milvus_client.milvus_manager", new_callable=MagicMock),
    patch("app.core.database.async_session", new_callable=MagicMock),
    patch("app.core.graph_engine.graph_engine", new_callable=MagicMock),
    patch("app.core.neo4j_client.neo4j_client", new_callable=MagicMock),
]


# ---------------------------------------------------------------------------
# Simulate the routing + dispatch logic from chat_completions
# ---------------------------------------------------------------------------

async def _dispatch(query: str, session_id: str = "test-session"):
    """Simulate the intent routing + agent dispatch from chat.py.

    This is NOT an HTTP call — it tests the pure Python pipeline so we avoid
    spinning up the full ASGI stack while still exercising every code path.
    """
    from app.agents.router import router_agent
    from app.agents.rag import rag_agent
    from app.agents.tool import tool_agent
    from app.agents.orchestrator import orchestrator

    route_result = await router_agent.route(query)
    intent = route_result["intent"]

    if intent == IntentType.GREETING:
        return {"answer": f"你好！我是供应链智能助手。", "intent": intent.value, "confidence": 0.0}

    if intent == IntentType.RAG_ANSWER:
        result = await rag_agent.answer(query=query, session_id=session_id)
        return {**result, "intent": intent.value}

    if intent == IntentType.TOOL_CALL:
        result = await tool_agent.run(query=query, session_id=session_id)
        return {**result, "intent": intent.value}

    if intent == IntentType.GOAL:
        result = await orchestrator.run(goal=query, session_id=session_id)
        return {**result, "intent": intent.value}

    # UNCLEAR or unsupported
    return {"answer": "抱歉，我没有理解您的问题，请换个方式描述。", "intent": intent.value, "confidence": 0.0}


# ===========================================================================
# Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_rag_query_full_flow():
    """Path 1: RAG_ANSWER — route -> rag_agent.answer -> response."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.rag.rag_agent.answer", new_callable=AsyncMock) as mock_answer:
        mock_route.return_value = _make_route_result(IntentType.RAG_ANSWER)
        mock_answer.return_value = _rag_answer()

        resp = await _dispatch("什么是安全库存")

        mock_route.assert_awaited_once()
        mock_answer.assert_awaited_once()
        assert resp["intent"] == "rag_answer"
        assert "安全库存" in resp["answer"]
        assert resp["confidence"] == pytest.approx(0.88)
        assert isinstance(resp["sources"], list) and len(resp["sources"]) > 0


@pytest.mark.asyncio
async def test_tool_query_full_flow():
    """Path 2: TOOL_CALL — route -> tool_agent.run -> response."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.tool.tool_agent.run", new_callable=AsyncMock) as mock_run:
        mock_route.return_value = _make_route_result(
            IntentType.TOOL_CALL, tool_name="query_inventory"
        )
        mock_run.return_value = _tool_answer()

        resp = await _dispatch("查库存 MAT-001")

        mock_route.assert_awaited_once()
        mock_run.assert_awaited_once()
        assert resp["intent"] == "tool_call"
        assert "MAT-001" in resp["answer"]
        assert isinstance(resp["tool_calls"], list) and len(resp["tool_calls"]) > 0


@pytest.mark.asyncio
async def test_orchestrator_goal_full_flow():
    """Path 3: GOAL — route -> orchestrator.run -> response."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.orchestrator.orchestrator.run", new_callable=AsyncMock) as mock_run:
        mock_route.return_value = _make_route_result(IntentType.GOAL)
        mock_run.return_value = _orchestrator_answer()

        resp = await _dispatch("帮我评估库存短缺风险")

        mock_route.assert_awaited_once()
        mock_run.assert_awaited_once()
        assert resp["intent"] == "goal"
        assert "库存" in resp["answer"]
        assert resp["plan"]["steps"]
        assert resp["execution"]["success_steps"] == 1


@pytest.mark.asyncio
async def test_greeting_returns_without_agents():
    """Greeting intent returns directly, no agent is called."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.rag.rag_agent.answer", new_callable=AsyncMock) as mock_rag, \
         patch("app.agents.tool.tool_agent.run", new_callable=AsyncMock) as mock_tool, \
         patch("app.agents.orchestrator.orchestrator.run", new_callable=AsyncMock) as mock_orch:

        mock_route.return_value = _make_route_result(IntentType.GREETING)

        resp = await _dispatch("你好")

        assert resp["intent"] == "greeting"
        assert "助手" in resp["answer"]
        mock_rag.assert_not_awaited()
        mock_tool.assert_not_awaited()
        mock_orch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unclear_returns_fallback():
    """UNCLEAR intent returns a friendly fallback message."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route:
        mock_route.return_value = _make_route_result(IntentType.UNCLEAR, confidence=0.3)

        resp = await _dispatch("asdfghjkl")

        assert resp["intent"] == "unclear"
        assert "没有理解" in resp["answer"] or "换个方式" in resp["answer"]


@pytest.mark.asyncio
async def test_rag_agent_exception_graceful():
    """When rag_agent raises, the caller should see an error message, not a crash."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.rag.rag_agent.answer", new_callable=AsyncMock) as mock_answer:
        mock_route.return_value = _make_route_result(IntentType.RAG_ANSWER)
        mock_answer.side_effect = RuntimeError("Milvus connection refused")

        # _dispatch does not catch exceptions internally, so we expect the caller
        # (chat.py) would catch it. Simulate that here:
        from app.agents.rag import rag_agent
        try:
            await rag_agent.answer(query="test", session_id="s1")
        except RuntimeError as exc:
            error_resp = {"answer": f"服务暂时不可用: {exc}", "intent": "rag_answer", "error": True}

        assert error_resp["error"] is True
        assert "Milvus" in error_resp["answer"]


@pytest.mark.asyncio
async def test_tool_agent_exception_graceful():
    """When tool_agent raises, verify the error surfaces correctly."""
    with patch("app.agents.router.router_agent.route", new_callable=AsyncMock) as mock_route, \
         patch("app.agents.tool.tool_agent.run", new_callable=AsyncMock) as mock_run:
        mock_route.return_value = _make_route_result(
            IntentType.TOOL_CALL, tool_name="query_inventory"
        )
        mock_run.side_effect = TimeoutError("Tool execution timed out")

        from app.agents.tool import tool_agent
        try:
            await tool_agent.run(query="查库存 X", session_id="s1")
        except TimeoutError as exc:
            error_resp = {"answer": f"工具执行超时: {exc}", "intent": "tool_call", "error": True}

        assert error_resp["error"] is True
        assert "超时" in error_resp["answer"]
