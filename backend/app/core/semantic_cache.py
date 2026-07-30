"""
SupplyChainRAG - 语义缓存模块（L2）

语义缓存：相似查询复用 RAG 结果，避免重复检索 + LLM 调用。

与精确匹配缓存的区别：
- 精确匹配: MD5(query) == MD5(cached_query) → 命中
- 语义缓存: cosine_similarity(embed(query), embed(cached_query)) > threshold → 命中
- "库存查询" vs "查一下库存" → 语义相似 → 命中缓存

架构分层（由 cache_manager.py 统一门面收口）：
  L1 — 内存 Query Cache（MD5 精确匹配，最快，进程内）
  L2 — Redis Semantic Cache（余弦相似度，跨请求共享）← 本模块
  L3 — Redis 数据查询结果缓存（Text-to-SQL / 工具）

存储模型（v2，修复 O(n) SCAN 性能问题）：
  - 向量索引: 单个 Hash `scqa:semantic_cache:index`
      field = md5(query)，value = json({"e": embedding, "ts": 时间戳, "v": 知识库版本})
      一次 HGETALL 取回全部向量 → numpy 向量化余弦，网络往返 O(n)→O(1)
  - 结果条目: `scqa:semantic_cache:{md5}` 独立 key，TTL 自动过期
      lookup 命中索引后单次 GET；结果 key 已过期则 HDEL 索引 field（惰性清理）

失效模型（v3，知识库版本号 epoch 失效）：
  - 版本号: `scqa:kb:version` INCR 计数器
  - store 时把当前版本写入条目；lookup 时版本不一致的条目视为 stale，
    跳过并惰性清理 → invalidate() 只需 INCR 一次（O(1)），无需 SCAN 全清
  - purge() 保留 SCAN 全清逻辑作为兜底（运维手动清理）
"""

