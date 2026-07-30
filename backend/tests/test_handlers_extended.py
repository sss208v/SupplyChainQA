"""
tests/test_handlers_extended.py — hybrid / goal handler 单测 + /ask vs /stream 一致性

补齐 Spec 3.6 要求：各 handler 覆盖正常路径 / 空输入 / 降级；
以及 Spec 3.3 验收基准：非流式 answer() 与流式 handler 对同一检索结果
产出一致的 sources 与 confidence（同一 sigmoid 归一化口径）。
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.chat_helpers import ChatRequest
from app.core.utils import sigmoid_normalize


def _events(raw_events):
    parsed = []
    for e in raw_events:
        payload = e.replace("data: ", "").strip()
        if payload and payload != "[DONE]":
            parsed.append(json.loads(payload))
    return parsed


async def _collect(agen):
    return _events([e async for e in agen])


# ===========================================================================
# handle_hybrid — 正常 / 空结果降级 / 工具失败降级
# ===========================================================================

class TestHandleHybrid:
    def _mk_agent(self, results):
        agent = MagicMock()
        agent.rag = MagicMock()
        agent.rag.search = MagicMock(return_value={"results": results})
        agent._format_context = MagicMock(return_value=("上下文", [{"index": 1, "source": "s.md"}]))
        return agent

    def _mk_milvus(self):
        m = MagicMock()
        m.build_visibility_expr = MagicMock(return_value="")
        return m

    @pytest.mark.asyncio
    async def test_normal_path_rag_plus_tool(self):
        """RAG 命中 + 工具成功 → sources / tool_call / content 事件齐全"""
        from app.api.handlers.hybrid import handle_hybrid
        agent = self._mk_agent([{"chunk_id": "c1", "content": "A", "rerank_score": 2.0}])

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="融合回答"))
        mock_tool_agent = MagicMock()
        mock_tool_agent.execute = AsyncMock(return_value={"result": "库存100件"})

        with patch("app.api.handlers.hybrid._is_tool_allowed", return_value=True), \
             patch("app.agents.tool.tool_agent", mock_tool_agent), \
             patch("app.api.handlers.hybrid.LLMFactory") as mock_factory:
            mock_factory.get_llm = MagicMock(return_value=mock_llm)
            events = await _collect(handle_hybrid(
                "查库存", "query_inventory", "purchase", "u1",
                ChatRequest(query="查库存"), agent, self._mk_milvus(),
            ))

        types = [e["type"] for e in events]
        assert "hybrid_start" in types
        assert "sources" in types
        assert "tool_call" in types
        assert types[-1] == "content"
        assert events[-1]["content"] == "融合回答"

    @pytest.mark.asyncio
    async def test_empty_results_degrades_to_guide(self):
        """RAG 空 + 无工具 → 引导话术，不调用 LLM"""
        from app.api.handlers.hybrid import handle_hybrid
        agent = self._mk_agent([])
        agent._format_context = MagicMock(return_value=("", []))

        events = await _collect(handle_hybrid(
            "不存在的问题", None, "purchase", "u1",
            ChatRequest(query="不存在的问题"), agent, self._mk_milvus(),
        ))

        contents = [e for e in events if e["type"] == "content"]
        assert len(contents) == 1
        assert "均未查到" in contents[0]["content"]

    @pytest.mark.asyncio
    async def test_tool_failure_degrades_gracefully(self):
        """工具抛异常 → error 事件后仍用 RAG 上下文生成回答"""
        from app.api.handlers.hybrid import handle_hybrid
        agent = self._mk_agent([{"chunk_id": "c1", "content": "A", "rerank_score": 2.0}])

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="仅知识库回答"))
        mock_tool_agent = MagicMock()
        mock_tool_agent.execute = AsyncMock(side_effect=RuntimeError("tool down"))

        with patch("app.api.handlers.hybrid._is_tool_allowed", return_value=True), \
             patch("app.agents.tool.tool_agent", mock_tool_agent), \
             patch("app.api.handlers.hybrid.LLMFactory") as mock_factory:
            mock_factory.get_llm = MagicMock(return_value=mock_llm)
            events = await _collect(handle_hybrid(
                "查库存", "query_inventory", "purchase", "u1",
                ChatRequest(query="查库存"), agent, self._mk_milvus(),
            ))

        types = [e["type"] for e in events]
        assert "error" in types           # 工具失败提示
        assert types[-1] == "content"     # 仍产出回答


# ===========================================================================
# handle_goal — 正常 / 空计划
# ===========================================================================

class TestHandleGoal:
    @pytest.mark.asyncio
    async def test_normal_plan_and_steps(self):
        """编排返回计划与步骤 → orchestrator_plan / agent_step / content"""
        from app.api.handlers.goal import handle_goal
        orchestrator = MagicMock()
        orchestrator.run = AsyncMock(return_value={
            "plan": {"goal": "补货", "steps": [
                {"agent": "InventoryAgent", "task": "查库存"},
                {"agent": "PurchaseAgent", "task": "建采购单"},
            ]},
            "execution": {"step_results": [
                {"step": 1, "agent": "InventoryAgent", "task": "查库存", "duration_ms": 10},
                {"step": 2, "agent": "PurchaseAgent", "task": "建采购单", "duration_ms": 20, "error": "超时"},
            ], "total_steps": 2, "success_steps": 1},
            "answer": "已完成补货流程分析",
        })

        events = await _collect(handle_goal("帮我补货", "sess-1", "u1", orchestrator))
        types = [e["type"] for e in events]

        assert types[0] == "orchestrator_start"
        assert "orchestrator_plan" in types
        step_events = [e for e in events if e["type"] == "agent_step"]
        assert len(step_events) == 2
        assert step_events[1]["status"] == "error"   # 失败步骤标记
        assert events[-1]["content"] == "已完成补货流程分析"

    @pytest.mark.asyncio
    async def test_empty_plan_still_returns_answer(self):
        """空计划（无 steps）→ 不发 plan 事件，仍产出 content"""
        from app.api.handlers.goal import handle_goal
        orchestrator = MagicMock()
        orchestrator.run = AsyncMock(return_value={
            "plan": {}, "execution": {}, "answer": "无需拆解，直接回答",
        })

        events = await _collect(handle_goal("你好", "sess-1", "u1", orchestrator))
        types = [e["type"] for e in events]
        assert "orchestrator_plan" not in types
        assert events[-1]["content"] == "无需拆解，直接回答"


# ===========================================================================
# /ask（answer()）与 /stream（handler）一致性 — Spec 3.3 验收基准
# ===========================================================================

class TestAskStreamConsistency:
    RESULTS = [
        {"chunk_id": "c1", "content": "安全库存为100件", "source": "库存管理制度.md",
         "section_title": "", "page_num": 0, "rerank_score": 2.5},
        {"chunk_id": "c2", "content": "补货周期为7天", "source": "采购管理规范.md",
         "section_title": "", "page_num": 0, "rerank_score": 1.0},
    ]

    def _mk_prep(self):
        analysis = MagicMock()
        analysis.complexity = 0.4
        analysis.strategy = "standard"
        analysis.entity_count = 0
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

    def _retrieval(self):
        return {
            "results": list(self.RESULTS),
            "all_chunks": list(self.RESULTS),
            "relevance_scores": [],
            "t_search": 0.01,
        }

    @pytest.mark.asyncio
    async def test_same_sources_and_confidence(self, monkeypatch):
        """同一检索结果下，非流式 answer() 与流式 handler 的 sources/confidence 一致"""
        from app.agents.rag import RAGAgent
        from app.api.handlers.rag_answer import handle_rag_answer
        from app.config import get_settings

        expected_conf = round(sigmoid_normalize(2.5), 4)

        # ---- 非流式：真实 RAGAgent + mock 检索与 LLM ----
        agent = RAGAgent.__new__(RAGAgent)
        agent.rag = MagicMock()
        agent.rag._detect_conflicts = MagicMock(return_value=[])  # 避免 MagicMock 进入 SSE 序列化
        import logging
        agent.logger = logging.getLogger("test")
        agent.prepare_retrieval = AsyncMock(return_value=self._mk_prep())
        agent.execute_retrieval = AsyncMock(return_value=self._retrieval())

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="回答内容"))
        with patch("app.agents.rag.LLMFactory") as mock_factory, \
             patch("app.agents.rag.chat_memory", None):
            mock_factory.get_llm = MagicMock(return_value=mock_llm)
            ask_result = await agent.answer("安全库存标准")

        # ---- 流式 handler：同一 agent（DEMO_MODE 跳过 LLM 流式）----
        settings = get_settings()
        monkeypatch.setattr(settings, "DEMO_MODE", True)
        monkeypatch.setattr(settings, "CLIP_ENABLED", False)
        monkeypatch.setattr(settings, "COVERAGE_ENABLED", False)

        agent.RAG_SYSTEM_PROMPT = RAGAgent.RAG_SYSTEM_PROMPT
        milvus = MagicMock()
        milvus.build_visibility_expr = MagicMock(return_value="")
        neo4j = MagicMock()
        neo4j.is_connected = False

        raw = []
        async for event in handle_rag_answer(
            "安全库存标准", "purchase", "sess-1", ChatRequest(query="安全库存标准"),
            None, 0.0, 0.01, "u1", agent, milvus, neo4j, None, MagicMock(),
        ):
            raw.append(event)
        stream_events = _events(raw)
        sources_event = next(e for e in stream_events if e["type"] == "sources")

        # confidence 同一 sigmoid 口径
        assert ask_result["confidence"] == expected_conf
        assert sources_event["confidence"] == expected_conf
        # sources 同一来源集合（均含父子文档扩展路径 _format_context）
        ask_sources = {s["source"] for s in ask_result["sources"]}
        stream_sources = {s["source"] for s in sources_event["sources"]}
        assert ask_sources == stream_sources == {"库存管理制度.md", "采购管理规范.md"}
