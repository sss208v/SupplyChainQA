"""
SmartQA Pro - RAG 评估引擎
============================================================
【学习要点】RAG 系统评估指标体系

1. 检索（Retrieval）阶段指标：
   - Recall@K: 检索结果中包含的相关文档占比
   - Precision@K: 检索结果中相关文档的比例
   - MRR (Mean Reciprocal Rank): 第一个相关文档排名的倒数均值
   - NDCG@K (Normalized Discounted Cumulative Gain): 考虑排名的质量指标
   - MAP (Mean Average Precision): 平均精度的均值

2. 生成（Generation）阶段指标：
   - ROUGE-L: 生成文本与参考文本的最长公共子序列
   - BLEU: n-gram 精确率（机器翻译常用）
   - 人工评估（最可靠但成本高）

3. 端到端评估：
   - 答案正确性（Answer Correctness）: 生成答案 vs 参考答案
   - 答案相关性（Answer Relevance）: 生成答案与问题的相关程度
   - 幻觉率（Hallucination Rate）: 生成内容中与检索内容不符的比例

4. 本模块实现：
   - 检索评估: Recall@K, Precision@K, MRR@K, NDCG@K
   - 生成评估: 需要 LLM 作为裁判（LLM-as-Judge）
============================================================
"""
import logging
from typing import Optional
from dataclasses import dataclass, field
from app.core.rag_engine import rag_engine

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """单次评估结果"""
    query: str
    retrieved_chunks: list[dict] = field(default_factory=list)
    relevant_chunks: list[str] = field(default_factory=list)  # 相关chunk_id列表

    # 检索指标
    recall_at_1: float = 0.0
    recall_at_3: float = 0.0
    recall_at_5: float = 0.0
    precision_at_1: float = 0.0
    precision_at_3: float = 0.0
    precision_at_5: float = 0.0
    mrr_at_k: float = 0.0
    ndcg_at_1: float = 0.0
    ndcg_at_3: float = 0.0
    ndcg_at_5: float = 0.0
    map_at_k: float = 0.0

    # 综合检索得分
    retrieval_score: float = 0.0  # 综合指标 = 0.4*Recall + 0.3*NDCG + 0.3*MRR

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "retrieved_count": len(self.retrieved_chunks),
            "relevant_count": len(self.relevant_chunks),
            "recall_at_1": round(self.recall_at_1, 4),
            "recall_at_3": round(self.recall_at_3, 4),
            "recall_at_5": round(self.recall_at_5, 4),
            "precision_at_1": round(self.precision_at_1, 4),
            "precision_at_3": round(self.precision_at_3, 4),
            "precision_at_5": round(self.precision_at_5, 4),
            "mrr_at_k": round(self.mrr_at_k, 4),
            "ndcg_at_1": round(self.ndcg_at_1, 4),
            "ndcg_at_3": round(self.ndcg_at_3, 4),
            "ndcg_at_5": round(self.ndcg_at_5, 4),
            "map_at_k": round(self.map_at_k, 4),
            "retrieval_score": round(self.retrieval_score, 4),
        }


