# -*- coding: utf-8 -*-
"""
Agentic RAG Components - Unit Tests
====================================
Tests for CriticEvaluator, QueryRewriter, and upgrade_strategy.

参考论文: Singh et al. "Agentic Retrieval-Augmented Generation" (arXiv:2501.09136)
"""
import pytest
from app.core.rag_engine import CriticEvaluator, QueryRewriter
from app.core.query_analyzer import query_analyzer


class TestCriticEvaluator:
    """CriticEvaluator 检索质量评估测试"""

    def test_extract_keywords_chinese(self):
        """中文关键词提取"""
        keywords = CriticEvaluator.extract_keywords("安全库存标准是多少")
        # regex extracts 2-8 char sequences, so the full phrase is one keyword
        assert len(keywords) >= 1
        assert any("安全库存" in kw for kw in keywords)

    def test_extract_keywords_english(self):
        """英文关键词提取"""
        keywords = CriticEvaluator.extract_keywords("MAT-001 inventory status")
        assert "mat-001" in keywords or "inventory" in keywords

    def test_extract_keywords_mixed(self):
        """中英混合关键词提取"""
        keywords = CriticEvaluator.extract_keywords("MAT-001 安全库存标准")
        assert len(keywords) >= 3  # mat-001, 安全库存, 标准

    def test_extract_keywords_empty(self):
        """空文本"""
        keywords = CriticEvaluator.extract_keywords("")
        assert len(keywords) == 0

    def test_evaluate_high_quality(self):
        """高质量检索结果评估"""
        query = "MAT-001 安全库存"
        results = [
            {"content": "MAT-001 安全库存为100件，标准系数1.5", "rerank_score": 0.8, "chunk_id": "c1"},
            {"content": "MAT-001 月均消耗50件", "rerank_score": 0.6, "chunk_id": "c2"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        assert eval_result["quality"] in ["high", "medium"]
        assert eval_result["keyword_coverage"] >= 0.5
        assert eval_result["top_score"] == 0.8

    def test_evaluate_low_quality(self):
        """低质量检索结果评估"""
        query = "MAT-001 安全库存标准"
        results = [
            {"content": "供应商准入流程需要ISO认证", "rerank_score": 0.1, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        assert eval_result["quality"] == "low"
        assert eval_result["needs_retry"] is True
        assert eval_result["suggestion"] in ["rewrite_query", "expand_search"]

    def test_evaluate_empty_results(self):
        """空检索结果评估"""
        eval_result = CriticEvaluator.evaluate("任何查询", [])
        assert eval_result["quality"] == "low"
        assert eval_result["needs_retry"] is True
        assert eval_result["result_count"] == 0

    def test_evaluate_medium_quality(self):
        """中等质量检索结果评估"""
        query = "安全库存计算"
        results = [
            {"content": "安全库存等于日均消耗乘以采购周期", "rerank_score": 0.3, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate(query, results)
        # "安全库存" should be in both query and result keywords
        assert eval_result["quality"] in ["medium", "high"]
        assert eval_result["keyword_coverage"] > 0.0


class TestQueryRewriter:
    """QueryRewriter 查询改写测试"""

    def test_expand_search(self):
        """expand_search 策略：移除疑问词"""
        rewritten = QueryRewriter.rewrite_for_retry(
            "MAT-001 的安全库存是多少",
            [], "expand_search"
        )
        assert "是多少" not in rewritten
        assert "MAT-001" in rewritten

    def test_expand_search_preserves_entity(self):
        """expand_search 保留实体编码"""
        rewritten = QueryRewriter.rewrite_for_retry(
            "PO-20250601 怎么查询状态",
            [], "expand_search"
        )
        assert "PO-20250601" in rewritten

    def test_rewrite_query_adds_keywords(self):
        """rewrite_query 策略：从结果中补充关键词"""
        results = [{"content": "安全库存公式 日均消耗 采购周期 系数1.5", "rerank_score": 0.5}]
        rewritten = QueryRewriter.rewrite_for_retry(
            "MAT-001 库存",
            results, "rewrite_query"
        )
        # 应该补充了结果中的关键词
        assert len(rewritten) >= len("MAT-001 库存")

    def test_rewrite_returns_original_on_unknown(self):
        """未知策略返回原始查询"""
        original = "测试查询"
        rewritten = QueryRewriter.rewrite_for_retry(original, [], "unknown")
        assert rewritten == original


class TestAdaptiveStrategyUpgrade:
    """Adaptive RAG 策略升级测试"""

    def test_light_to_standard(self):
        """light + medium 质量 -> 标准策略"""
        result = query_analyzer.upgrade_strategy("light", "medium")
        assert result == "standard"

    def test_light_to_full(self):
        """light + low 质量 -> 完整策略"""
        result = query_analyzer.upgrade_strategy("light", "low")
        assert result == "full"

    def test_standard_to_full(self):
        """standard + low 质量 -> 完整策略"""
        result = query_analyzer.upgrade_strategy("standard", "low")
        assert result == "full"

    def test_full_no_upgrade(self):
        """full 已是最高级，无法升级"""
        result = query_analyzer.upgrade_strategy("full", "low")
        assert result == "full"

    def test_high_quality_no_upgrade(self):
        """高质量不触发升级"""
        result = query_analyzer.upgrade_strategy("light", "high")
        assert result == "light"

    def test_standard_high_no_upgrade(self):
        """standard + high 不触发升级"""
        result = query_analyzer.upgrade_strategy("standard", "high")
        assert result == "standard"


class TestCriticEvaluatorEdgeCases:
    """CriticEvaluator 边界情况测试"""

    def test_results_with_zero_scores(self):
        """所有结果分数为0"""
        results = [
            {"content": "无关内容", "rerank_score": 0.0, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate("测试查询", results)
        assert eval_result["quality"] in ["low", "medium"]

    def test_results_with_negative_scores(self):
        """负分数结果（Reranker 可能产生负分）"""
        results = [
            {"content": "MAT-001 库存", "rerank_score": -0.5, "chunk_id": "c1"},
        ]
        eval_result = CriticEvaluator.evaluate("MAT-001", results)
        assert eval_result["top_score"] == -0.5

    def test_many_results(self):
        """大量检索结果"""
        results = [
            {"content": f"文档{i} 关于安全库存的内容", "rerank_score": 0.5 - i * 0.01, "chunk_id": f"c{i}"}
            for i in range(20)
        ]
        eval_result = CriticEvaluator.evaluate("安全库存", results)
        assert eval_result["result_count"] > 0
