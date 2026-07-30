"""
tests/test_rag_pipeline_unified.py — RAG 检索管线单一实现验证（P1-5）

验证 prepare_retrieval / execute_retrieval 拆分后：
- answer()（非流式 /chat/ask）与 handlers/rag_answer.py（流式 /chat/stream）
  共用同一套检索实现，检索结果一致
- CRAG 开关与 Self-RAG 策略由 execute_retrieval 统一控制
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.rag import RAGAgent


def _mk_agent(search_results):
    """构造 mock 引擎的 RAGAgent：search 返回固定结果"""
    agent = RAGAgent.__new__(RAGAgent)
    agent.rag = MagicMock()
    agent.rag.search = MagicMock(return_value={"results": search_results})
    import logging
    agent.logger = logging.getLogger("test")
    return agent


def _prep(search_queries=None):
    """构造 prepare_retrieval 的输出（跳过 LLM 依赖）"""
    analysis = MagicMock()
    analysis.complexity = 0.3
    analysis.strategy = "light"
    return {
        "query_type": "specific",
        "rrf_query_type": "precise",
        "search_queries": search_queries or ["测试查询"],
        "analysis": analysis,
        "strategy_config": {"top_k": 3, "use_self_rag": False, "use_crag": False},
        "adaptive_top_k": 3,
        "t_prepare": 0.01,
    }


class TestExecuteRetrieval:
    @pytest.mark.asyncio
    async def test_dedup_across_queries(self):
        """多查询检索结果按 chunk_id 去重"""
        agent = _mk_agent([
            {"chunk_id": "c1", "content": "A", "rerank_score": 0.9},
            {"chunk_id": "c1", "content": "A", "rerank_score": 0.9},
            {"chunk_id": "c2", "content": "B", "rerank_score": 0.5},
        ])
        retrieval = await agent.execute_retrieval("测试查询", _prep(["q1", "q2"]))
        ids = [r["chunk_id"] for r in retrieval["results"]]
        assert ids == ["c1", "c2"]
        # 两个查询 → 两次 engine.search
        assert agent.rag.search.call_count == 2

    @pytest.mark.asyncio
    async def test_visibility_expr_passthrough(self):
        """visibility_expr 透传给引擎（RBAC 行级过滤不丢失）"""
        agent = _mk_agent([{"chunk_id": "c1", "content": "A"}])
        expr = 'array_contains(security_group, "finance")'
        await agent.execute_retrieval("q", _prep(), visibility_expr=expr)
        assert agent.rag.search.call_args.kwargs["visibility_expr"] == expr

    @pytest.mark.asyncio
    async def test_crag_disabled_by_strategy(self):
        """strategy_config.use_crag=False 时不触发 CRAG 重试"""
        agent = _mk_agent([{"chunk_id": "c1", "content": "无关内容", "rerank_score": 0.01}])
        with patch("app.agents.rag.CriticEvaluator") as mock_critic:
            await agent.execute_retrieval("q", _prep())
            mock_critic.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_crag_retry_on_low_quality(self):
        """CRAG 判定低质量 → 改写 Query 重检索并合并结果"""
        agent = _mk_agent([{"chunk_id": "c1", "content": "A", "rerank_score": 0.1}])
        prep = _prep()
        prep["strategy_config"]["use_crag"] = True

        retry_results = [{"chunk_id": "c2", "content": "B", "rerank_score": 0.8}]

        def _search_side_effect(*args, **kwargs):
            # 第二次调用（重试）返回新结果
            if agent.rag.search.call_count >= 2:
                return {"results": retry_results}
            return {"results": [{"chunk_id": "c1", "content": "A", "rerank_score": 0.1}]}

        agent.rag.search = MagicMock(side_effect=_search_side_effect)

        with patch("app.agents.rag.settings") as mock_s, \
             patch("app.agents.rag.CriticEvaluator") as mock_critic, \
             patch("app.agents.rag.QueryRewriter") as mock_rewriter:
            mock_s.CRAG_ENABLED = True
            mock_s.LLM_RELEVANCE_ENABLED = False
            mock_critic.evaluate.side_effect = [
                {"quality": "low", "keyword_coverage": 0.1, "top_score": 0.1,
                 "suggestion": "换关键词", "needs_retry": True},
                {"quality": "high", "keyword_coverage": 0.8, "top_score": 0.8,
                 "suggestion": "", "needs_retry": False},
            ]
            mock_rewriter.rewrite_for_retry.return_value = "改写后的查询"

            retrieval = await agent.execute_retrieval("q", prep)

        ids = {r["chunk_id"] for r in retrieval["results"]}
        assert ids == {"c1", "c2"}  # 原始 + 重试结果合并
        mock_rewriter.rewrite_for_retry.assert_called_once()


class TestAnswerUsesUnifiedPipeline:
    @pytest.mark.asyncio
    async def test_answer_delegates_to_execute_retrieval(self):
        """answer() 必须通过 execute_retrieval 检索（与流式 handler 同一实现）"""
        agent = _mk_agent([])
        agent.prepare_retrieval = AsyncMock(return_value=_prep())
        agent.execute_retrieval = AsyncMock(return_value={
            "results": [], "all_chunks": [], "relevance_scores": [], "t_search": 0.01,
        })

        result = await agent.answer("测试查询")

        agent.prepare_retrieval.assert_awaited_once_with("测试查询")
        agent.execute_retrieval.assert_awaited_once()
        # 空结果走兜底文案
        assert result["confidence"] == 0.0
        assert result["query_type"] == "specific"

    def test_handler_no_longer_owns_pipeline(self):
        """流式 handler 不再自建检索管线（防止双实现回潮）"""
        import inspect
        from app.api.handlers import rag_answer as handler_mod
        src = inspect.getsource(handler_mod)
        # handler 必须复用 RAGAgent 的统一入口
        assert "prepare_retrieval" in src
        assert "execute_retrieval" in src
        # 不允许再出现自建的查询理解调用（管线职责已收敛到 RAGAgent）
        assert "_classify_query" not in src
        assert "_prepare_queries" not in src
