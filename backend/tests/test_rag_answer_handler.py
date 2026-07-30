"""
tests/test_rag_answer_handler.py — handle_rag_answer 流式 handler 单元测试

DEMO_MODE 路径（不触发真实 LLM），mock RAGAgent 统一检索管线，
验证 SSE 事件序列、来源下发、性能指标与记忆写入。
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.chat_helpers import ChatRequest
from app.api.handlers.rag_answer import handle_rag_answer


def _mk_prep():
    analysis = MagicMock()
    analysis.complexity = 0.4
    analysis.strategy = "standard"
    analysis.entity_count = 1
    analysis.needs_reasoning = False
    analysis.method = "rule"
    return {
        "query_type": "specific",
        "rrf_query_type": "precise",
        "search_queries": ["安全库存标准"],
        "analysis": analysis,
        "strategy_config": {"top_k": 3, "use_self_rag": False, "use_query_rewrite": False},
        "adaptive_top_k": 3,
        "t_prepare": 0.01,
    }


def _mk_rag_agent(results):
    agent = MagicMock()
    agent.prepare_retrieval = AsyncMock(return_value=_mk_prep())
    agent.execute_retrieval = AsyncMock(return_value={
        "results": results,
        "all_chunks": list(results),
        "relevance_scores": [],
        "t_search": 0.02,
    })
    agent._format_context = MagicMock(return_value=(
        "上下文内容",
        [{"index": 1, "source": "库存管理制度.md", "score": 5.0}],
    ))
    agent.rag = MagicMock()
    agent.rag._detect_conflicts = MagicMock(return_value=[])
    agent.RAG_SYSTEM_PROMPT = "历史:{chat_history}\n资料:{context}"
    return agent


def _events(raw_events):
    """解析 SSE 字符串列表为 dict 列表"""
    parsed = []
    for e in raw_events:
        payload = e.replace("data: ", "").strip()
        if payload and payload != "[DONE]":
            parsed.append(json.loads(payload))
    return parsed


async def _run_handler(rag_agent, memory=None, monkeypatch=None):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "CLIP_ENABLED", False)
    monkeypatch.setattr(settings, "COVERAGE_ENABLED", False)

    milvus = MagicMock()
    milvus.build_visibility_expr = MagicMock(return_value="")
    neo4j = MagicMock()
    neo4j.is_connected = False

    body = ChatRequest(query="安全库存标准是什么")
    raw = []
    async for event in handle_rag_answer(
        "安全库存标准是什么", "purchase", "sess-1", body,
        None, 0.0, 0.01, "user-1",
        rag_agent, milvus, neo4j, memory, MagicMock(),
    ):
        raw.append(event)
    return _events(raw)


@pytest.mark.asyncio
async def test_rag_answer_demo_mode_event_sequence(monkeypatch):
    """完整事件序列：dag_progress → query_analysis → content → sources → performance_metrics"""
    results = [{"chunk_id": "c1", "content": "安全库存为100件", "source": "库存管理制度.md", "rerank_score": 5.0}]
    agent = _mk_rag_agent(results)

    events = await _run_handler(agent, memory=None, monkeypatch=monkeypatch)
    types = [e["type"] for e in events]

    assert "dag_progress" in types
    assert "query_analysis" in types
    assert "content" in types           # DEMO_MODE 降级回答
    assert "sources" in types
    assert "performance_metrics" in types
    # 统一管线被调用（单一实现验证）
    agent.prepare_retrieval.assert_awaited_once()
    agent.execute_retrieval.assert_awaited_once()


@pytest.mark.asyncio
async def test_rag_answer_conflicts_event(monkeypatch):
    """检测到数据冲突时下发 conflicts 事件"""
    results = [{"chunk_id": "c1", "content": "安全库存为100件", "rerank_score": 5.0}]
    agent = _mk_rag_agent(results)
    agent.rag._detect_conflicts = MagicMock(return_value=[
        {"entity": "安全库存", "values": [100.0, 200.0], "sources": ["c1", "c2"], "type": "numeric_conflict"},
    ])

    events = await _run_handler(agent, memory=None, monkeypatch=monkeypatch)
    conflict_events = [e for e in events if e["type"] == "conflicts"]
    assert len(conflict_events) == 1
    assert conflict_events[0]["conflicts"][0]["entity"] == "安全库存"


@pytest.mark.asyncio
async def test_rag_answer_memory_written_with_user_id(monkeypatch):
    """对话记忆写入必须携带 user_id（隔离约束）"""
    results = [{"chunk_id": "c1", "content": "内容", "rerank_score": 5.0}]
    agent = _mk_rag_agent(results)
    memory = MagicMock()
    memory.get_context_string = AsyncMock(return_value="")
    memory.add_message = AsyncMock()

    await _run_handler(agent, memory=memory, monkeypatch=monkeypatch)

    assert memory.add_message.await_count == 2
    for call in memory.add_message.await_args_list:
        assert call.kwargs.get("user_id") == "user-1"


@pytest.mark.asyncio
async def test_rag_answer_empty_results(monkeypatch):
    """空检索结果不崩溃，仍产出 content 与完成事件"""
    agent = _mk_rag_agent([])
    agent._format_context = MagicMock(return_value=("", []))

    events = await _run_handler(agent, memory=None, monkeypatch=monkeypatch)
    types = [e["type"] for e in events]
    assert "content" in types
    # 无来源时不下发 sources
    assert "sources" not in types
