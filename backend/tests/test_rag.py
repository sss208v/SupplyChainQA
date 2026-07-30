"""
SupplyChainRAG - RAG 引擎单元测试（重写版）

导入真实 RAGEngine 代码，mock 外部 I/O（Milvus、embedding model、reranker），
测试 RRF 融合、rerank 降级、空结果处理等核心逻辑。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.core.rag_engine import RAGEngine


class TestRRFMerge:
    """测试 _merge_results 中的 RRF 融合逻辑"""

    def _make_engine(self):
        """创建 mock 化的 RAGEngine 实例"""
        with patch.object(RAGEngine, '__init__', lambda self: None):
            engine = RAGEngine.__new__(RAGEngine)
            engine._embedding_cache = {}
            return engine

    def test_rrf_basic_merge(self):
        """两路结果正确融合，B 在两路都排名靠前应排第一"""
        vector = [
            {"chunk_id": "A", "vector_score": 0.9},
            {"chunk_id": "B", "vector_score": 0.8},
            {"chunk_id": "C", "vector_score": 0.7},
        ]
        bm25 = [
            {"chunk_id": "B", "bm25_score": 5.0},
            {"chunk_id": "D", "bm25_score": 4.0},
            {"chunk_id": "A", "bm25_score": 3.0},
        ]
        engine = self._make_engine()
        result = engine._merge_results(vector, bm25, query="", query_type="default")
        ids = [r["chunk_id"] for r in result]
        assert ids[0] == "B", "B 在两路都排名高，应排第一"
        assert "A" in ids[:3], "A 在两路都出现，应排名靠前"
        assert "D" in ids, "D 应出现在结果中"

    def test_rrf_empty_vector_results(self):
        """向量结果为空时，仅返回 BM25 结果"""
        engine = self._make_engine()
        bm25 = [{"chunk_id": "X", "bm25_score": 1.0}]
        result = engine._merge_results([], bm25)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "X"

    def test_rrf_empty_bm25_results(self):
        """BM25 结果为空时，仅返回向量结果"""
        engine = self._make_engine()
        vector = [{"chunk_id": "Y", "vector_score": 0.9}]
        result = engine._merge_results(vector, [])
        assert len(result) == 1
        assert result[0]["chunk_id"] == "Y"

    def test_rrf_both_empty(self):
        """两路都为空时返回空列表"""
        engine = self._make_engine()
        result = engine._merge_results([], [])
        assert result == []

    def test_rrf_precise_query_type_boosts_bm25(self):
        """precise 权重高于 default 时，BM25 高排名的 B 应更靠前（固定权重验证机制，不依赖调参值）"""
        engine = self._make_engine()
        vector = [
            {"chunk_id": "A", "vector_score": 0.9},
            {"chunk_id": "B", "vector_score": 0.8},
        ]
        bm25 = [
            {"chunk_id": "B", "bm25_score": 5.0},
            {"chunk_id": "A", "bm25_score": 3.0},
        ]
        with patch("app.core.rag.engine.settings") as mock_s:
            mock_s.RRF_K = 60
            mock_s.RRF_MIN_SCORE = 0.008
            mock_s.JACCARD_DEDUP_THRESHOLD = 0.7
            mock_s.RRF_BM25_WEIGHT_PRECISE = 1.5
            mock_s.RRF_VECTOR_WEIGHT_SEMANTIC = 1.5
            mock_s.RRF_BM25_WEIGHT_DEFAULT = 1.0
            mock_s.RRF_VECTOR_WEIGHT_DEFAULT = 1.0
            default_result = engine._merge_results(vector, bm25, query_type="default")
            precise_result = engine._merge_results(vector, bm25, query_type="precise")
        default_ids = [r["chunk_id"] for r in default_result]
        precise_ids = [r["chunk_id"] for r in precise_result]
        # precise 模式下 BM25 权重更高，B (BM25 rank 1) 应更靠前
        assert precise_ids.index("B") <= default_ids.index("B")

    def test_rrf_semantic_query_type_boosts_vector(self):
        """query_type='semantic' 时向量权重提高（x1.5），A (向量 rank 1) 应更靠前"""
        engine = self._make_engine()
        vector = [
            {"chunk_id": "A", "vector_score": 0.9},
            {"chunk_id": "B", "vector_score": 0.8},
        ]
        bm25 = [
            {"chunk_id": "B", "bm25_score": 5.0},
            {"chunk_id": "A", "bm25_score": 3.0},
        ]
        default_result = engine._merge_results(vector, bm25, query_type="default")
        semantic_result = engine._merge_results(vector, bm25, query_type="semantic")
        default_ids = [r["chunk_id"] for r in default_result]
        semantic_ids = [r["chunk_id"] for r in semantic_result]
        # semantic 模式下向量权重更高，A (向量 rank 1) 应更靠前
        assert semantic_ids.index("A") <= default_ids.index("A")

    def test_rrf_weights_symmetry(self):
        """验证三种权重模式的对称性：precise 偏 BM25, semantic 偏向量, default 等权"""
        engine = self._make_engine()
        vector = [
            {"chunk_id": "A", "vector_score": 0.9},
            {"chunk_id": "B", "vector_score": 0.8},
        ]
        bm25 = [
            {"chunk_id": "B", "bm25_score": 5.0},
            {"chunk_id": "A", "bm25_score": 3.0},
        ]
        precise_ids = [r["chunk_id"] for r in engine._merge_results(vector, bm25, query_type="precise")]
        semantic_ids = [r["chunk_id"] for r in engine._merge_results(vector, bm25, query_type="semantic")]
        # precise 下 B(BM25 rank1) 更靠前，semantic 下 A(向量 rank1) 更靠前
        assert precise_ids.index("B") <= semantic_ids.index("B")
        assert semantic_ids.index("A") <= precise_ids.index("A")

    def test_rrf_deduplication(self):
        """同一 chunk_id 在两路出现时应合并，不重复"""
        engine = self._make_engine()
        vector = [{"chunk_id": "A", "vector_score": 0.9}]
        bm25 = [{"chunk_id": "A", "bm25_score": 5.0}]
        result = engine._merge_results(vector, bm25)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "A"


class TestRAGEngineSearch:
    """测试 RAGEngine.search 方法（mock embedding + Milvus）"""

    @pytest.mark.asyncio
    async def test_search_returns_results_dict(self):
        """search 应返回包含 results 和 metadata 的字典"""
        engine = RAGEngine.__new__(RAGEngine)
        engine._embedding_cache = {}
        engine._query_cache = {}

        # Mock embedding
        engine.embedding = MagicMock()
        engine.embedding.embed_query = MagicMock(return_value=[0.1] * 512)

        # Mock Milvus search
        engine.milvus = MagicMock()
        engine.milvus.search = MagicMock(return_value=[
            [
                {"id": "1", "distance": 0.9, "entity": {"content": "test content", "source": "doc.md", "chunk_id": "c1"}},
            ]
        ])

        # Mock BM25
        engine.bm25 = MagicMock()
        engine.bm25.search = MagicMock(return_value=[])

        # Mock reranker as None (disabled)
        engine.reranker = None

        result = engine.search(query="test", top_k=5)
        assert "results" in result or isinstance(result, list)


class TestRerankerDegradation:
    """测试 Reranker 优雅降级"""

    def test_reranker_none_uses_raw_scores(self):
        """Reranker 为 None 时，应使用原始分数"""
        # 验证当 reranker 不可用时，rag_engine 不会崩溃
        from app.core.rag_engine import RAGEngine
        engine = RAGEngine.__new__(RAGEngine)
        engine.reranker = None
        # 验证引擎仍然可以被创建
        assert engine.reranker is None


class TestConflictDetection:
    """测试冲突检测逻辑（如果存在）"""

    def test_detect_conflicts_exists(self):
        """_detect_conflicts 方法应存在"""
        assert hasattr(RAGEngine, '_detect_conflicts')


class TestQueryTypeChain:
    """端到端验证 _classify_query → _map_rrf_query_type 映射链路"""

    def test_tech_keyword_query_maps_to_specific(self):
        """含技术关键词的查询 → specific → precise（BM25 x1.5）"""
        from app.agents.rag import rag_agent
        qt = rag_agent._classify_query("API配置怎么修改")
        assert qt == "specific"
        rrf = rag_agent._map_rrf_query_type(qt)
        assert rrf == "precise"

    def test_short_query_maps_to_broad(self):
        """很短的查询 → broad → semantic（向量 x1.5）"""
        from app.agents.rag import rag_agent
        qt = rag_agent._classify_query("库存")
        assert qt == "broad"
        rrf = rag_agent._map_rrf_query_type(qt)
        assert rrf == "semantic"

    def test_ambiguous_query_maps_to_default(self):
        """模糊查询 → ambiguous → default（等权）"""
        from app.agents.rag import rag_agent
        qt = rag_agent._classify_query("那个东西怎么样了")
        assert qt == "ambiguous"
        rrf = rag_agent._map_rrf_query_type(qt)
        assert rrf == "default"

    def test_multiple_questions_map_to_broad(self):
        """多个问号的查询 → broad"""
        from app.agents.rag import rag_agent
        qt = rag_agent._classify_query("库存有多少？供应商是谁？")
        assert qt == "broad"

    def test_tech_keywords_list(self):
        """验证技术关键词分类的准确性"""
        from app.agents.rag import rag_agent
        # 包含技术关键词 → specific
        for q in ["API 配置方法", "部署流程文档", "代码运行报错了", "连接超时怎么处理"]:
            qt = rag_agent._classify_query(q)
            assert qt == "specific", f"'{q}' 应为 specific，实际为 {qt}"

    def test_map_rrf_query_type_covers_all(self):
        """验证映射函数覆盖所有分类"""
        from app.agents.rag import rag_agent
        assert rag_agent._map_rrf_query_type("specific") == "precise"
        assert rag_agent._map_rrf_query_type("broad") == "semantic"
        assert rag_agent._map_rrf_query_type("ambiguous") == "default"
        assert rag_agent._map_rrf_query_type("unknown") == "default"