class RAGEvaluator:
    """
    RAG 系统检索评估器

    支持的指标：
    - Recall@K: 相关文档被召回的比例
    - Precision@K: 检索结果中相关文档的占比
    - MRR@K: Mean Reciprocal Rank，第一个相关文档排名的倒数均值
    - NDCG@K: 考虑排名位置的质量指标
    - MAP@K: 平均精度的均值
    """

    def __init__(self):
        self.history: list[EvaluationResult] = []

    def evaluate_retrieval(
        self,
        query: str,
        retrieved_chunk_ids: list[str],
        relevant_chunk_ids: list[str],
        k_values: list[int] = None,
    ) -> EvaluationResult:
        """
        评估检索质量

        Args:
            query: 查询文本
            retrieved_chunk_ids: 检索返回的chunk_id列表（按排名顺序）
            relevant_chunk_ids: 实际相关的chunk_id列表（ground truth）
            k_values: 评估的K值列表，默认 [1, 3, 5]

        Returns:
            EvaluationResult: 包含各项指标的评估结果
        """
        if k_values is None:
            k_values = [1, 3, 5]

        result = EvaluationResult(
            query=query,
            retrieved_chunks=retrieved_chunk_ids,
            relevant_chunks=relevant_chunk_ids,
        )

        relevant_set = set(relevant_chunk_ids)
        retrieved = retrieved_chunk_ids

        # ---- Recall@K ----
        # Recall = |relevant_retrieved| / |relevant|
        for k in k_values:
            retrieved_at_k = set(retrieved[:k])
            if len(relevant_set) > 0:
                recall = len(retrieved_at_k & relevant_set) / len(relevant_set)
            else:
                recall = 0.0

            if k == 1:
                result.recall_at_1 = recall
            elif k == 3:
                result.recall_at_3 = recall
            elif k == 5:
                result.recall_at_5 = recall

        # ---- Precision@K ----
        # Precision = |relevant_retrieved| / |retrieved_at_k|
        for k in k_values:
            retrieved_at_k = retrieved[:k]
            if len(retrieved_at_k) > 0:
                precision = len(set(retrieved_at_k) & relevant_set) / len(retrieved_at_k)
            else:
                precision = 0.0

            if k == 1:
                result.precision_at_1 = precision
            elif k == 3:
                result.precision_at_3 = precision
            elif k == 5:
                result.precision_at_5 = precision

        # ---- MRR@K (Mean Reciprocal Rank) ----
        # MRR = 1/|Q| * Σ(1/rank_i)，rank_i是第一个相关文档的排名
        mrr = 0.0
        for i, chunk_id in enumerate(retrieved):
            if chunk_id in relevant_set:
                mrr = 1.0 / (i + 1)
                break
        result.mrr_at_k = mrr

        # ---- NDCG@K (Normalized Discounted Cumulative Gain) ----
        for k in k_values:
            dcg = 0.0
            for i, chunk_id in enumerate(retrieved[:k]):
                # relevance = 1 if relevant else 0（简化版）
                rel = 1.0 if chunk_id in relevant_set else 0.0
                dcg += rel / (i + 1)  # i从0开始，所以i+1就是排名

            # IDCG = 最理想情况下DCG（相关文档都在最前面）
            idcg = 0.0
            for i in range(min(k, len(relevant_set))):
                idcg += 1.0 / (i + 1)

            ndcg = dcg / idcg if idcg > 0 else 0.0

            if k == 1:
                result.ndcg_at_1 = ndcg
            elif k == 3:
                result.ndcg_at_3 = ndcg
            elif k == 5:
                result.ndcg_at_5 = ndcg

        # ---- MAP@K (Mean Average Precision) ----
        # AP = Σ(P@k * rel_k) / |relevant|，只考虑相关文档
        ap = 0.0
        relevant_seen = 0
        for i, chunk_id in enumerate(retrieved):
            if chunk_id in relevant_set:
                relevant_seen += 1
                precision_at_i = relevant_seen / (i + 1)
                ap += precision_at_i

        if len(relevant_set) > 0:
            result.map_at_k = ap / len(relevant_set)
        else:
            result.map_at_k = 0.0

        # ---- 综合检索得分 ----
        # retrieval_score = 0.4*Recall@5 + 0.3*NDCG@5 + 0.3*MRR
        result.retrieval_score = (
            0.4 * result.recall_at_5
            + 0.3 * result.ndcg_at_5
            + 0.3 * result.mrr_at_k
        )

        self.history.append(result)
        return result

    def evaluate_online(self, query: str, top_k: int = 5) -> dict:
        """
        在线评估：对实际查询进行检索并评估
        （适用于无ground truth时的快速评估）

        评估维度：
        1. 检索覆盖率：检索结果的rerank_score分布
        2. 结果多样性：检索来源（vector vs bm25）的分布
        3. 置信度合理性：confidence与实际检索质量的匹配度
        """
        search_result = rag_engine.search(query, top_k=top_k)

        retrieved_chunks = search_result.get("results", [])
        retrieved_ids = [c.get("chunk_id", "") for c in retrieved_chunks]

        # 基于检索结果本身的质量评估
        if not retrieved_chunks:
            return {
                "query": query,
                "retrieved_count": 0,
                "avg_rerank_score": 0.0,
                "avg_confidence": 0.0,
                "vector_ratio": 0.0,
                "bm25_ratio": 0.0,
                "score_distribution": {},
                "quality_label": "no_results",
            }

        # 统计rerank_score分布
        scores = [c.get("rerank_score", 0) for c in retrieved_chunks]
        avg_score = sum(scores) / len(scores)

        # 统计检索来源
        vector_count = sum(1 for c in retrieved_chunks if c.get("retrieval_source") == "vector")
        bm25_count = sum(1 for c in retrieved_chunks if c.get("retrieval_source") == "bm25")
        vector_ratio = vector_count / len(retrieved_chunks)
        bm25_ratio = bm25_count / len(retrieved_chunks)

        # 质量标签
        if avg_score >= 0.8:
            label = "excellent"
        elif avg_score >= 0.6:
            label = "good"
        elif avg_score >= 0.4:
            label = "fair"
        elif avg_score > 0:
            label = "poor"
        else:
            label = "no_signal"

        return {
            "query": query,
            "retrieved_count": len(retrieved_chunks),
            "avg_rerank_score": round(avg_score, 4),
            "max_rerank_score": round(max(scores), 4),
            "min_rerank_score": round(min(scores), 4),
            "avg_confidence": search_result.get("confidence", 0.0),
            "vector_ratio": round(vector_ratio, 4),
            "bm25_ratio": round(bm25_ratio, 4),
            "retrieved_ids": retrieved_ids,
            "quality_label": label,
        }

    def get_summary(self) -> dict:
        """获取评估历史汇总"""
        if not self.history:
            return {
                "total_queries": 0,
                "message": "暂无评估数据，请先调用 evaluate_retrieval()"
            }

        total = len(self.history)
        avg_recall5 = sum(r.recall_at_5 for r in self.history) / total
        avg_ndcg5 = sum(r.ndcg_at_5 for r in self.history) / total
        avg_mrr = sum(r.mrr_at_k for r in self.history) / total
        avg_map = sum(r.map_at_k for r in self.history) / total
        avg_score = sum(r.retrieval_score for r in self.history) / total

        # 分位数
        retrieval_scores = sorted([r.retrieval_score for r in self.history])
        p50 = retrieval_scores[total // 2]
        p90 = retrieval_scores[int(total * 0.9)] if total >= 10 else retrieval_scores[-1]

        return {
            "total_queries": total,
            "avg_recall_at_5": round(avg_recall5, 4),
            "avg_ndcg_at_5": round(avg_ndcg5, 4),
            "avg_mrr": round(avg_mrr, 4),
            "avg_map": round(avg_map, 4),
            "avg_retrieval_score": round(avg_score, 4),
            "retrieval_score_p50": round(p50, 4),
            "retrieval_score_p90": round(p90, 4),
            "queries_evaluated": [r.query for r in self.history],
        }


# 全局评估器单例
rag_evaluator = RAGEvaluator()
