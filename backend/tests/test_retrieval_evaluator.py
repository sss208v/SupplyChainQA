"""检索评估器单元测试 — 之前 0 覆盖"""
import pytest


class TestEvaluationResult:
    def test_init_defaults(self):
        """EvaluationResult 默认值"""
        from app.core.retrieval_evaluator import EvaluationResult
        r = EvaluationResult(query="Q1", retrieved_chunks=[], relevant_chunks=[])
        assert r.query == "Q1"
        # 默认分数为 0
        assert r.recall_at_1 == 0.0
        assert r.recall_at_3 == 0.0
        assert r.recall_at_5 == 0.0
        assert r.precision_at_1 == 0.0
        assert r.mrr_at_k == 0.0
        assert r.ndcg_at_1 == 0.0
        assert r.map_at_k == 0.0
        assert r.retrieval_score == 0.0

    def test_to_dict(self):
        """to_dict 应返回所有指标字段"""
        from app.core.retrieval_evaluator import EvaluationResult
        r = EvaluationResult(query="Q1", retrieved_chunks=["c1"], relevant_chunks=["c1"])
        d = r.to_dict()
        assert d["query"] == "Q1"
        assert d["retrieved_count"] == 1
        assert d["relevant_count"] == 1
        assert "recall_at_1" in d
        assert "mrr_at_k" in d
        assert "retrieval_score" in d


class TestRetrievalEvaluator:
    def test_init(self):
        """RetrievalEvaluator 初始化应创建空 history"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        assert e.history == []

    def test_evaluate_retrieval_perfect_match(self):
        """完美匹配：retrieved 包含所有 relevant，precision=1.0 且 MRR=1.0"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "c2", "c3"],
            relevant_chunk_ids=["c1"],
        )
        # 1 个 relevant 在 top-1 → recall@1 = 1/1 = 1.0
        assert result.recall_at_1 == 1.0
        assert result.recall_at_3 == 1.0
        # top-1 命中 1/1，precision@1 = 1.0
        assert result.precision_at_1 == 1.0
        # top-3 命中 1/3，precision@3 = 1/3
        assert result.precision_at_3 == 1.0 / 3
        assert result.mrr_at_k == 1.0

    def test_evaluate_retrieval_no_relevant(self):
        """relevant_chunks 为空 → recall 全 0，但 precision 可能非 0"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "c2"],
            relevant_chunk_ids=[],
        )
        # 避免除零
        assert result.recall_at_1 == 0.0
        assert result.recall_at_3 == 0.0
        # precision 不涉及 relevant
        # precision = 0/2 = 0 (无相关命中)
        assert result.precision_at_3 == 0.0
        assert result.mrr_at_k == 0.0  # 找不到相关

    def test_evaluate_retrieval_no_retrieved(self):
        """retrieved 为空 → precision 0，recall 0"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=[],
            relevant_chunk_ids=["c1", "c2"],
        )
        assert result.recall_at_1 == 0.0
        assert result.precision_at_1 == 0.0
        assert result.mrr_at_k == 0.0

    def test_evaluate_retrieval_partial_match(self):
        """部分匹配：1/3 命中"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "x", "y"],  # 1/3 命中
            relevant_chunk_ids=["c1", "c2", "c3"],
        )
        # Recall@1 = 1/3 (top-1 是 c1，relevant 3 个)
        assert result.recall_at_1 == 1.0 / 3
        # Recall@3 = 1/3
        assert result.recall_at_3 == 1.0 / 3
        # Precision@1 = 1/1
        assert result.precision_at_1 == 1.0
        # Precision@3 = 1/3
        assert result.precision_at_3 == 1.0 / 3
        # MRR = 1/1 = 1.0
        assert result.mrr_at_k == 1.0

    def test_evaluate_retrieval_mrr_second_position(self):
        """MRR：第一个不在 relevant，第二个在 → 1/2"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["x", "c1", "y"],
            relevant_chunk_ids=["c1"],
        )
        assert result.mrr_at_k == 0.5

    def test_evaluate_retrieval_retrieval_score_composite(self):
        """综合得分 = 0.4*Recall@5 + 0.3*NDCG@5 + 0.3*MRR"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "c2"],
            relevant_chunk_ids=["c1", "c2"],
        )
        expected = 0.4 * result.recall_at_5 + 0.3 * result.ndcg_at_5 + 0.3 * result.mrr_at_k
        assert result.retrieval_score == round(expected, 4)

    def test_evaluate_retrieval_custom_k_values(self):
        """自定义 k_values（EvaluationResult 只有 recall_at_1/3/5 字段）"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "c2", "c3", "c4", "c5"],
            relevant_chunk_ids=["c1", "c2"],
            k_values=[3, 5],
        )
        # 3 个 relevant 中 2 个在 top-3 → recall@3 = 2/2 = 1.0 (relevant 只有 2 个)
        assert result.recall_at_3 == 1.0
        # top-5 也都命中
        assert result.recall_at_5 == 1.0
        # k=1 未在 k_values 列表中 → 保持默认值 0.0
        assert result.recall_at_1 == 0.0

    def test_evaluate_retrieval_appends_history(self):
        """每次评估应追加到 history"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        e.evaluate_retrieval("Q1", ["c1"], ["c1"])
        e.evaluate_retrieval("Q2", ["c2"], ["c2"])
        assert len(e.history) == 2

    def test_evaluate_retrieval_ndcg_perfect(self):
        """NDCG 完美排序 = 1.0"""
        from app.core.retrieval_evaluator import RetrievalEvaluator
        e = RetrievalEvaluator()
        result = e.evaluate_retrieval(
            query="Q1",
            retrieved_chunk_ids=["c1", "c2", "c3"],
            relevant_chunk_ids=["c1", "c2", "c3"],
        )
        assert result.ndcg_at_1 == 1.0
        assert result.ndcg_at_5 == 1.0
