# -*- coding: utf-8 -*-
"""
Agentic RAG Integration Tests
================================
Tests for CRAG flow, Graph Critic, and Self-RAG integration.
These tests simulate the full flow without requiring Docker services.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.core.rag_engine import CriticEvaluator, QueryRewriter, RAGEngine


class TestCriticEvaluatorIntegration:
    """CriticEvaluator 集成测试 - 模拟真实场景"""

    def test_crag_flow_high_quality_no_retry(self):
        """高质量结果不需要重试"""
        query = "MAT-001 安全库存"
        results = [
            {"content": "MAT-001 安全库存为100件", "rerank_score": 0.8, "chunk_id": "c1"},
            {"content": "MAT-001 月均消耗50件", "rerank_score": 0.6, "chunk_id": "c2"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        assert eval_result["needs_retry"] is False
        assert eval_result["quality"] in ["high", "medium"]

    def test_crag_flow_low_quality_triggers_retry(self):
        """低质量结果触发重试"""
        query = "MAT-001 安全库存"
        results = [
            {"content": "供应商准入流程需要ISO认证", "rerank_score": 0.1, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        assert eval_result["needs_retry"] is True
        assert eval_result["quality"] == "low"

    def test_crag_flow_medium_quality_triggers_rewrite(self):
        """中等质量结果触发改写"""
        query = "安全库存计算公式"
        results = [
            {"content": "安全库存等于日均消耗乘以采购周期", "rerank_score": 0.3, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        assert eval_result["needs_retry"] is True
        assert eval_result["suggestion"] in ["rewrite_query", "expand_search"]

    def test_crag_rewrite_expand_search(self):
        """expand_search 策略：移除疑问词，保留实体"""
        query = "MAT-001 的安全库存是多少"
        rewritten = QueryRewriter.rewrite_for_retry(query, [], "expand_search")
        assert "MAT-001" in rewritten
        assert "是多少" not in rewritten

    def test_crag_rewrite_query_adds_keywords(self):
        """rewrite_query 策略：从结果中补充关键词"""
        query = "MAT-001 库存"
        results = [{"content": "安全库存公式 日均消耗 采购周期 系数1.5", "rerank_score": 0.5}]
        rewritten = QueryRewriter.rewrite_for_retry(query, results, "rewrite_query")
        assert len(rewritten) >= len(query)


class TestGraphCriticIntegration:
    """Graph Critic 集成测试"""

    def test_graph_critic_high_overlap(self):
        """高重叠度：Graph 结果应该被注入"""
        query = "MAT-001 相关订单"
        graph_context = "MAT-001 由供应商 SUP-001 供应，关联采购订单 PO-001 状态为待交付"
        
        query_keywords = CriticEvaluator.extract_keywords(query)
        graph_keywords = CriticEvaluator.extract_keywords(graph_context)
        overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
        
        assert overlap > 0.2  # 超过阈值，应该注入

    def test_graph_critic_low_overlap(self):
        """低重叠度：Graph 结果应该被过滤"""
        query = "安全库存计算公式"
        graph_context = "供应商评级标准 质量合格率40% 交期准时率30%"
        
        query_keywords = CriticEvaluator.extract_keywords(query)
        graph_keywords = CriticEvaluator.extract_keywords(graph_context)
        overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
        
        assert overlap <= 0.2  # 低于阈值，应该过滤

    def test_graph_critic_empty_context(self):
        """空 Graph 上下文"""
        query = "MAT-001 库存"
        graph_context = ""
        
        query_keywords = CriticEvaluator.extract_keywords(query)
        graph_keywords = CriticEvaluator.extract_keywords(graph_context)
        overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
        
        assert overlap == 0.0


class TestAdaptiveStrategyIntegration:
    """Adaptive RAG 策略升级集成测试"""

    def test_light_strategy_upgrade_on_low_quality(self):
        """light 策略在低质量时升级到 full"""
        from app.core.query_analyzer import query_analyzer
        result = query_analyzer.upgrade_strategy("light", "low")
        assert result == "full"

    def test_standard_strategy_upgrade_on_low_quality(self):
        """standard 策略在低质量时升级到 full"""
        from app.core.query_analyzer import query_analyzer
        result = query_analyzer.upgrade_strategy("standard", "low")
        assert result == "full"

    def test_full_strategy_never_upgrades(self):
        """full 策略永远不会升级"""
        from app.core.query_analyzer import query_analyzer
        for quality in ["high", "medium", "low"]:
            result = query_analyzer.upgrade_strategy("full", quality)
            assert result == "full"

    def test_high_quality_never_upgrades(self):
        """高质量永远不会触发升级"""
        from app.core.query_analyzer import query_analyzer
        for strategy in ["light", "standard", "full"]:
            result = query_analyzer.upgrade_strategy(strategy, "high")
            assert result == strategy


class TestRAGEngineGraphCritic:
    """RAGEngine Graph Critic 逻辑测试"""

    def test_graph_injection_with_high_overlap(self):
        """高重叠度时 Graph 结果应该被注入"""
        # 模拟 merged results
        merged = [
            {"content": "MAT-001 安全库存为100件", "score": 0.5, "chunk_id": "c1"},
        ]
        graph_context = "MAT-001 由供应商 SUP-001 供应"
        query = "MAT-001 供应商"
        
        # 模拟 Critic 评估
        query_keywords = CriticEvaluator.extract_keywords(query)
        graph_keywords = CriticEvaluator.extract_keywords(graph_context)
        overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
        
        if overlap > 0.2:
            import hashlib
            graph_chunk = {
                "chunk_id": f"neo4j_graph_{hashlib.md5(graph_context.encode()).hexdigest()[:8]}",
                "content": graph_context,
                "source": "neo4j_graph",
                "score": 0.5,
                "retrieval_source": "neo4j_graph",
            }
            merged.insert(0, graph_chunk)
        
        assert len(merged) == 2
        assert merged[0]["source"] == "neo4j_graph"

    def test_graph_injection_with_low_overlap(self):
        """低重叠度时 Graph 结果应该被过滤"""
        merged = [
            {"content": "MAT-001 安全库存为100件", "score": 0.5, "chunk_id": "c1", "source": "test_doc"},
        ]
        graph_context = "供应商评级标准 质量合格率40%"
        query = "安全库存计算公式"
        
        query_keywords = CriticEvaluator.extract_keywords(query)
        graph_keywords = CriticEvaluator.extract_keywords(graph_context)
        overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
        
        if overlap > 0.2:
            import hashlib
            graph_chunk = {
                "chunk_id": f"neo4j_graph_{hashlib.md5(graph_context.encode()).hexdigest()[:8]}",
                "content": graph_context,
                "source": "neo4j_graph",
                "score": 0.5,
                "retrieval_source": "neo4j_graph",
            }
            merged.insert(0, graph_chunk)
        
        assert len(merged) == 1  # Graph 结果被过滤
        assert merged[0]["source"] != "neo4j_graph"


class TestGraphChunkSplitToggle:
    """P1-3 图谱伪 chunk 拆分开关 GRAPH_CHUNK_SPLIT_BY_ENTITY 行为测试

    通过 mock 图谱与向量/BM25 检索直接驱动 RAGEngine.search()，
    验证拆分模式（默认）逐实体成 chunk、整段模式（回滚）拼单块。
    """

    def _run_search(self, split_by_entity: bool):
        engine = RAGEngine.__new__(RAGEngine)  # 跳过 __init__（不起真实模型/连接）
        engine.embedding = MagicMock()
        engine.embedding.embed_query.side_effect = Exception("skip vector")  # 降级纯 BM25，不走 Milvus
        engine.bm25 = MagicMock()
        engine.bm25.search.return_value = []
        engine.reranker = MagicMock()
        engine.reranker._model = None  # 走降级排序分支，不调真实 reranker
        engine._merge_results = MagicMock(return_value=[])

        two_ctx = {"MAT-001": "MAT-001 由供应商 SUP-001 供应", "MAT-002": "MAT-002 由供应商 SUP-002 供应"}

        graph_client = MagicMock()
        graph_client.is_connected = True
        graph_client._normalize_entity.side_effect = lambda e: e
        graph_client.get_2hop_subgraph_context_sync.side_effect = lambda e, entity_type=None: two_ctx.get(e, "")

        with patch("app.core.neo4j_client.neo4j_client", graph_client), \
             patch("app.core.entity_linker.entity_linker") as linker, \
             patch("app.core.rag.engine.settings") as st, \
             patch("app.core.rag.engine._get_cache_manager") as cache_mgr:
            linker.link.return_value = []
            cache_mgr.return_value.l1_get.return_value = None
            st.VECTOR_TOP_K = 50
            st.BM25_TOP_K = 50
            st.RERANK_TOP_K = 8
            st.RERANK_SCORE_THRESHOLD = 0.0
            st.SEMANTIC_CACHE_ENABLED = False
            st.RERANKER_ENABLED = False
            st.GRAPH_CHUNK_SPLIT_BY_ENTITY = split_by_entity
            return engine.search("MAT-001 和 MAT-002 的供应商", top_k=8)

    def test_split_mode_yields_per_entity_chunks(self):
        """拆分模式：两个实体各自成 chunk，source 带实体标识"""
        result = self._run_search(split_by_entity=True)
        graph_chunks = [r for r in result["results"] if r.get("retrieval_source") == "neo4j_graph"]
        assert len(graph_chunks) == 2
        assert {c["graph_entity"] for c in graph_chunks} == {"MAT-001", "MAT-002"}
        assert all(c["source"].startswith("neo4j_graph:") for c in graph_chunks)

    def test_merged_mode_yields_single_chunk(self):
        """整段模式（回滚）：两实体拼成单块，source 为裸 neo4j_graph"""
        result = self._run_search(split_by_entity=False)
        graph_chunks = [r for r in result["results"] if r.get("retrieval_source") == "neo4j_graph"]
        assert len(graph_chunks) == 1
        assert graph_chunks[0]["source"] == "neo4j_graph"
        assert "SUP-001" in graph_chunks[0]["content"] and "SUP-002" in graph_chunks[0]["content"]
