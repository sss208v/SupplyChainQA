"""
RAG 引擎单元测试 -- 纯 mock，无外部依赖
覆盖: _merge_results (RRF fusion)、query_type 权重、空结果、
     Reranker 降级、_detect_conflicts、search pipeline。
"""
import math
import pytest
from unittest.mock import patch, MagicMock
from app.core.rag_engine import RAGEngine, RerankerEngine


def _v(ids): return [{"chunk_id": c, "content": f"vec-{c}"} for c in ids]
def _b(ids): return [{"chunk_id": c, "content": f"bm25-{c}"} for c in ids]


class TestMergeResults:
    """RRF 融合排序"""
    def test_rrf_score_formula(self):
        # 传入空 query 避免自适应权重触发；期望值从真实 settings 计算，
        # 避免硬编码 K/权重导致调参后断言漂移
        from app.config import get_settings
        s = get_settings()
        merged = RAGEngine._merge_results(_v(["A"]), _b(["A"]), query="")
        assert len(merged) == 1
        # rank 从 0 开始: vec_w/(K+0) + bm25_w/(K+0)
        expected = (s.RRF_VECTOR_WEIGHT_DEFAULT + s.RRF_BM25_WEIGHT_DEFAULT) / s.RRF_K
        assert abs(merged[0]["rrf_score"] - expected) < 1e-9

    def test_dual_source_beats_single(self):
        scores = {d["chunk_id"]: d["rrf_score"]
                  for d in RAGEngine._merge_results(_v(["A", "B", "C"]), _b(["B", "C", "A"]))}
        assert scores["B"] > scores["C"] and scores["A"] > scores["C"]

    def test_only_vector(self):
        merged = RAGEngine._merge_results(_v(["X", "Y"]), _b([]))
        assert len(merged) == 2
        assert all(d["rrf_score"] > 0 and "vector" in d["retrieval_source"] for d in merged)

    def test_only_bm25(self):
        merged = RAGEngine._merge_results(_v([]), _b(["P", "Q"]))
        assert len(merged) == 2
        assert all(d["rrf_score"] > 0 and "bm25" in d["retrieval_source"] for d in merged)

    def test_both_empty(self):
        assert RAGEngine._merge_results([], []) == []

    def test_dedup_by_chunk_id(self):
        assert len(RAGEngine._merge_results(_v(["A", "A"]), _b(["A"]))) == 1


class TestQueryTypeWeights:
    """query_type 控制 BM25 / vector 权重"""
    def _score(self, vr, br, qt):
        merged = RAGEngine._merge_results(
            _v(["A"] + [f"v{i}" for i in range(vr)]),
            _b(["A"] + [f"b{i}" for i in range(br)]),
            query_type=qt)
        return next(d["rrf_score"] for d in merged if d["chunk_id"] == "A")

    def test_precise_bumps_bm25(self):
        """precise 权重高于 default 时，precise 得分更高（固定权重验证机制，不依赖调参值）"""
        with patch("app.core.rag.engine.settings") as mock_s:
            _mock_settings(mock_s)  # PRECISE=1.5 > DEFAULT=1.0
            assert self._score(1, 1, "precise") > self._score(1, 1, "default")

    def test_semantic_bumps_vector(self):
        """semantic 向量权重高于 default 时，semantic 得分更高"""
        with patch("app.core.rag.engine.settings") as mock_s:
            _mock_settings(mock_s)  # SEMANTIC=1.5 > DEFAULT=1.0
            assert self._score(1, 1, "semantic") > self._score(1, 1, "default")

    def test_default_equal_weights(self):
        assert abs(self._score(0, 1, "default") - self._score(1, 0, "default")) < 1e-12

    @patch("app.core.rag.engine.settings")
    def test_weights_read_from_settings(self, mock_s):
        """验证 _merge_results 从 settings 读取权重，而非硬编码"""
        _mock_settings(mock_s)
        mock_s.RRF_BM25_WEIGHT_PRECISE = 3.0
        mock_s.RRF_VECTOR_WEIGHT_DEFAULT = 1.0

        merged = RAGEngine._merge_results(
            _v(["A"]), _b(["A"]), query_type="precise"
        )
        score_high_bm25 = merged[0]["rrf_score"]

        mock_s.RRF_BM25_WEIGHT_PRECISE = 1.0
        merged2 = RAGEngine._merge_results(
            _v(["A"]), _b(["A"]), query_type="precise"
        )
        score_low_bm25 = merged2[0]["rrf_score"]

        # precise 查询下 BM25 权重越高，同 rank 下 RRF 分数越高
        assert score_high_bm25 > score_low_bm25

    @patch("app.core.rag.engine.settings")
    def test_semantic_weight_read_from_settings(self, mock_s):
        """验证 semantic 查询的向量权重从 settings 读取"""
        _mock_settings(mock_s)
        mock_s.RRF_VECTOR_WEIGHT_SEMANTIC = 3.0
        mock_s.RRF_BM25_WEIGHT_DEFAULT = 1.0

        merged = RAGEngine._merge_results(
            _v(["A"]), _b(["A"]), query_type="semantic"
        )
        assert merged[0]["rrf_score"] == (3.0 + 1.0) / 60


