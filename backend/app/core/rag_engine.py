"""
SmartQA Pro - RAG检索引擎
核心：嵌入模型 + Milvus向量检索 + BM25关键词检索 + Reranker精排
"""
import logging
import hashlib
from typing import Optional
from functools import lru_cache
from langchain_community.embeddings import HuggingFaceBgeEmbeddings
from app.config import get_settings
from app.core.milvus_client import milvus_manager

logger = logging.getLogger(__name__)
settings = get_settings()

# 【缓存策略】Embedding缓存使用内存LRU，不用Redis，原因：
# 1. Embedding计算是CPU密集型，缓存避免重复计算
# 2. 内存比Redis快100倍（无网络开销）
# 3. 500条缓存约占2MB内存（512维×4字节×500），可接受
# 4. 缓存key用MD5哈希，避免长文本占用过多内存
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
        if not self._model:
            self.init()

        if not documents:
            return []

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


class BM25Engine:
    """
    BM25关键词检索引擎

    使用 rank_bm25 库实现真正的 BM25 算法：
    - IDF 逆文档频率
    - 文档长度归一化
    - 饱和函数（term frequency saturation）
    """

    def __init__(self):
        self._tokenized_corpus: list[list[str]] = []
        self._chunks: list[dict] = []  # 保留原始 chunk 信息用于返回
        self._bm25: Optional["BM25Okapi"] = None
        self._doc_index_map: dict[str, int] = {}  # chunk_id -> corpus index

    def index_documents(self, doc_id: str, chunks: list[dict], security_group: list[str] | None = None):
        """
        索引文档切片

        Args:
            doc_id: 文档ID
            chunks: 切片列表 [{chunk_id, content, source, page_num}]
        """
        security_group = security_group or ["admin"]

        # 清理旧数据（如果已存在）
        self._remove_doc_by_id(doc_id)

        # 分词并构建语料库
        start_idx = len(self._tokenized_corpus)
        for i, chunk in enumerate(chunks):
            tokens = self._tokenize(chunk["content"])
            self._tokenized_corpus.append(tokens)
            # 记录 chunk_id -> corpus index 的映射
            chunk_id = chunk.get("chunk_id", f"{doc_id}_{i}")
            self._doc_index_map[chunk_id] = start_idx + i
            # 保存原始 chunk 信息
            self._chunks.append({
                **chunk,
                "chunk_id": chunk_id,
                "source": chunk.get("source", ""),
                "page_num": chunk.get("page_num", 0),
                "security_group": security_group,
            })

        # 初始化 BM25
        from rank_bm25 import BM25Okapi
        self._bm25 = BM25Okapi(self._tokenized_corpus)
        logger.info(f"BM25索引完成: doc_id={doc_id}, 切片数={len(chunks)}, 总语料={len(self._tokenized_corpus)}")

    def search(
        self,
        query: str,
        top_k: int = 20,
        allowed_roles: Optional[list[str]] = None,
        doc_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        BM25关键词检索（真正的 BM25 算法）
        """
        if not self._bm25 or not self._tokenized_corpus:
            return []

        query_tokens = self._tokenize(query)
        scores = self._bm25.get_scores(query_tokens)

        # 构造 (score, chunk) pairs
        scored = [(scores[i], self._chunks[i]) for i in range(len(self._chunks))]
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, chunk in scored:
            if doc_ids and chunk.get("doc_id") not in doc_ids:
                continue
            if allowed_roles:
                groups = set(chunk.get("security_group") or [])
                if not groups.intersection(allowed_roles):
                    continue
            results.append({
                "content": chunk["content"],
                "source": chunk.get("source", ""),
                "page_num": chunk.get("page_num", 0),
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk.get("doc_id", ""),
                "security_group": chunk.get("security_group", ["admin"]),
                "bm25_score": float(score),
                "retrieval_source": "bm25",
            })
            if len(results) >= top_k:
                break

        return results

    def _remove_doc_by_id(self, doc_id: str):
        """删除指定 doc_id 的文档索引"""
        # 找出所有属于该 doc_id 的 chunk 索引
        indices_to_remove = []
        new_chunks = []
        new_tokenized = []
        new_index_map = {}

        for i, chunk in enumerate(self._chunks):
            if chunk.get("doc_id") == doc_id:
                indices_to_remove.append(i)
            else:
                new_idx = len(new_chunks)
                new_chunks.append(chunk)
                new_tokenized.append(self._tokenized_corpus[i])
                new_index_map[chunk["chunk_id"]] = new_idx

        if not indices_to_remove:
            return

        self._chunks = new_chunks
        self._tokenized_corpus = new_tokenized
        self._doc_index_map = new_index_map

        # 重建 BM25
        if self._tokenized_corpus:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(self._tokenized_corpus)
        else:
            self._bm25 = None

        logger.info(f"BM25索引删除: doc_id={doc_id}, 删除切片数={len(indices_to_remove)}")

    def remove_doc(self, doc_id: str):
        """删除文档索引（公开接口）"""
        self._remove_doc_by_id(doc_id)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        中英文混合分词

        使用 jieba 处理中文（如果可用），否则用字符级别分词。
        英文按单词拆分。
        """
        import re

        # 提取英文单词（保留大小写，因为 BM25 对大小写敏感）
        en_words = re.findall(r"[a-zA-Z]+", text)

        # 尝试使用 jieba 处理中文
        try:
            import jieba
            cn_chars = list(jieba.cut(text))
        except ImportError:
            # 回退：按字符级别分词，每2个中文字为一个token
            cn_chars = re.findall(r"[\u4e00-\u9fff]+", text)
            # 将连续中文字符串按字符拆分并重新组合为bigram
            bigrams = []
            for chars in cn_chars:
                for i in range(0, len(chars) - 1, 2):
                    bigrams.append(chars[i:i+2])
            cn_chars = bigrams

        # 提取数字
        numbers = re.findall(r"\d+", text)

        tokens = en_words + cn_chars + numbers
        return tokens


class RAGEngine:
    """RAG检索引擎 - 混合召回 + 重排序"""

    # Query-level 缓存：相同 query 直接返回结果，省掉检索
    _query_cache: dict[str, tuple[float, dict]] = {}
    _QUERY_CACHE_TTL = 300  # 5分钟过期
    _QUERY_CACHE_MAX = 100  # 最多缓存100条

    def __init__(self):
        self.embedding = EmbeddingEngine()
        self.reranker = RerankerEngine()
        self.bm25 = BM25Engine()

    def index_document(self, doc_id: str, chunks: list[dict], security_group: list = None) -> dict:
        """
        索引文档到向量数据库和BM25

        Args:
            doc_id: 文档ID
            chunks: [{chunk_id, content, source, page_num}]
            security_group: 权限角色列表，如 ["admin", "finance"]
        """
        if security_group is None:
            security_group = ["admin"]

        # 1. 生成嵌入向量
        texts = [chunk["content"] for chunk in chunks]
        embeddings = self.embedding.embed_documents(texts)

        # 2. 插入Milvus
        records = []
        for chunk, emb in zip(chunks, embeddings):
            records.append(
                {
                    "doc_id": doc_id,
                    "chunk_id": chunk["chunk_id"],
                    "content": chunk["content"],
                    "source": chunk.get("source", ""),
                    "page_num": chunk.get("page_num", 0),
                    "section_title": chunk.get("section_title", ""),
                    "paragraph_index": chunk.get("paragraph_index", 0),
                    "embedding": emb,
                    "security_group": security_group,
                }
            )

        result = milvus_manager.batch_insert(records)

        # 3. 建立BM25索引
        self.bm25.index_documents(doc_id, chunks, security_group=security_group)

        return {
            "doc_id": doc_id,
            "chunk_count": len(chunks),
            "insert_count": result.get("insert_count", 0),
        }

    def search(
        self,
        query: str,
        top_k: int = 3,
        doc_ids: Optional[list[str]] = None,
        visibility_expr: str = "",
    ) -> dict:
        """
        混合检索：向量检索 + BM25检索 + Reranker精排

        Args:
            query: 查询文本
            top_k: 最终返回Top-K结果
            doc_ids: 限定搜索的文档ID列表（已废弃，用visibility_expr替代）
            visibility_expr: Milvus 可见性过滤表达式

        Returns:
            {
                "results": [...],       # 检索结果
                "confidence": float,    # 置信度
                "query_type": str,      # 查询类型
                "retrieval_method": str, # 检索方法（hybrid_reranked / hybrid_no_rerank）
            }
        """
        # 1. 向量检索
        import time as _t
        _t0 = _t.perf_counter()

        # Query Cache：相同 query 直接返回缓存结果
        cache_key = hashlib.md5(f"{query}_{top_k}_{doc_ids}_{visibility_expr}".encode()).hexdigest()
        if cache_key in self._query_cache:
            cached_time, cached_result = self._query_cache[cache_key]
            if _t.time() - cached_time < self._QUERY_CACHE_TTL:
                logger.info(f"[QueryCache] 命中: {query[:30]}...")
                return cached_result
            else:
                del self._query_cache[cache_key]

        query_embedding = self.embedding.embed_query(query)
        _t_embed = _t.perf_counter() - _t0

        # 向量检索（gRPC 超时时自动重试，最多2次，间隔1s）
        vector_results = None
        from app.core.retry import _is_retriable
        for retry_i in range(3):
            try:
                vector_results = milvus_manager.search(
                    query_embedding=query_embedding,
                    top_k=settings.VECTOR_TOP_K,
                    expr=visibility_expr if visibility_expr else None,
                )
                break
            except Exception as e:
                if not _is_retriable(e) or retry_i == 2:
                    logger.warning(f"[Retry] Milvus向量检索失败(尝试{retry_i+1}/3): {type(e).__name__}: {e}")
                    vector_results = []  # 降级：返回空结果，不抛异常
                    break
                logger.warning(f"[Retry] Milvus向量检索失败，1s后重试(尝试{retry_i+1}/3): {type(e).__name__}")
                time.sleep(1)
        _t_vec = _t.perf_counter() - _t0 - _t_embed

        # 2. BM25检索
        _t1 = _t.perf_counter()
        bm25_allowed_roles = self._roles_from_visibility_expr(visibility_expr)
        bm25_results = self.bm25.search(
            query,
            top_k=settings.BM25_TOP_K,
            allowed_roles=bm25_allowed_roles,
            doc_ids=doc_ids,
        )
        _t_bm25 = _t.perf_counter() - _t1

        # 3. 合并去重
        merged = self._merge_results(vector_results, bm25_results)

        # 只取top N给reranker（CPU上cross-encoder推理慢，减少输入量）
        merged_for_rerank = merged[:settings.RERANK_TOP_K * 2]  # 取rerank_top_k的2倍

        if not merged:
            return {
                "results": [],
                "confidence": 0.0,
                "query_type": "no_results",
                "retrieval_method": "none",
            }

        # 4. Reranker精排（带降级检测）
        # 【降级策略】rerank()内部会尝试初始化模型，如果失败则按原始分数排序
        _t2 = _t.perf_counter()
        if settings.RERANKER_ENABLED or self.reranker._model is not None:
            reranked = self.reranker.rerank(query, merged_for_rerank, top_k=top_k)
        else:
            for doc in merged_for_rerank:
                doc["rerank_score"] = doc.get("score", 0.0)
            reranked = sorted(merged_for_rerank, key=lambda x: x["rerank_score"], reverse=True)[:top_k]
        _t_rerank = _t.perf_counter() - _t2
        logger.info(f"[RAG检索] embed={_t_embed*1000:.0f}ms vector={_t_vec*1000:.0f}ms bm25={_t_bm25*1000:.0f}ms rerank={_t_rerank*1000:.0f}ms total={(_t_embed+_t_vec+_t_bm25+_t_rerank)*1000:.0f}ms")
        # 检测reranker是否真正参与了精排：如果_model为None则走了降级路径
        retrieval_method = "hybrid_reranked" if self.reranker._model is not None else "hybrid_no_rerank"

        # 5. 计算置信度
        confidence = self._calculate_confidence(reranked)

        result = {
            "results": reranked,
            "confidence": confidence,
            "query_type": "rag_answer",
            "retrieval_method": retrieval_method,
        }

        # 存入 Query Cache
        if len(self._query_cache) >= self._QUERY_CACHE_MAX:
            # 淘汰最旧的
            oldest_key = min(self._query_cache, key=lambda k: self._query_cache[k][0])
            del self._query_cache[oldest_key]
        self._query_cache[cache_key] = (_t.time(), result)

        return result

    @staticmethod
    def _merge_results(
        vector_results: list[dict], bm25_results: list[dict]
    ) -> list[dict]:
        """RRF（Reciprocal Rank Fusion）融合排序

        score(d) = Σ 1/(k + rank_i(d))
        其中 k=60（常用常数），rank_i 是文档在第i路检索中的排名

        优点：不需要归一化不同检索器的分数，直接用排名融合
        """
        RRF_K = 60  # RRF常数，论文推荐值

        # 构建 chunk_id -> 排名 的映射
        vector_ranks = {}
        for rank, item in enumerate(vector_results):
            cid = item.get("chunk_id", "")
            if cid not in vector_ranks:
                vector_ranks[cid] = rank

        bm25_ranks = {}
        for rank, item in enumerate(bm25_results):
            cid = item.get("chunk_id", "")
            if cid not in bm25_ranks:
                bm25_ranks[cid] = rank

        # 收集所有唯一文档
        all_docs = {}
        for item in vector_results:
            cid = item.get("chunk_id", "")
            if cid not in all_docs:
                all_docs[cid] = item.copy()
        for item in bm25_results:
            cid = item.get("chunk_id", "")
            if cid not in all_docs:
                all_docs[cid] = item.copy()

        # 计算 RRF 分数
        seen = set()
        merged = []
        for cid, doc in all_docs.items():
            rrf_score = 0.0
            if cid in vector_ranks:
                rrf_score += 1.0 / (RRF_K + vector_ranks[cid])
                doc["retrieval_source"] = "vector"
            if cid in bm25_ranks:
                rrf_score += 1.0 / (RRF_K + bm25_ranks[cid])
                doc["retrieval_source"] = doc.get("retrieval_source", "") + "+bm25"
            doc["rrf_score"] = rrf_score
            merged.append(doc)

        # 按 RRF 分数降序排列
        merged.sort(key=lambda x: x["rrf_score"], reverse=True)

        return merged

    @staticmethod
    def _roles_from_visibility_expr(visibility_expr: str) -> Optional[list[str]]:
        """Extract role filters from the Milvus visibility expression for BM25 filtering."""
        if not visibility_expr:
            return None

        import re
        roles = re.findall(r'array_contains\(security_group,\s*"([^"]+)"\)', visibility_expr)
        return roles or None


    @staticmethod
    def _calculate_confidence(results: list[dict]) -> float:
        """计算检索置信度"""
        if not results:
            return 0.0

        # 基于Top1的rerank_score
        top_score = results[0].get("rerank_score", 0)

        # 映射到0-1范围（rerank_score可能为负值）
        # 使用sigmoid映射
        import math
        confidence = 1 / (1 + math.exp(-top_score))

        return round(confidence, 4)


# 全局单例
rag_engine = RAGEngine()
