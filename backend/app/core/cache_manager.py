"""
SupplyChainRAG - 多层缓存统一门面（4 层缓存架构）

缓存分层（自上而下，命中即返回）：
  L1 — 进程内 LRU + TTL（最快，进程私有，多 worker 各持一份）
  L2 — Redis 语义缓存（余弦相似度匹配，跨实例共享，见 semantic_cache.py）
  L3 — Redis 数据查询结果缓存（Text-to-SQL / 只读工具的 read-through）
  L4 — nginx 静态资源缓存（前端构建产物，见 frontend/nginx.conf，不经过本模块）

设计原则：
- 门面只做统一入口 + 命中率指标收集，不迁移各层业务逻辑
- Redis 不可用时一律优雅降级（loader 直通 / 返回 miss），绝不抛错阻断主链路
- 所有异常路径至少 logger.warning（CLAUDE.md 禁止静默 pass）
"""
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


class _LayerStats:
    """单层缓存命中率计数器"""

    __slots__ = ("hits", "misses")

    def __init__(self):
        self.hits = 0
        self.misses = 0

    def hit(self):
        self.hits += 1

    def miss(self):
        self.misses += 1

    def as_dict(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }


class CacheManager:
    """多层缓存统一门面

    L1 用 OrderedDict 实现 LRU：命中时 move_to_end，写入超限时 popitem(last=False)。
    L2 委托 SemanticCache（懒加载，避免循环依赖）。
    L3 为通用 read-through Redis 缓存，key 结构 scqa:l3:{namespace}:{key}。
    """

    _L3_PREFIX = "scqa:l3"

    def __init__(self, l1_max: Optional[int] = None, l1_ttl: Optional[int] = None):
        settings = get_settings()
        self._l1_max = l1_max or settings.L1_CACHE_MAX
        self._l1_ttl = l1_ttl or settings.L1_CACHE_TTL
        # key -> (存入时间戳, value)
        self._l1: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._stats = {
            "l1": _LayerStats(),
            "l2": _LayerStats(),
            "l3": _LayerStats(),
        }

    # ------------------------------------------------------------------
    # L1 — 进程内 LRU + TTL
    # ------------------------------------------------------------------
    def l1_get(self, key: str) -> Optional[Any]:
        """L1 查询：命中返回值并刷新 LRU 顺序；过期条目惰性删除"""
        entry = self._l1.get(key)
        if entry is None:
            self._stats["l1"].miss()
            return None
        ts, value = entry
        if time.time() - ts >= self._l1_ttl:
            del self._l1[key]
            self._stats["l1"].miss()
            return None
        self._l1.move_to_end(key)
        self._stats["l1"].hit()
        return value

    def l1_set(self, key: str, value: Any) -> None:
        """L1 写入：超限时淘汰最久未使用的条目"""
        if key in self._l1:
            self._l1.move_to_end(key)
        self._l1[key] = (time.time(), value)
        while len(self._l1) > self._l1_max:
            self._l1.popitem(last=False)

    def l1_clear(self) -> int:
        """清空 L1（知识库变更后调用，避免脏检索结果）"""
        count = len(self._l1)
        self._l1.clear()
        return count

    # ------------------------------------------------------------------
    # L2 — 语义缓存（委托 SemanticCache，只做指标包装）
    # ------------------------------------------------------------------
    @staticmethod
    def _get_semantic_cache():
        from app.core.semantic_cache import semantic_cache
        return semantic_cache

    async def l2_lookup(self, query: str, query_embedding: list[float]) -> Optional[dict]:
        result = await self._get_semantic_cache().lookup(query, query_embedding)
        if result is not None:
            self._stats["l2"].hit()
        else:
            self._stats["l2"].miss()
        return result

    async def l2_store(self, query: str, query_embedding: list[float], result: dict) -> None:
        await self._get_semantic_cache().store(query, query_embedding, result)

    async def l2_invalidate(self) -> None:
        await self._get_semantic_cache().invalidate()

    # ------------------------------------------------------------------
    # L3 — Redis 数据查询结果缓存（read-through）
    # ------------------------------------------------------------------
    @staticmethod
    def _get_redis_client():
        """获取 Redis 客户端，不可用时返回 None（优雅降级）"""
        try:
            from app.core.redis_client import redis_manager
            if redis_manager._pool is None:
                return None
            return redis_manager.client
        except Exception as e:
            logger.debug(f"[CacheManager] Redis 客户端获取失败: {e}")
            return None

    def _l3_key(self, namespace: str, key: str) -> str:
        return f"{self._L3_PREFIX}:{namespace}:{key}"

    async def l3_get_or_set(
        self,
        namespace: str,
        key: str,
        ttl: int,
        loader: Callable[[], Awaitable[Any]],
        cache_if: Optional[Callable[[Any], bool]] = None,
    ) -> Any:
        """L3 read-through：命中返回缓存 JSON；未命中执行 loader 并回写。

        Args:
            cache_if: 可选谓词，loader 结果为 False 时不回写（如错误结果不缓存）

        Redis 不可用或值不可 JSON 序列化时，loader 结果直通不缓存。
        """
        client = self._get_redis_client()
        redis_key = self._l3_key(namespace, key)

        if client is not None:
            try:
                cached = await client.get(redis_key)
                if cached is not None:
                    self._stats["l3"].hit()
                    logger.debug(f"[CacheManager] L3 命中: {namespace}:{key[:16]}...")
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"[CacheManager] L3 读取异常，降级直查: {e}")

        self._stats["l3"].miss()
        value = await loader()

        if client is not None and (cache_if is None or cache_if(value)):
            try:
                await client.set(redis_key, json.dumps(value, ensure_ascii=False), ex=ttl)
            except (TypeError, ValueError) as e:
                logger.warning(f"[CacheManager] L3 值不可序列化，跳过缓存: {e}")
            except Exception as e:
                logger.warning(f"[CacheManager] L3 写入异常，忽略: {e}")

        return value

    async def l3_invalidate(self, namespace: str) -> int:
        """按命名空间清空 L3（写操作后调用，防脏读）"""
        client = self._get_redis_client()
        if client is None:
            return 0
        count = 0
        try:
            cursor = 0
            while True:
                cursor, keys = await client.scan(
                    cursor=cursor, match=f"{self._L3_PREFIX}:{namespace}:*", count=100
                )
                if keys:
                    await client.delete(*keys)
                    count += len(keys)
                if cursor == 0:
                    break
            if count:
                logger.info(f"[CacheManager] L3 失效: namespace={namespace}, 清除 {count} 条")
        except Exception as e:
            logger.warning(f"[CacheManager] L3 失效异常: {e}")
        return count

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """各层命中率快照（L4 由 nginx 承担，不在应用层统计）"""
        return {
            "l1": {**self._stats["l1"].as_dict(), "size": len(self._l1), "max": self._l1_max},
            "l2": self._stats["l2"].as_dict(),
            "l3": self._stats["l3"].as_dict(),
        }


# 全局单例（dependencies.py 提供 provider 供测试替换）
cache_manager = CacheManager()