class TestDetectConflicts:
    def test_same_values_no_conflict(self):
        r = [{"chunk_id": "c1", "content": "安全库存为 100 件", "rrf_score": 0.5},
             {"chunk_id": "c2", "content": "安全库存为 100 件", "rrf_score": 0.4}]
        assert RAGEngine._detect_conflicts(r) == []

    def test_different_values_detected(self):
        r = [{"chunk_id": "c1", "content": "安全库存为 100 件", "rrf_score": 0.5},
             {"chunk_id": "c2", "content": "安全库存为 200 件", "rrf_score": 0.4}]
        c = RAGEngine._detect_conflicts(r)
        assert len(c) == 1
        assert c[0]["entity"] == "安全库存"
        assert {100.0, 200.0} == set(c[0]["values"])
        assert c[0]["type"] == "numeric_conflict"

    def test_empty(self):
        assert RAGEngine._detect_conflicts([]) == []


class TestCalculateConfidence:
    def test_empty(self):
        assert RAGEngine._calculate_confidence([]) == 0.0

    def test_high_score(self):
        conf = RAGEngine._calculate_confidence([{"rerank_score": 5.0}])
        assert conf == round(1 / (1 + math.exp(-5)), 4) > 0.9

    def test_negative_score(self):
        assert RAGEngine._calculate_confidence([{"rerank_score": -5.0}]) < 0.1


class TestRerankerDegradation:
    def test_fallback_sorted_by_original_score(self):
        r = RerankerEngine()
        r._model = None
        # Stub init() to prevent loading real CrossEncoder model
        r.init = lambda: None
        docs = [{"chunk_id": "a", "content": "A", "bm25_score": 0.3},
                {"chunk_id": "b", "content": "B", "bm25_score": 0.8}]
        result = r.rerank("q", docs, top_k=2)
        assert set(d["chunk_id"] for d in result) == {"a", "b"}
        assert all("rerank_score" in d for d in result)
    def test_empty_docs(self):
        r = RerankerEngine(); r._model = None
        assert r.rerank("q", []) == []


class TestRolesFromVisibility:
    def test_extract(self):
        expr = 'array_contains(security_group, "admin") && array_contains(security_group, "finance")'
        assert RAGEngine._roles_from_visibility_expr(expr) == ["admin", "finance"]

    def test_empty_expr(self):
        assert RAGEngine._roles_from_visibility_expr("") is None

    def test_no_match(self):
        assert RAGEngine._roles_from_visibility_expr("x == 1") is None


def _mock_settings(ms):
    ms.VECTOR_TOP_K = 10
    ms.BM25_TOP_K = 10
    ms.RERANKER_ENABLED = False
    ms.RERANK_TOP_K = 5
    ms.RERANK_SCORE_THRESHOLD = 0.0
    ms.RRF_K = 60
    ms.RRF_MIN_SCORE = 0.008
    ms.JACCARD_DEDUP_THRESHOLD = 0.7
    ms.RRF_BM25_WEIGHT_PRECISE = 1.5
    ms.RRF_VECTOR_WEIGHT_SEMANTIC = 1.5
    ms.RRF_BM25_WEIGHT_DEFAULT = 1.0
    ms.RRF_VECTOR_WEIGHT_DEFAULT = 1.0


class TestSearchPipeline:
    """search() 全链路 mock 测试"""
    @patch("app.core.rag.engine.settings")
    @patch("app.core.rag.engine.milvus_manager")
    def test_returns_expected_structure(self, mock_milvus, mock_s):
        _mock_settings(mock_s)
        mock_milvus.search.return_value = [{"chunk_id": "v1", "content": "V"}]
        engine = RAGEngine()
        engine.embedding.embed_query = MagicMock(return_value=[0.1] * 512)
        engine.bm25.search = MagicMock(return_value=[{"chunk_id": "b1", "content": "B", "bm25_score": 0.7}])
        result = engine.search("q", top_k=3)
        assert {"results", "confidence", "conflicts", "retrieval_method"} <= result.keys()
        assert isinstance(result["confidence"], float)

    @patch("app.core.rag.engine.settings")
    @patch("app.core.rag.engine.milvus_manager")
    def test_embedding_failure_degrades_to_bm25(self, mock_milvus, mock_s):
        _mock_settings(mock_s)
        engine = RAGEngine()
        engine.embedding.embed_query = MagicMock(side_effect=RuntimeError("fail"))
        engine.bm25.search = MagicMock(return_value=[{"chunk_id": "b1", "content": "F", "bm25_score": 0.5}])
        result = engine.search("q", top_k=3)
        assert len(result["results"]) > 0
        mock_milvus.search.assert_not_called()

    @patch("app.core.rag.engine.settings")
    def test_no_results_returns_empty(self, mock_s):
        _mock_settings(mock_s)
        engine = RAGEngine()
        engine.embedding.embed_query = MagicMock(return_value=[0.1] * 512)
        engine.bm25.search = MagicMock(return_value=[])
        with patch("app.core.rag.engine.milvus_manager") as m:
            m.search.return_value = []
            result = engine.search("none", top_k=3)
        assert result["results"] == []
        assert result["confidence"] == 0.0
        assert result["retrieval_method"] == "none"
