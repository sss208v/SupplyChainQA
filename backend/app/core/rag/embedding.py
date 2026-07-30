"""
SupplyChainRAG - 文本嵌入引擎 (BGE-M3)

提供 EmbeddingEngine 类，负责将查询文本和文档文本转换为稠密向量表示。
使用 HuggingFace BGE 系列模型，内置 LRU 内存缓存以减少重复计算。

缓存策略：
- Embedding 计算是 CPU 密集型，缓存避免重复计算
- 内存比 Redis 快 100 倍（无网络开销）
- 500 条缓存约占 2MB 内存（512 维 × 4 字节 × 500），可接受
- 缓存 key 用 MD5 哈希，避免长文本占用过多内存
"""
import logging
import hashlib
from typing import Optional

from langchain_community.embeddings import HuggingFaceBgeEmbeddings

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_embedding_cache: dict[str, list[float]] = {}
_EMBEDDING_CACHE_MAX = 500


class EmbeddingEngine:
    """文本嵌入引擎 (BGE-M3)"""

    def __init__(self):
        self._model: Optional[HuggingFaceBgeEmbeddings] = None

    def init(self):
        """初始化嵌入模型（带容错：加载失败不抛异常，由调用方决定降级策略）"""
        if self._model is not None:
            return

        try:
            import warnings
            logger.info(f"正在加载嵌入模型: {settings.EMBEDDING_MODEL}")
            # 抑制 langchain_community 中 HuggingFaceBgeEmbeddings 的 deprecation warning
            # 该 warning 建议迁移到 langchain_huggingface，但该包需要额外安装；
            # 功能完全正常，等下一次依赖升级时统一迁移
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=Warning,
                    message=".*HuggingFaceBgeEmbeddings.*",
                )
                self._model = HuggingFaceBgeEmbeddings(
                    model_name=settings.EMBEDDING_MODEL,
                    model_kwargs={"device": settings.EMBEDDING_DEVICE},
                    encode_kwargs={"normalize_embeddings": True},
                )
            logger.info("嵌入模型加载完成")
        except Exception as e:
            # 【降级策略】嵌入模型加载失败时，设置为None，后续调用会抛出明确的RuntimeError
            self._model = None
            logger.warning(f"嵌入模型加载失败，向量检索将不可用: {e}")

    def embed_query(self, text: str) -> list[float]:
        """嵌入查询文本（带内存LRU缓存）"""
        if not self._model:
            self.init()
        if self._model is None:
            raise RuntimeError("嵌入模型不可用，无法执行向量检索。请检查模型配置或网络连接。")

        # Embedding 向量是文本语义的数学表征，不承载权限信息。
        # 真正的权限边界在检索层（visibility_expr 过滤）和结果缓存层（query_cache 含 visibility_expr）。
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in _embedding_cache:
            logger.debug(f"Embedding cache hit: {text[:30]}...")
            return _embedding_cache[cache_key]

        # 缓存未命中，计算嵌入
        logger.debug(f"Embedding cache miss: {text[:30]}...")
        result = self._model.embed_query(text)

        # 存入缓存（LRU淘汰：超过上限时移除最旧条目）
        if len(_embedding_cache) >= _EMBEDDING_CACHE_MAX:
            _embedding_cache.pop(next(iter(_embedding_cache)))
        _embedding_cache[cache_key] = result

        return result

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """嵌入文档文本列表（带单条文档缓存）"""
        if not self._model:
            self.init()
        if self._model is None:
            raise RuntimeError("嵌入模型不可用，无法生成文档向量。请检查模型配置或网络连接。")

        # 逐条检查缓存，分离已缓存/未缓存
        results: list[list[float]] = [None] * len(texts)  # type: ignore
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cache_key = hashlib.md5(text.encode()).hexdigest()
            if cache_key in _embedding_cache:
                logger.debug(f"Embedding cache hit (doc): {text[:30]}...")
                results[i] = _embedding_cache[cache_key]
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # 批量计算未缓存的文档嵌入
        if uncached_texts:
            logger.debug(f"Embedding cache miss (doc): {len(uncached_texts)} texts to embed")
            new_embeddings = self._model.embed_documents(uncached_texts)
            for idx, text, emb in zip(uncached_indices, uncached_texts, new_embeddings):
                results[idx] = emb
                # 存入缓存
                cache_key = hashlib.md5(text.encode()).hexdigest()
                if len(_embedding_cache) >= _EMBEDDING_CACHE_MAX:
                    _embedding_cache.pop(next(iter(_embedding_cache)))
                _embedding_cache[cache_key] = emb

        return results

    def clear_cache(self):
        """清空Embedding缓存"""
        global _embedding_cache
        _embedding_cache.clear()
        logger.info("Embedding缓存已清空")
