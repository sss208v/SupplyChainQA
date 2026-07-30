"""
tests/test_cache_manager.py — 多层缓存统一门面单元测试

覆盖：
- L1: 命中/未命中/TTL 过期/LRU 淘汰/clear
- L3: read-through 命中/未命中回写/cache_if 谓词/Redis 不可用直通/命名空间失效
- stats: 各层命中率结构
"""
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.cache_manager import CacheManager


def _make_redis():
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.delete = AsyncMock()
    client.scan = AsyncMock(return_value=(0, []))
    return client


# ===========================================================================
# L1 — 进程内 LRU + TTL
# ===========================================================================

class TestL1Cache:
    def test_miss_then_hit(self):
        cm = CacheManager(l1_max=4, l1_ttl=300)
        assert cm.l1_get("k1") is None
        cm.l1_set("k1", {"v": 1})
        assert cm.l1_get("k1") == {"v": 1}

    def test_ttl_expiry(self, monkeypatch):
        cm = CacheManager(l1_max=4, l1_ttl=10)
        cm.l1_set("k1", "v1")
        # 前进 11 秒 → 过期
        real_time = time.time()
        monkeypatch.setattr(time, "time", lambda: real_time + 11)
        assert cm.l1_get("k1") is None

    def test_lru_eviction_order(self):
        """超限时淘汰最久未使用的条目（get 会刷新 LRU 顺序）"""
        cm = CacheManager(l1_max=2, l1_ttl=300)
        cm.l1_set("a", 1)
        cm.l1_set("b", 2)
        cm.l1_get("a")      # 刷新 a → b 变为最旧
        cm.l1_set("c", 3)   # 淘汰 b
        assert cm.l1_get("a") == 1
        assert cm.l1_get("b") is None
        assert cm.l1_get("c") == 3

    def test_clear(self):
        cm = CacheManager(l1_max=4, l1_ttl=300)
        cm.l1_set("a", 1)
        cm.l1_set("b", 2)
        assert cm.l1_clear() == 2
        assert cm.l1_get("a") is None

    def test_stats_count_hits_misses(self):
        cm = CacheManager(l1_max=4, l1_ttl=300)
        cm.l1_get("x")          # miss
        cm.l1_set("x", 1)
        cm.l1_get("x")          # hit
        s = cm.stats()["l1"]
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate"] == 0.5


# ===========================================================================
# L3 — Redis read-through
# ===========================================================================

class TestL3Cache:
    @pytest.mark.asyncio
    async def test_miss_calls_loader_and_writes_back(self):
        cm = CacheManager()
        client = _make_redis()
        cm._get_redis_client = MagicMock(return_value=client)

        async def loader():
            return {"rows": [1, 2]}

        result = await cm.l3_get_or_set("t2sql", "k1", 60, loader)
        assert result == {"rows": [1, 2]}
        client.set.assert_awaited_once()
        args = client.set.await_args
        assert args.args[0] == "scqa:l3:t2sql:k1"
        assert json.loads(args.args[1]) == {"rows": [1, 2]}
        assert args.kwargs["ex"] == 60

    @pytest.mark.asyncio
    async def test_hit_skips_loader(self):
        cm = CacheManager()
        client = _make_redis()
        client.get = AsyncMock(return_value=json.dumps({"cached": True}))
        cm._get_redis_client = MagicMock(return_value=client)

        called = {"n": 0}

        async def loader():
            called["n"] += 1
            return {"cached": False}

        result = await cm.l3_get_or_set("tool", "k1", 30, loader)
        assert result == {"cached": True}
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_cache_if_false_skips_write(self):
        """错误结果（cache_if 返回 False）不回写缓存"""
        cm = CacheManager()
        client = _make_redis()
        cm._get_redis_client = MagicMock(return_value=client)

        async def loader():
            return {"error": "db down"}

        result = await cm.l3_get_or_set(
            "t2sql", "k1", 60, loader, cache_if=lambda v: not v.get("error")
        )
        assert result["error"] == "db down"
        client.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redis_unavailable_passthrough(self):
        """Redis 不可用 → loader 直通，不抛异常"""
        cm = CacheManager()
        cm._get_redis_client = MagicMock(return_value=None)

        async def loader():
            return "direct"

        assert await cm.l3_get_or_set("tool", "k", 30, loader) == "direct"

    @pytest.mark.asyncio
    async def test_redis_get_exception_degrades(self):
        """Redis get 抛异常 → 降级执行 loader"""
        cm = CacheManager()
        client = _make_redis()
        client.get = AsyncMock(side_effect=ConnectionError("down"))
        cm._get_redis_client = MagicMock(return_value=client)

        async def loader():
            return 42

        assert await cm.l3_get_or_set("tool", "k", 30, loader) == 42

    @pytest.mark.asyncio
    async def test_invalidate_namespace(self):
        cm = CacheManager()
        client = _make_redis()
        client.scan = AsyncMock(return_value=(0, ["scqa:l3:tool:a", "scqa:l3:tool:b"]))
        cm._get_redis_client = MagicMock(return_value=client)

        count = await cm.l3_invalidate("tool")
        assert count == 2
        client.delete.assert_awaited_once_with("scqa:l3:tool:a", "scqa:l3:tool:b")

    @pytest.mark.asyncio
    async def test_invalidate_redis_unavailable(self):
        cm = CacheManager()
        cm._get_redis_client = MagicMock(return_value=None)
        assert await cm.l3_invalidate("tool") == 0


# ===========================================================================
# stats 结构
# ===========================================================================

class TestStats:
    def test_stats_structure(self):
        cm = CacheManager()
        s = cm.stats()
        for layer in ("l1", "l2", "l3"):
            assert {"hits", "misses", "hit_rate"} <= set(s[layer].keys())
        assert "size" in s["l1"]
        assert "max" in s["l1"]

    def test_module_singleton(self):
        from app.core.cache_manager import cache_manager
        assert isinstance(cache_manager, CacheManager)
