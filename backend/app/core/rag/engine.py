"""
SupplyChainRAG - RAG 检索引擎（主控模块）

提供 RAGEngine 类，协调 EmbeddingEngine、RerankerEngine、BM25Engine、CriticEvaluator
实现混合召回 + 重排序的完整 RAG 流水线：

1. 向量检索（Milvus + BGE 嵌入）
2. BM25 关键词检索
3. Graph RAG（Neo4j 实体图谱，可选）
4. RRF 融合排序
5. Reranker 精排
6. Critic 评估（CRAG）
"""
import logging
import hashlib
import concurrent.futures
from typing import Optional

from app.config import get_settings
from app.core.milvus_client import milvus_manager
from app.core.utils import sigmoid_normalize

from app.core.rag.embedding import EmbeddingEngine
from app.core.rag.reranker import RerankerEngine
from app.core.rag.bm25 import BM25Engine
from app.core.rag.critic import CriticEvaluator

logger = logging.getLogger(__name__)
settings = get_settings()

# ---- 多层缓存门面（L1 内存 LRU + L2 语义缓存统一入口）----
_cache_manager = None

def _get_cache_manager():
    """懒加载缓存门面单例（延迟导入避免循环依赖）"""
    global _cache_manager
    if _cache_manager is None:
        from app.core.cache_manager import cache_manager
        _cache_manager = cache_manager
    return _cache_manager


# 模块级共享线程池：同步上下文桥接 async 缓存调用时复用，
# 避免每次请求新建/销毁 ThreadPoolExecutor 的开销
_bridge_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="rag-cache-bridge"
)


def _run_async(coro):
    """在同步上下文中执行 async 协程（共享线程池，仅作 sync→async 兑底桥接）"""
    try:
        import asyncio as _asyncio
        try:
            _asyncio.get_running_loop()  # 检测是否有运行中的事件循环
            # 已有事件循环 → 共享线程池执行
            return _bridge_pool.submit(_asyncio.run, coro).result(timeout=5)
        except RuntimeError:
            # 无事件循环 → 直接 run
            return _asyncio.run(coro)
    except Exception as e:
        logger.debug(f"[SemanticCache] async 执行失败，优雅降级: {e}")
        return None


