"""
RAG 引擎扩展单元测试 — 覆盖 engine.py 中原测试未涉及的辅助方法

覆盖范围：
  1. _normalize_query_entities: 实体编码归一化（去空白、O→0、补连字符）
  2. _filter_low_score: RRF 低分过滤
  3. _dedup_by_similarity: Jaccard 语义去重
  4. fuse_with_graph: 图谱结果融合排序
  5. index_document: 文档索引到 Milvus + BM25
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.rag_engine import RAGEngine


# ---------------------------------------------------------------------------
# 1. TestNormalizeQueryEntities
# ---------------------------------------------------------------------------

class TestNormalizeQueryEntities:
    """_normalize_query_entities: 实体编码归一化。"""

    def test_lowercase_to_uppercase(self):
        assert "MAT-001" in RAGEngine._normalize_query_entities("mat-001")

    def test_removes_whitespace(self):
        assert RAGEngine._normalize_query_entities("MAT 001") == "MAT-001"

    def test_o_to_zero_correction(self):
        """O→0 纠错：MAT-OO1 → MAT-001"""
        result = RAGEngine._normalize_query_entities("MAT-OO1")
        assert result == "MAT-001"

    def test_adds_hyphen(self):
        """MAT001 → MAT-001"""
        result = RAGEngine._normalize_query_entities("MAT001")
        assert result == "MAT-001"

    def test_po_normalization(self):
        """PO20250101 → PO-20250101"""
        result = RAGEngine._normalize_query_entities("PO20250101")
        assert result == "PO-20250101"

    def test_sup_normalization(self):
        """sup001 → SUP-001"""
        result = RAGEngine._normalize_query_entities("sup001")
        assert result == "SUP-001"

    def test_already_normalized_unchanged(self):
        """已经规范的编码不变"""
        result = RAGEngine._normalize_query_entities("MAT-001")
        assert result == "MAT-001"

    def test_mixed_o_and_zero(self):
        """MAT-O01 → MAT-001"""
        result = RAGEngine._normalize_query_entities("MAT-O01")
        assert result == "MAT-001"

    def test_non_entity_text_passthrough(self):
        """非实体文本保持大写但不做编码修改"""
        result = RAGEngine._normalize_query_entities("什么是安全库存")
        assert "什么是安全库存" in result.upper() or result == "什么是安全库存"


# ---------------------------------------------------------------------------
# 2. TestFilterLowScore
# ---------------------------------------------------------------------------

class TestFilterLowScore:
    """_filter_low_score: 过滤 RRF 分数过低的结果。"""

    def test_filters_below_threshold(self):
        results = [
            {"chunk_id": "a", "rrf_score": 0.05},
            {"chunk_id": "b", "rrf_score": 0.003},
            {"chunk_id": "c", "rrf_score": 0.02},
        ]
        filtered = RAGEngine._filter_low_score(results, min_rrf=0.008)
        assert len(filtered) == 2
        assert all(r["rrf_score"] >= 0.008 for r in filtered)

    def test_all_above_threshold_kept(self):
        results = [
            {"chunk_id": "a", "rrf_score": 0.05},
            {"chunk_id": "b", "rrf_score": 0.10},
        ]
        filtered = RAGEngine._filter_low_score(results, min_rrf=0.008)
        assert len(filtered) == 2

    def test_all_below_threshold_empty(self):
        results = [
            {"chunk_id": "a", "rrf_score": 0.001},
            {"chunk_id": "b", "rrf_score": 0.002},
        ]
        filtered = RAGEngine._filter_low_score(results, min_rrf=0.008)
        assert filtered == []

    def test_empty_input(self):
        assert RAGEngine._filter_low_score([], min_rrf=0.008) == []

    def test_default_threshold(self):
        """默认阈值 0.008"""
        results = [{"chunk_id": "a", "rrf_score": 0.007}]
        filtered = RAGEngine._filter_low_score(results)
        assert filtered == []


# ---------------------------------------------------------------------------
# 3. TestDedupBySimilarity
# ---------------------------------------------------------------------------

class TestDedupBySimilarity:
    """_dedup_by_similarity: Jaccard 相似度去重。"""

    def test_identical_content_deduped(self):
        """完全相同内容 → 只保留第一个。"""
        results = [
            {"chunk_id": "a", "content": "供应链管理是一种方法论", "rrf_score": 0.5},
            {"chunk_id": "b", "content": "供应链管理是一种方法论", "rrf_score": 0.4},
        ]
        deduped = RAGEngine._dedup_by_similarity(results, threshold=0.7)
        assert len(deduped) == 1
        assert deduped[0]["chunk_id"] == "a"

    def test_different_content_all_kept(self):
        """完全不同内容 → 全部保留。"""
        results = [
            {"chunk_id": "a", "content": "ABCDEFGHIJKLMNOP", "rrf_score": 0.5},
            {"chunk_id": "b", "content": "ZYXWVUTSRQPONMLK", "rrf_score": 0.4},
        ]
        deduped = RAGEngine._dedup_by_similarity(results, threshold=0.7)
        assert len(deduped) == 2

    def test_single_item_returned_as_is(self):
        results = [{"chunk_id": "a", "content": "test", "rrf_score": 0.5}]
        deduped = RAGEngine._dedup_by_similarity(results, threshold=0.7)
        assert len(deduped) == 1

    def test_empty_input(self):
        assert RAGEngine._dedup_by_similarity([], threshold=0.7) == []

    def test_high_similarity_deduped(self):
        """高相似度内容（仅末尾有差异）→ 去重。"""
        base_text = "供应链管理包含计划采购生产交付退货五大流程" * 3
        results = [
            {"chunk_id": "a", "content": base_text + "A", "rrf_score": 0.5},
            {"chunk_id": "b", "content": base_text + "B", "rrf_score": 0.4},
        ]
        deduped = RAGEngine._dedup_by_similarity(results, threshold=0.7)
        assert len(deduped) == 1

    def test_only_compares_with_last_three(self):
        """去重只和前 3 个比较，第 5 个不和第 1 个比较。"""
        results = [
            {"chunk_id": "a", "content": "unique content alpha", "rrf_score": 0.9},
            {"chunk_id": "b", "content": "different content beta", "rrf_score": 0.8},
            {"chunk_id": "c", "content": "another content gamma", "rrf_score": 0.7},
            {"chunk_id": "d", "content": "fourth distinct content", "rrf_score": 0.6},
            # 这个和 "a" 完全相同，但只与前 3 个（b,c,d）比较，不会因为和 a 相似被去重
            {"chunk_id": "e", "content": "unique content alpha", "rrf_score": 0.5},
        ]
        deduped = RAGEngine._dedup_by_similarity(results, threshold=0.7)
        # "e" 只和 b,c,d 比较，和它们不相似，所以保留
        assert len(deduped) == 5


# ---------------------------------------------------------------------------
# 4. TestFuseWithGraph
# ---------------------------------------------------------------------------

class TestFuseWithGraph:
    """fuse_with_graph: 图谱结果融合排序。"""

    @patch("app.core.rag.engine.settings")
    def test_no_graph_entities_returns_unchanged(self, mock_settings):
        """无图谱匹配 → 原样返回。"""
        mock_settings.GRAPH_FUSION_ALPHA = 0.7
        mock_settings.GRAPH_FUSION_BETA = 0.3
        results = [
            {"chunk_id": "a", "content": "test", "rrf_score": 0.5},
        ]
        fused = RAGEngine.fuse_with_graph(results, set())
        assert fused == results
        assert "graph_score" not in fused[0]

    @patch("app.core.rag.engine.settings")
    def test_graph_hit_boosts_score(self, mock_settings):
        """含匹配实体的 chunk 获得 graph_score=1.0 加成。"""
        mock_settings.GRAPH_FUSION_ALPHA = 0.7
        mock_settings.GRAPH_FUSION_BETA = 0.3
        results = [
            {"chunk_id": "a", "content": "MAT-001 的库存为 100", "rrf_score": 0.3},
            {"chunk_id": "b", "content": "供应链管理概述", "rrf_score": 0.5},
        ]
        fused = RAGEngine.fuse_with_graph(results, {"MAT-001"})

        # MAT-001 相关 chunk 应该排在前面
        assert fused[0]["chunk_id"] == "a"
        assert fused[0]["graph_score"] == 1.0
        assert fused[1]["graph_score"] == 0.0
        # final_score = alpha * rrf_score + beta * graph_score
        assert fused[0]["final_score"] == 0.7 * 0.3 + 0.3 * 1.0

    @patch("app.core.rag.engine.settings")
    def test_custom_alpha_beta(self, mock_settings):
        """自定义 alpha/beta 参数。"""
        mock_settings.GRAPH_FUSION_ALPHA = 0.5
        mock_settings.GRAPH_FUSION_BETA = 0.5
        results = [
            {"chunk_id": "a", "content": "MAT-001 test", "rrf_score": 0.4},
        ]
        fused = RAGEngine.fuse_with_graph(results, {"MAT-001"}, alpha=0.6, beta=0.4)
        assert fused[0]["final_score"] == 0.6 * 0.4 + 0.4 * 1.0

    @patch("app.core.rag.engine.settings")
    def test_case_insensitive_matching(self, mock_settings):
        """匹配不区分大小写。"""
        mock_settings.GRAPH_FUSION_ALPHA = 0.7
        mock_settings.GRAPH_FUSION_BETA = 0.3
        results = [
            {"chunk_id": "a", "content": "mat-001 的信息", "rrf_score": 0.3},
        ]
        fused = RAGEngine.fuse_with_graph(results, {"MAT-001"})
        assert fused[0]["graph_score"] == 1.0

    @patch("app.core.rag.engine.settings")
    def test_multiple_entities(self, mock_settings):
        """多个图谱实体，任一匹配即加分。"""
        mock_settings.GRAPH_FUSION_ALPHA = 0.7
        mock_settings.GRAPH_FUSION_BETA = 0.3
        results = [
            {"chunk_id": "a", "content": "PO-001 订单详情", "rrf_score": 0.3},
            {"chunk_id": "b", "content": "无关内容", "rrf_score": 0.4},
        ]
        fused = RAGEngine.fuse_with_graph(results, {"MAT-001", "PO-001"})
        assert fused[0]["chunk_id"] == "a"
        assert fused[0]["graph_score"] == 1.0


# ---------------------------------------------------------------------------
# 5. TestIndexDocument
# ---------------------------------------------------------------------------

class TestIndexDocument:
    """index_document: 文档索引到 Milvus + BM25。"""

    @patch("app.core.rag.engine.milvus_manager")
    def test_indexes_chunks_to_milvus_and_bm25(self, mock_milvus):
        """正常索引：embed + milvus insert + bm25 index。"""
        mock_milvus.batch_insert.return_value = {"insert_count": 2}

        engine = RAGEngine()
        engine.embedding.embed_documents = MagicMock(return_value=[[0.1]*512, [0.2]*512])
        engine.bm25.index_documents = MagicMock()

        chunks = [
            {"chunk_id": "c1", "content": "chunk 1 content", "source": "doc.md", "page_num": 1},
            {"chunk_id": "c2", "content": "chunk 2 content", "source": "doc.md", "page_num": 2},
        ]
        result = engine.index_document("doc-001", chunks, security_group=["admin", "finance"])

        assert result["doc_id"] == "doc-001"
        assert result["chunk_count"] == 2
        assert result["insert_count"] == 2

        # 验证 Milvus 插入参数
        call_args = mock_milvus.batch_insert.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]["doc_id"] == "doc-001"
        assert call_args[0]["security_group"] == ["admin", "finance"]
        assert call_args[0]["embedding"] == [0.1]*512

        # 验证 BM25 索引被调用
        engine.bm25.index_documents.assert_called_once_with(
            "doc-001", chunks, security_group=["admin", "finance"]
        )

    @patch("app.core.rag.engine.milvus_manager")
    def test_default_security_group_is_admin(self, mock_milvus):
        """未指定 security_group → 默认 ["admin"]。"""
        mock_milvus.batch_insert.return_value = {"insert_count": 1}

        engine = RAGEngine()
        engine.embedding.embed_documents = MagicMock(return_value=[[0.1]*512])
        engine.bm25.index_documents = MagicMock()

        chunks = [{"chunk_id": "c1", "content": "test", "source": "s", "page_num": 1}]
        engine.index_document("doc-002", chunks)

        call_args = mock_milvus.batch_insert.call_args[0][0]
        assert call_args[0]["security_group"] == ["admin"]

    @patch("app.core.rag.engine.milvus_manager")
    def test_empty_chunks_returns_zero_counts(self, mock_milvus):
        """空 chunks → insert_count=0。"""
        mock_milvus.batch_insert.return_value = {"insert_count": 0}

        engine = RAGEngine()
        engine.embedding.embed_documents = MagicMock(return_value=[])
        engine.bm25.index_documents = MagicMock()

        result = engine.index_document("doc-003", [])
        assert result["chunk_count"] == 0
        assert result["insert_count"] == 0
