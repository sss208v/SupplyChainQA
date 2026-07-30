"""
SupplyChainRAG - 重排序引擎 (BGE-Reranker)

提供 RerankerEngine 类，使用 sentence_transformers 的 CrossEncoder 模型
对混合检索结果进行精排序（reranking），提升最终排序质量。

当模型不可用时自动降级为按原始检索分数排序返回。
"""
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RerankerEngine:
    """重排序引擎 (BGE-Reranker via sentence_transformers.CrossEncoder)"""

    def __init__(self):
        self._model = None  # Optional[CrossEncoder]

    def init(self):
        """初始化重排序模型（带容错：加载失败不抛异常，搜索时降级为纯混合召回）"""
        if self._model is not None:
            return

        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"正在加载重排序模型: {settings.RERANKER_MODEL}")
            self._model = CrossEncoder(
                settings.RERANKER_MODEL,
                device=settings.RERANKER_DEVICE,
            )
            logger.info("重排序模型加载完成")
        except Exception as e:
            self._model = None
            logger.warning(f"重排序模型加载失败，将降级为无精排的混合检索: {e}")

    def rerank(
        self, query: str, documents: list[dict], top_k: int = 3
    ) -> list[dict]:
        """
        对检索结果进行重排序

        Args:
            query: 查询文本
            documents: 检索结果列表，每项需包含content字段
            top_k: 返回Top-K结果

        Returns:
            重排序后的结果列表，包含rerank_score字段
        """
        if not documents:
            return []

        if not self._model:
            self.init()

        # 【降级策略】如果重排序模型不可用，按原始检索分数排序返回
        if self._model is None:
            logger.warning("重排序模型不可用，按原始检索分数排序返回结果")
            def _get_sort_score(doc):
                """优先使用bm25_score，其次vector_score，都没有则为0"""
                return doc.get("bm25_score") or doc.get("vector_score") or 0.0
            fallback_results = sorted(documents, key=_get_sort_score, reverse=True)[:top_k]
            # 标记降级分数，便于上层判断
            for doc in fallback_results:
                doc["rerank_score"] = _get_sort_score(doc)
            return fallback_results

        # 构造query-doc对
        pairs = [(query, doc["content"]) for doc in documents]

        # 计算重排序分数
        scores = self._model.predict(pairs)

        # 合并分数并排序
        for doc, score in zip(documents, scores):
            doc["rerank_score"] = float(score)

        # 按rerank_score降序排列
        sorted_docs = sorted(documents, key=lambda x: x["rerank_score"], reverse=True)

        return sorted_docs[:top_k]