class RAGEngine:
    """RAG检索引擎 - 混合召回 + 重排序

    Query-level 缓存（L1）已迁移至 cache_manager 统一门面（进程内 LRU+TTL），
    本类不再持有类属性级共享状态。
    """

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

        # 0. 幂等：同 doc_id 重复入库先删旧 chunk（与 BM25 的 _remove_doc_by_id 对齐，
        #    否则 /ingest 重复触发会在 Milvus 里不断累积重复数据）
        try:
            milvus_manager.delete_by_doc_id(doc_id)
        except Exception as e:
            logger.warning(f"[RAG] 入库前清理旧 doc_id={doc_id} 失败（继续插入）: {e}")

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
        query_type: str = "default",
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
        # 1. 向量检索（带 embedding 容错降级）
        import time as _t
        _t0 = _t.perf_counter()

        cache = _get_cache_manager()

        # L1 Query Cache：相同 query 直接返回缓存的结果（cache_manager 进程内 LRU）
        cache_key = hashlib.md5(f"{query}_{top_k}_{doc_ids}_{visibility_expr}".encode()).hexdigest()
        cached_l1 = cache.l1_get(cache_key)
        if cached_l1 is not None:
            logger.info(f"[QueryCache] L1命中: {query[:30]}...")
            return cached_l1

        # Embedding 容错降级：模型不可用时回退纯 BM25
        query_embedding = None
        try:
            query_embedding = self.embedding.embed_query(query)
        except Exception as _emb_e:
            logger.warning(f"[RAG降级] Embedding 计算失败，降级为纯 BM25 检索: {type(_emb_e).__name__}: {_emb_e}")
        _t_embed = _t.perf_counter() - _t0

        # ---- L2: Semantic Cache（余弦相似度，Redis，经 cache_manager 门面）----
        if query_embedding is not None and settings.SEMANTIC_CACHE_ENABLED:
            try:
                cached_result = _run_async(
                    cache.l2_lookup(query, query_embedding)
                )
                if cached_result is not None:
                    # 同时写入 L1 内存缓存，加速后续完全相同的查询
                    cache.l1_set(cache_key, cached_result)
                    return cached_result
            except Exception as _sc_e:
                logger.debug(f"[SemanticCache] L2 lookup 异常，降级到 L3: {_sc_e}")

        # 向量检索（仅当 embedding 成功时执行）
        vector_results = []
        if query_embedding is not None:
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
                    _t.sleep(1)
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

        # 2.5 Graph RAG：实体提取 + Neo4j 2-hop 子图检索
        graph_context = None
        try:
            from app.core.neo4j_client import neo4j_client as _graph_client
            if _graph_client.is_connected:
                import re as _re
                # SuperPower-2: 扩展正则以捕获模糊实体编码（mat001, MAT-OO1, PO20250101 等）
                _code_entities = _re.findall(r'(MAT[-\s]?[O0\d]+|PO[-\s]?\d+|SUP[-\s]?[A-Z0-9]+)', query, _re.IGNORECASE)
                # (entity, entity_type)：编码实体类型留空由前缀推断
                entity_specs = [(e, None) for e in _code_entities]
                # 实体链接词典：自然语言实体（供应商/物料中文名）→ 图谱查询键，
                # 补齐编码正则覆盖不到的触发面（词典外置 app/data/entity_aliases.json）
                from app.core.entity_linker import entity_linker
                _seen = {_graph_client._normalize_entity(e) for e in _code_entities}
                for _lk in entity_linker.link(query):
                    if _lk["entity"] not in _seen:
                        entity_specs.append((_lk["entity"], _lk["type"]))
                        _seen.add(_lk["entity"])
                entities = [e for e, _ in entity_specs]
                if entity_specs:
                    graph_pairs = []  # [(entity, 子图上下文)]，每实体独立成对
                    for entity, _etype in entity_specs[:3]:  # 最多 3 个实体
                        # 同步调用栈直接用同步 driver；旧实现（线程池 + asyncio.run 新 loop
                        # 复用异步连接池）存在约 50% 交替失败（'NoneType' has no attribute 'send'）
                        ctx = _graph_client.get_2hop_subgraph_context_sync(entity, entity_type=_etype)
                        if ctx:
                            graph_pairs.append((entity, ctx))
                    if graph_pairs:
                        graph_context = " ".join(c for _, c in graph_pairs)
                        logger.info(f"[GraphRAG] 实体={entities[:3]} 图谱上下文={len(graph_context)}chars")
        except Exception as _e:
            logger.debug(f"[GraphRAG] 图检索跳过: {_e}")

        # 3. 合并去重
        merged = self._merge_results(vector_results, bm25_results, query=query, query_type=query_type)

        # 注入 Graph RAG 上下文（作为伪 Chunk 参与 Reranker 精排）
        # Agentic RAG: Graph + Critic 双路评估 (Section 5.6)
        # 参考论文: Singh et al. "Agentic RAG" (arXiv:2501.09136)
        # Agent-G 架构: Critic Module 评估 Graph 和 Document 两路检索结果的质量
        # 按实体拆分（GRAPH_CHUNK_SPLIT_BY_ENTITY=True，默认）：每实体独立过 Critic、独立成 chunk，
        # 让 Reranker 逐实体裁决，避免多实体拼大块时无关实体稀释相关性、只能整体取中间分；
        # 整段模式（False，回滚/对照）：多实体拼成单块整段注入，用整体 keyword_overlap 判定
        if graph_context:
            query_keywords = CriticEvaluator.extract_keywords(query)
            injected, filtered = 0, 0
            _split_by_entity = settings.GRAPH_CHUNK_SPLIT_BY_ENTITY
            if _split_by_entity:
                inject_units = list(graph_pairs)
            else:
                inject_units = [("+".join(e for e, _ in graph_pairs), " ".join(c for _, c in graph_pairs))]
            for _g_entity, _g_ctx in inject_units:
                graph_keywords = CriticEvaluator.extract_keywords(_g_ctx)
                overlap = len(query_keywords & graph_keywords) / max(len(query_keywords), 1)
                # 只有当该实体的 Graph 结果与查询相关时才注入（避免噪声）
                if overlap > 0.2 or not merged:
                    graph_chunk = {
                        "chunk_id": f"neo4j_graph_{hashlib.md5(_g_ctx.encode()).hexdigest()[:8]}",
                        "content": _g_ctx,
                        # source 带实体标识便于冲突检测/引用溯源（拆分模式）；整段模式保持裸 "neo4j_graph"
                        "source": f"neo4j_graph:{_g_entity}" if _split_by_entity else "neo4j_graph",
                        "score": max((d.get("score", 0) for d in merged), default=0.5),
                        # 降级路径（reranker 关闭且模型未加载）按 rrf_score 排序，
                        # 若缺此字段图谱 chunk 会取 0 分沉底被 top_k 截掉
                        "rrf_score": max((d.get("rrf_score", 0) for d in merged), default=0.5),
                        "retrieval_source": "neo4j_graph",
                        "graph_entity": _g_entity,
                    }
                    merged.insert(0, graph_chunk)  # 插入首位，确保参与精排
                    injected += 1
                    logger.info(f"[GraphRAG+Critic] Graph结果注入 entity={_g_entity} (keyword_overlap={overlap:.2f})")
                else:
                    filtered += 1
                    logger.info(f"[GraphRAG+Critic] Graph结果被Critic过滤 entity={_g_entity} (keyword_overlap={overlap:.2f})")

        # 只取top N给reranker（CPU上cross-encoder推理慢，减少输入量）
        merged_for_rerank = merged[:settings.RERANK_TOP_K * 2]  # 取rerank_top_k的2倍

        if not merged:
            return {
                "results": [],
                "confidence": 0.0,
                "conflicts": [],
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
                doc["rerank_score"] = doc.get("rrf_score", 0.0)
            reranked = sorted(merged_for_rerank, key=lambda x: x["rerank_score"], reverse=True)[:top_k]

        # rerank 分数截断：丢弃 sigmoid_normalize(rerank_score) 低于阀值的块（精度过滤，保底≥1 避免空结果）
        _rst = settings.RERANK_SCORE_THRESHOLD
        if _rst > 0 and reranked:
            _kept = [d for d in reranked if sigmoid_normalize(d.get("rerank_score", 0.0)) >= _rst]
            if _kept:
                if len(_kept) < len(reranked):
                    logger.info(f"[RerankCutoff] threshold={_rst}: {len(reranked)}→{len(_kept)} chunks")
                reranked = _kept
        _t_rerank = _t.perf_counter() - _t2
        logger.info(f"[RAG检索] embed={_t_embed*1000:.0f}ms vector={_t_vec*1000:.0f}ms bm25={_t_bm25*1000:.0f}ms rerank={_t_rerank*1000:.0f}ms total={(_t_embed+_t_vec+_t_bm25+_t_rerank)*1000:.0f}ms")
        # 检测reranker是否真正参与了精排：如果_model为None则走了降级路径
        retrieval_method = "hybrid_reranked" if self.reranker._model is not None else "hybrid_no_rerank"

        # 5. 计算置信度
        confidence = self._calculate_confidence(reranked)
        conflicts = self._detect_conflicts(reranked)

        result = {
            "results": reranked,
            "confidence": confidence,
            "conflicts": conflicts,
            "query_type": "rag_answer",
            "retrieval_method": retrieval_method,
        }

        # 存入 L1 Query Cache
        cache.l1_set(cache_key, result)

        # ---- L2: Semantic Cache 写入（异步不阻塞返回）----
        if query_embedding is not None and settings.SEMANTIC_CACHE_ENABLED:
            try:
                _run_async(
                    cache.l2_store(query, query_embedding, result)
                )
            except Exception as _sc_e:
                logger.debug(f"[SemanticCache] L2 store 异常，优雅降级: {_sc_e}")

        return result

    @staticmethod
    def _normalize_query_entities(text: str) -> str:
        """查询实体归一化 — 修复常见 OCR/手误后用于 query_type 检测。

        与 neo4j_client._normalize_entity() 对齐逻辑：
        - 去空白、转大写
        - O→0 纠错 (MAT-OO1 → MAT-001)
        - 补连字符 (MAT001 → MAT-001)

        这样用户输入 mat001 / MAT OO1 / mat-001 都能被 precise regex 命中。
        """
        import re as _re
        normalized = text.strip().upper()
        # 1. 去除中间空白
        normalized = _re.sub(r'\s+', '', normalized)
        # 2. 编码段 O→0 纠错
        normalized = _re.sub(
            r'(MAT|PO|SUP)(-?)([O0\d]+)',
            lambda m: m.group(1) + m.group(2) + ''.join(
                '0' if c == 'O' else c for c in m.group(3)
            ),
            normalized,
        )
        # 3. 补连字符: MAT001 → MAT-001
        normalized = _re.sub(r'^(MAT|PO|SUP)(\d)', r'\1-\2', normalized)
        return normalized

    @staticmethod
    def _merge_results(
        vector_results: list[dict], bm25_results: list[dict],
        query: str = "", query_type: str = "default"
    ) -> list[dict]:
        """RRF 融合排序。query_type 控制按查询类型设置的权重：
        - 'precise'（含编码/数字）：BM25 权重 ×1.5
        - 'semantic'（含怎么/如何）：向量权重 ×1.5
        - 'default'：等权
        """
        RRF_K = settings.RRF_K

        # 按 query_type 设置权重（全部从 settings 读取，避免硬编码）
        if query_type == "precise":
            bm25_weight = settings.RRF_BM25_WEIGHT_PRECISE
            vec_weight = settings.RRF_VECTOR_WEIGHT_DEFAULT
        elif query_type == "semantic":
            bm25_weight = settings.RRF_BM25_WEIGHT_DEFAULT
            vec_weight = settings.RRF_VECTOR_WEIGHT_SEMANTIC
        else:
            bm25_weight = settings.RRF_BM25_WEIGHT_DEFAULT
            vec_weight = settings.RRF_VECTOR_WEIGHT_DEFAULT

        # Fallback: rule-based detection from query text
        if query_type == "default" and query:
            import re
            # 归一化查询中的实体编码（O→0, 补连字符, 去空白）
            normalized_query = RAGEngine._normalize_query_entities(query)
            has_semantic = bool(re.search(r'怎么|如何|什么|哪些|为什么|介绍|说明', query))
            has_precise = bool(re.search(
                r'[A-Z]{2,}-?\d{3,}|\d{4,}|MAT-\d+|PO-\d+|SUP-\d+',
                normalized_query
            ))
            if has_precise and not has_semantic:
                bm25_weight = settings.RRF_BM25_WEIGHT_PRECISE
                vec_weight = settings.RRF_VECTOR_WEIGHT_DEFAULT
            elif has_semantic and not has_precise:
                bm25_weight = settings.RRF_BM25_WEIGHT_DEFAULT
                vec_weight = settings.RRF_VECTOR_WEIGHT_SEMANTIC

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
        merged = []
        for cid, doc in all_docs.items():
            rrf_score = 0.0
            in_vector = cid in vector_ranks
            in_bm25 = cid in bm25_ranks
            if in_vector:
                rrf_score += vec_weight / (RRF_K + vector_ranks[cid])
            if in_bm25:
                rrf_score += bm25_weight / (RRF_K + bm25_ranks[cid])
            if in_vector and in_bm25:
                doc["retrieval_source"] = "vector+bm25"
            elif in_vector:
                doc["retrieval_source"] = "vector"
            elif in_bm25:
                doc["retrieval_source"] = "bm25"
            doc["rrf_score"] = rrf_score
            merged.append(doc)

        # 按 RRF 分数降序排列
        merged.sort(key=lambda x: x["rrf_score"], reverse=True)

        # ---- 后处理 ----
        merged = RAGEngine._filter_low_score(merged, min_rrf=settings.RRF_MIN_SCORE)
        merged = RAGEngine._dedup_by_similarity(merged, threshold=settings.JACCARD_DEDUP_THRESHOLD)

        return merged

    @staticmethod
    def _filter_low_score(results: list[dict], min_rrf: float = 0.008) -> list[dict]:
        """过滤 RRF 分数过低的结果（两路检索都没排进前 50 名）"""
        return [r for r in results if r.get("rrf_score", 0) >= min_rrf]

    @staticmethod
    def _dedup_by_similarity(results: list[dict], threshold: float = 0.7) -> list[dict]:
        """相邻 chunk 语义去重：Jaccard 相似度 > 0.7 的只保留分数高的"""
        if len(results) <= 1:
            return results

        def jaccard(text_a: str, text_b: str, n: int = 2) -> float:
            """2-gram Jaccard 相似度"""
            if not text_a or not text_b:
                return 0.0
            a_grams = {text_a[i:i+n] for i in range(len(text_a) - n + 1)}
            b_grams = {text_b[i:i+n] for i in range(len(text_b) - n + 1)}
            if not a_grams or not b_grams:
                return 0.0
            return len(a_grams & b_grams) / len(a_grams | b_grams)

        kept = [results[0]]
        for r in results[1:]:
            content = r.get("content", "")
            is_dup = False
            for k in kept[-3:]:  # 只和前 3 个比较
                if jaccard(content, k.get("content", "")) > threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(r)
        return kept

    @staticmethod
    def _detect_conflicts(results: list[dict], top_n: int = 5) -> list[dict]:
        """检测多源数据冲突：同一实体在不同 chunk 中出现不同数值时标记"""
        import re
        entities = {}  # entity_name -> [(value, chunk_id, rrf_score)]
        for r in results[:top_n]:
            text = r.get("content", "")
            cid = r.get("chunk_id", "")
            score = r.get("rrf_score", 0)
            # 提取 实体名+数字 对：如 "安全库存为 100 件" / "安全库存=50"
            pairs = re.findall(
                r"([\u4e00-\u9fff]{2,8}(?:标准|库存|阈值|上限|下限|比例|周期|期限|天数|金额|价格|费率))[^\d]{0,5}(\d+(?:\.\d+)?)",
                text
            )
            for entity, value in pairs:
                value = float(value)
                if entity not in entities:
                    entities[entity] = []
                entities[entity].append((value, cid, score))

        conflicts = []
        for entity, vals in entities.items():
            unique_vals = set(v[0] for v in vals)
            if len(unique_vals) > 1:
                conflicts.append({
                    "entity": entity,
                    "values": sorted(unique_vals),
                    "sources": [v[1] for v in vals],
                    "type": "numeric_conflict",
                })

        return conflicts

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
        confidence = sigmoid_normalize(top_score)

        return round(confidence, 4)

    @staticmethod
    def fuse_with_graph(
        rag_results: list[dict],
        graph_matched_entities: set,
        alpha: float = None,
        beta: float = None,
    ) -> list[dict]:
        """
        将图谱检索结果融合到 RAG 结果排序中。

        图检索是精确命中（二值：匹配/未匹配），不是连续排名，
        所以在 RRF 分数基础上用加权叠加而非放入 RRF 公式。

        Args:
            rag_results: RRF 融合后的结果列表（已含 rrf_score）
            graph_matched_entities: 图谱中匹配到的实体编码集合（如 {"MAT-001","PO-001"}）
            alpha: RRF 权重（默认从 settings.GRAPH_FUSION_ALPHA 读取）
            beta: 图谱权重（默认从 settings.GRAPH_FUSION_BETA 读取）

        Returns:
            重新排序后的结果列表（新增 graph_score 字段）
        """
        if alpha is None:
            alpha = settings.GRAPH_FUSION_ALPHA
        if beta is None:
            beta = settings.GRAPH_FUSION_BETA
        if not graph_matched_entities:
            return rag_results

        for item in rag_results:
            content = item.get("content", "")
            # 检查 chunks 内容是否包含图谱匹配的实体
            graph_hit = any(
                entity.lower() in content.lower()
                for entity in graph_matched_entities
            )
            item["graph_score"] = 1.0 if graph_hit else 0.0
            item["final_score"] = (
                alpha * item.get("rrf_score", 0) + beta * item["graph_score"]
            )

        # 按 final_score 降序重排
        rag_results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return rag_results


# 全局单例
rag_engine = RAGEngine()