import hashlib
import json
import logging
import math
import time
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SemanticCache:
    """
    语义缓存：相似查询复用 RAG 结果，避免重复检索 + LLM 调用。

    v2 存储模型：向量集中存 Hash 索引（1 次 HGETALL），结果独立 key（1 次 GET），
    替代 v1 的全量 SCAN + 逐 key GET（200 条时 200+ 次网络往返）。
    """

    # Redis key 前缀
    _KEY_PREFIX = "scqa:semantic_cache"
    # 向量索引 Hash key（field=md5(query), value=json({"e": embedding, "ts": ts, "v": version})）
    _INDEX_KEY = "scqa:semantic_cache:index"
    # 知识库版本号 key（INCR 实现 O(1) 全量失效）
    _VERSION_KEY = "scqa:kb:version"

    def __init__(self):
        # 延迟导入避免循环依赖；在方法内按需获取
        self._redis_manager = None

    def _get_redis_client(self) -> Optional[Any]:
        """获取 Redis 客户端，失败时返回 None（优雅降级）"""
        try:
            from app.core.redis_client import redis_manager
            if redis_manager._pool is None:
                return None
            return redis_manager.client
        except Exception as e:
            logger.debug(f"[SemanticCache] Redis 客户端获取失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 余弦相似度
    # ------------------------------------------------------------------
    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """
        计算两个向量的余弦相似度（纯 Python 版，供单向量比较/测试使用）。

        返回值范围 [-1, 1]：
          1.0  = 完全相同方向
          0.0  = 正交（不相关）
         -1.0  = 完全相反方向

        如果向量维度不一致或为空，返回 0.0。
        """
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for ai, bi in zip(a, b):
            dot += ai * bi
            norm_a += ai * ai
            norm_b += bi * bi

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    @staticmethod
    def _batch_cosine(query_embedding: list[float], embeddings: list[list[float]]) -> list[float]:
        """numpy 向量化批量余弦：一次矩阵乘替代 n 次循环计算"""
        import numpy as np

        m = np.asarray(embeddings, dtype=np.float32)
        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return [0.0] * len(embeddings)
        m_norms = np.linalg.norm(m, axis=1)
        denom = np.where(m_norms == 0.0, 1.0, m_norms) * q_norm
        sims = (m @ q) / denom
        # 零向量行强制置 0（denom 兜底 1.0 时点积也为 0，此处防御性处理）
        sims = np.where(m_norms == 0.0, 0.0, sims)
        return sims.tolist()

    # ------------------------------------------------------------------
    # Redis key 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _make_key(query: str) -> str:
        """根据查询文本生成 Redis key"""
        h = hashlib.md5(query.encode("utf-8")).hexdigest()
        return f"scqa:semantic_cache:{h}"

    @staticmethod
    def _make_field(query: str) -> str:
        """索引 Hash 的 field（与 _make_key 的 hash 部分一致）"""
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # 知识库版本号
    # ------------------------------------------------------------------
    async def _get_kb_version(self, client: Any) -> int:
        """读取当前知识库版本号（key 不存在视为 0）"""
        raw = await client.get(self._VERSION_KEY)
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            logger.warning(f"[SemanticCache] 版本号损坏: {raw!r}，按 0 处理")
            return 0

    # ------------------------------------------------------------------
    # 查询（lookup）
    # ------------------------------------------------------------------
    async def lookup(
        self, query: str, query_embedding: list[float]
    ) -> Optional[dict]:
        """
        语义缓存查询（2 次 Redis 往返）。

        1. HGETALL 索引 → numpy 批量余弦 → 找最优 field
        2. 最优相似度 >= 阈值时，GET 对应结果 key
        3. 结果 key 已过期（TTL 淘汰）→ HDEL 索引 field 惰性清理，返回 miss

        Args:
            query: 查询文本（仅用于日志）
            query_embedding: 查询的嵌入向量

        Returns:
            命中时返回缓存的 RAG result dict；未命中返回 None。
        """
        if not settings.SEMANTIC_CACHE_ENABLED:
            return None

        client = self._get_redis_client()
        if client is None:
            logger.debug("[SemanticCache] Redis 不可用，跳过语义缓存查询")
            return None

        try:
            kb_version = await self._get_kb_version(client)
            index = await client.hgetall(self._INDEX_KEY)
            if not index:
                return None

            fields: list[str] = []
            embeddings: list[list[float]] = []
            stale_fields: list[str] = []
            for field, raw in index.items():
                try:
                    entry = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                # 知识库版本不一致 → stale，跳过并记录待清理
                if int(entry.get("v", 0)) != kb_version:
                    stale_fields.append(field)
                    continue
                emb = entry.get("e")
                if emb and len(emb) == len(query_embedding):
                    fields.append(field)
                    embeddings.append(emb)

            # 惰性清理 stale 条目（索引 field + 结果 key），失败不影响主链路
            if stale_fields:
                try:
                    pipe = client.pipeline(transaction=True)
                    pipe.hdel(self._INDEX_KEY, *stale_fields)
                    pipe.delete(*[f"{self._KEY_PREFIX}:{f}" for f in stale_fields])
                    await pipe.execute()
                    logger.info(f"[SemanticCache] 惰性清理 {len(stale_fields)} 条过期版本条目")
                except Exception as e:
                    logger.warning(f"[SemanticCache] stale 条目清理失败: {e}")

            if not fields:
                return None

            sims = self._batch_cosine(query_embedding, embeddings)
            best_idx = max(range(len(sims)), key=lambda i: sims[i])
            best_score = sims[best_idx]

            if best_score < settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD:
                logger.debug(
                    f"[SemanticCache] 未命中: query='{query[:30]}...' "
                    f"best_score={best_score:.4f} "
                    f"threshold={settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD}"
                )
                return None

            best_field = fields[best_idx]
            raw = await client.get(f"{self._KEY_PREFIX}:{best_field}")
            if raw is None:
                # 结果 key 已 TTL 过期 → 惰性清理索引 field
                await client.hdel(self._INDEX_KEY, best_field)
                logger.debug(f"[SemanticCache] 索引命中但结果已过期，已清理 field={best_field[:8]}")
                return None

            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"[SemanticCache] 结果条目损坏，跳过: field={best_field[:8]}")
                return None

            logger.info(
                f"[SemanticCache] 命中: query='{query[:30]}...' "
                f"cached='{entry.get('query_text', '')[:30]}...' "
                f"score={best_score:.4f} "
                f"threshold={settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD} "
                f"kb_version={kb_version}"
            )
            return entry.get("cached_result")

        except Exception as e:
            logger.warning(f"[SemanticCache] lookup 异常，优雅降级: {e}")
            return None

    # ------------------------------------------------------------------
    # 存储（store）
    # ------------------------------------------------------------------
    async def store(
        self, query: str, query_embedding: list[float], result: dict
    ) -> None:
        """
        将查询 + 嵌入 + RAG 结果存入 Redis（pipeline 一次写入索引 + 结果）。

        超出 SEMANTIC_CACHE_MAX_ENTRIES 时按索引 ts 淘汰最旧条目。
        """
        if not settings.SEMANTIC_CACHE_ENABLED:
            return

        client = self._get_redis_client()
        if client is None:
            logger.debug("[SemanticCache] Redis 不可用，跳过语义缓存存储")
            return

        try:
            kb_version = await self._get_kb_version(client)
            field = self._make_field(query)
            entry = {
                "query_text": query,
                "cached_result": result,
                "timestamp": time.time(),
                "v": kb_version,
            }
            index_entry = {"e": query_embedding, "ts": time.time(), "v": kb_version}

            pipe = client.pipeline(transaction=True)
            pipe.hset(self._INDEX_KEY, field, json.dumps(index_entry))
            pipe.set(
                f"{self._KEY_PREFIX}:{field}",
                json.dumps(entry, ensure_ascii=False),
                ex=settings.SEMANTIC_CACHE_TTL,
            )
            await pipe.execute()

            await self._evict_if_needed(client)
            logger.debug(
                f"[SemanticCache] 已存储: query='{query[:30]}...' ttl={settings.SEMANTIC_CACHE_TTL}s"
            )
        except Exception as e:
            logger.warning(f"[SemanticCache] store 异常，优雅降级: {e}")

    async def _evict_if_needed(self, client: Any) -> None:
        """索引超上限时，按 ts 淘汰最旧的 field 及其结果 key"""
        size = await client.hlen(self._INDEX_KEY)
        max_entries = settings.SEMANTIC_CACHE_MAX_ENTRIES
        if size <= max_entries:
            return

        index = await client.hgetall(self._INDEX_KEY)
        entries: list[tuple[float, str]] = []
        for field, raw in index.items():
            try:
                ts = float(json.loads(raw).get("ts", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                ts = 0.0  # 损坏条目优先淘汰
            entries.append((ts, field))

        entries.sort()  # ts 升序 → 最旧在前
        to_evict = [field for _, field in entries[: len(entries) - max_entries]]
        if to_evict:
            pipe = client.pipeline(transaction=True)
            pipe.hdel(self._INDEX_KEY, *to_evict)
            pipe.delete(*[f"{self._KEY_PREFIX}:{f}" for f in to_evict])
            await pipe.execute()
            logger.info(f"[SemanticCache] 容量淘汰: 清除 {len(to_evict)} 条最旧条目")

    # ------------------------------------------------------------------
    # 失效（invalidate）
    # ------------------------------------------------------------------
    async def invalidate(self) -> None:
        """知识库变更时失效全部语义缓存：INCR 版本号（O(1)）

        旧版本条目在下次 lookup 时被惰性跳过并清理，无需 SCAN 全清。
        """
        client = self._get_redis_client()
        if client is None:
            logger.debug("[SemanticCache] Redis 不可用，无法失效缓存")
            return

        try:
            new_version = await client.incr(self._VERSION_KEY)
            logger.info(f"[SemanticCache] 已失效: 知识库版本号 → {new_version}（旧条目惰性清理）")
        except Exception as e:
            logger.warning(f"[SemanticCache] invalidate 异常: {e}")

    async def purge(self) -> None:
        """物理清除所有语义缓存条目（索引 + 结果 key），SCAN 全清兜底"""
        client = self._get_redis_client()
        if client is None:
            logger.debug("[SemanticCache] Redis 不可用，无法清除缓存")
            return

        try:
            count = 0
            cursor = 0
            while True:
                cursor, keys = await client.scan(
                    cursor=cursor,
                    match=f"{self._KEY_PREFIX}:*",
                    count=100,
                )
                if keys:
                    await client.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break

            logger.info(f"[SemanticCache] 已物理清除 {count} 条语义缓存（含索引）")
        except Exception as e:
            logger.warning(f"[SemanticCache] purge 异常: {e}")


# 全局单例（供 cache_manager 门面委托）
semantic_cache = SemanticCache()
