"""
tests/test_semantic_cache.py — SemanticCache 单元测试（v2 Hash 索引 + v3 版本号失效）

覆盖范围：
- _cosine_similarity / _batch_cosine: 相同/正交/相反/空向量/维度不匹配
- lookup: 命中/未命中/低于阈值/结果过期惰性清理/Redis不可用/功能禁用/损坏条目
- store: 写入索引+结果（含版本号） / 容量淘汰 / Redis不可用不抛异常 / 功能禁用
- invalidate: INCR 版本号（O(1) 失效）/ Redis不可用时不抛异常
- purge: SCAN 全清兜底
- 版本号失效: stale 条目跳过 + 惰性清理
"""

import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pipe():
    """pipeline mock：链式命令是同步的，execute 是异步的"""
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    return pipe


@pytest.fixture
def mock_redis_client():
    """创建一个模拟的 Redis 异步客户端（v2 需要 hash 命令 + v3 需要 incr）"""
    client = AsyncMock()
    client.scan = AsyncMock(return_value=(0, []))
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.delete = AsyncMock()
    client.hgetall = AsyncMock(return_value={})
    client.hdel = AsyncMock()
    client.hlen = AsyncMock(return_value=0)
    client.incr = AsyncMock(return_value=1)
    client.pipeline = MagicMock(return_value=_make_pipe())
    return client


@pytest.fixture
def semantic_cache(mock_redis_client):
    """创建带 mock Redis 的 SemanticCache 实例"""
    with patch("app.core.semantic_cache.settings") as mock_settings:
        mock_settings.SEMANTIC_CACHE_ENABLED = True
        mock_settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD = 0.92
        mock_settings.SEMANTIC_CACHE_TTL = 600
        mock_settings.SEMANTIC_CACHE_MAX_ENTRIES = 200

        from app.core.semantic_cache import SemanticCache
        cache = SemanticCache()
        cache._get_redis_client = MagicMock(return_value=mock_redis_client)
        yield cache


@pytest.fixture
def sample_embedding():
    """一个简单的归一化测试向量"""
    return [1.0, 0.0, 0.0]


@pytest.fixture
def sample_result():
    """模拟 RAG search 返回的结果 dict"""
    return {
        "results": [
            {
                "content": "测试文档内容",
                "source": "test.pdf",
                "chunk_id": "chunk_001",
                "rerank_score": 0.85,
            }
        ],
        "confidence": 0.78,
        "query_type": "rag_answer",
        "retrieval_method": "hybrid_reranked",
    }


def _index_entry(embedding, version=0):
    return json.dumps({"e": embedding, "ts": time.time(), "v": version})


def _result_entry(result, query_text="缓存的查询", version=0):
    return json.dumps({
        "query_text": query_text,
        "cached_result": result,
        "timestamp": time.time(),
        "v": version,
    }, ensure_ascii=False)


# ===========================================================================
# 1. 余弦相似度测试
# ===========================================================================

class TestCosineSimilarity:
    """测试余弦相似度计算"""

    def test_identical_vectors(self):
        """相同向量 → 相似度 1.0"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 2.0, 3.0]
        assert SemanticCache._cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """正交向量 → 相似度 0.0"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert SemanticCache._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """相反向量 → 相似度 -1.0"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert SemanticCache._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_similar_vectors(self):
        """相似向量 → 高相似度"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 0.5, 0.3]
        b = [0.9, 0.6, 0.2]
        score = SemanticCache._cosine_similarity(a, b)
        assert score > 0.95  # 应该非常相似

    def test_empty_vectors(self):
        """空向量 → 0.0"""
        from app.core.semantic_cache import SemanticCache
        assert SemanticCache._cosine_similarity([], []) == 0.0
        assert SemanticCache._cosine_similarity([1.0], []) == 0.0
        assert SemanticCache._cosine_similarity([], [1.0]) == 0.0

    def test_dimension_mismatch(self):
        """维度不一致 → 0.0"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert SemanticCache._cosine_similarity(a, b) == 0.0

    def test_zero_vector(self):
        """零向量 → 0.0"""
        from app.core.semantic_cache import SemanticCache
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert SemanticCache._cosine_similarity(a, b) == 0.0

    def test_scaled_vectors(self):
        """缩放不影响余弦相似度"""
        from app.core.semantic_cache import SemanticCache
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]  # a 的 2 倍
        assert SemanticCache._cosine_similarity(a, b) == pytest.approx(1.0)

    def test_batch_cosine_matches_scalar(self):
        """numpy 批量余弦与纯 Python 单条计算结果一致"""
        from app.core.semantic_cache import SemanticCache
        q = [1.0, 0.5, 0.3]
        embeddings = [[1.0, 0.5, 0.3], [0.0, 1.0, 0.0], [-1.0, -0.5, -0.3]]
        batch = SemanticCache._batch_cosine(q, embeddings)
        for emb, got in zip(embeddings, batch):
            expected = SemanticCache._cosine_similarity(q, emb)
            assert got == pytest.approx(expected, abs=1e-5)

    def test_batch_cosine_zero_vectors(self):
        """批量余弦：零查询向量/零缓存向量 → 0.0"""
        from app.core.semantic_cache import SemanticCache
        assert SemanticCache._batch_cosine([0.0, 0.0], [[1.0, 2.0]]) == [0.0]
        result = SemanticCache._batch_cosine([1.0, 2.0], [[0.0, 0.0]])
        assert result[0] == pytest.approx(0.0)


# ===========================================================================
# 2. lookup 测试
# ===========================================================================

class TestLookup:
    """测试语义缓存查询（v2：HGETALL 索引 + GET 结果）"""

    @pytest.mark.asyncio
    async def test_lookup_miss_empty_cache(self, semantic_cache, sample_embedding):
        """空索引 → 返回 None"""
        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_hit(self, semantic_cache, mock_redis_client, sample_embedding, sample_result):
        """相似度超过阈值 → 返回缓存结果"""
        mock_redis_client.hgetall = AsyncMock(
            return_value={"abc123": _index_entry(sample_embedding)}
        )
        # 第 1 次 GET 是版本号（None → 0），第 2 次是结果条目
        mock_redis_client.get = AsyncMock(
            side_effect=[None, _result_entry(sample_result, "相似的查询")]
        )

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is not None
        assert result["confidence"] == sample_result["confidence"]
        assert result["results"][0]["content"] == "测试文档内容"
        # 1 次 HGETALL + 2 次 GET（版本号 + 结果，仍为 O(1) 往返）
        assert mock_redis_client.hgetall.await_count == 1
        assert mock_redis_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_lookup_below_threshold(self, semantic_cache, mock_redis_client, sample_result):
        """相似度低于阈值 → 返回 None（未命中），且不触发 GET"""
        query_embedding = [1.0, 0.0, 0.0]
        cached_embedding = [0.5, 0.5, 0.707]  # 余弦 ≈ 0.5 < 0.92

        mock_redis_client.hgetall = AsyncMock(
            return_value={"xyz789": _index_entry(cached_embedding)}
        )

        result = await semantic_cache.lookup("测试查询", query_embedding)
        assert result is None
        # 仅 1 次版本号 GET，未触发结果 GET
        assert mock_redis_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_lookup_expired_result_lazy_cleanup(
        self, semantic_cache, mock_redis_client, sample_embedding
    ):
        """索引命中但结果 key 已 TTL 过期 → HDEL 清理索引 field，返回 None"""
        mock_redis_client.hgetall = AsyncMock(
            return_value={"stale01": _index_entry(sample_embedding)}
        )
        mock_redis_client.get = AsyncMock(return_value=None)  # 版本号0 + 结果已过期

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None
        mock_redis_client.hdel.assert_awaited_once()
        args = mock_redis_client.hdel.await_args.args
        assert "stale01" in args

    @pytest.mark.asyncio
    async def test_lookup_redis_unavailable(self, sample_embedding):
        """Redis 不可用 → 返回 None（优雅降级）"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = True

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()
            cache._get_redis_client = MagicMock(return_value=None)

            result = await cache.lookup("测试查询", sample_embedding)
            assert result is None

    @pytest.mark.asyncio
    async def test_lookup_disabled(self, sample_embedding):
        """SEMANTIC_CACHE_ENABLED=False → 返回 None"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = False

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()

            result = await cache.lookup("测试查询", sample_embedding)
            assert result is None

    @pytest.mark.asyncio
    async def test_lookup_hgetall_exception(self, semantic_cache, mock_redis_client, sample_embedding):
        """Redis hgetall 抛异常 → 返回 None（优雅降级）"""
        mock_redis_client.hgetall = AsyncMock(side_effect=ConnectionError("Redis connection lost"))

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_corrupted_index_entry(self, semantic_cache, mock_redis_client, sample_embedding):
        """索引条目损坏（JSON 解析失败）→ 跳过该条目，返回 None"""
        mock_redis_client.hgetall = AsyncMock(
            return_value={"corrupt": "not valid json {{{"}
        )

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_corrupted_result_entry(
        self, semantic_cache, mock_redis_client, sample_embedding
    ):
        """结果条目损坏 → 返回 None，不抛异常"""
        mock_redis_client.hgetall = AsyncMock(
            return_value={"abc123": _index_entry(sample_embedding)}
        )
        mock_redis_client.get = AsyncMock(return_value="not valid json {{{")

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_selects_best_match(self, semantic_cache, mock_redis_client, sample_result):
        """多个缓存条目 → 选择相似度最高的"""
        query_embedding = [1.0, 0.0, 0.0]

        mock_redis_client.hgetall = AsyncMock(return_value={
            "low_sim": _index_entry([0.3, 0.7, 0.6]),
            "high_sim": _index_entry([0.99, 0.01, 0.0]),
        })
        mock_redis_client.get = AsyncMock(
            side_effect=[None, _result_entry(sample_result, "高相似度查询")]
        )

        result = await semantic_cache.lookup("测试查询", query_embedding)
        assert result is not None
        assert result["confidence"] == sample_result["confidence"]
        # 应 GET 相似度最高的 field 对应的结果 key（第 2 次 GET）
        get_key = mock_redis_client.get.await_args_list[1].args[0]
        assert get_key.endswith("high_sim")

    @pytest.mark.asyncio
    async def test_lookup_dimension_mismatch_skipped(
        self, semantic_cache, mock_redis_client, sample_embedding
    ):
        """索引中维度不匹配的向量应被跳过"""
        mock_redis_client.hgetall = AsyncMock(return_value={
            "wrong_dim": _index_entry([1.0, 0.0]),  # 2 维 vs 查询 3 维
        })

        result = await semantic_cache.lookup("测试查询", sample_embedding)
        assert result is None
        # 仅 1 次版本号 GET，未触发结果 GET
        assert mock_redis_client.get.await_count == 1


# ===========================================================================
# 3. store 测试
# ===========================================================================

class TestStore:
    """测试语义缓存存储（v2：pipeline 写索引 + 结果）"""

    @pytest.mark.asyncio
    async def test_store_writes_index_and_result(
        self, semantic_cache, mock_redis_client, sample_embedding, sample_result
    ):
        """store 通过 pipeline 写入索引 field + 结果 key（带 TTL）"""
        pipe = _make_pipe()
        mock_redis_client.pipeline = MagicMock(return_value=pipe)

        await semantic_cache.store("测试查询", sample_embedding, sample_result)

        # 索引写入
        pipe.hset.assert_called_once()
        hset_args = pipe.hset.call_args.args
        assert hset_args[0] == "scqa:semantic_cache:index"
        index_data = json.loads(hset_args[2])
        assert index_data["e"] == sample_embedding
        assert "ts" in index_data

        # 结果写入（带 TTL）
        pipe.set.assert_called_once()
        set_args = pipe.set.call_args
        assert set_args.args[0].startswith("scqa:semantic_cache:")
        stored = json.loads(set_args.args[1])
        assert stored["query_text"] == "测试查询"
        assert stored["cached_result"]["confidence"] == sample_result["confidence"]
        assert set_args.kwargs["ex"] == 600

        pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_evicts_oldest_when_over_capacity(
        self, semantic_cache, mock_redis_client, sample_embedding, sample_result
    ):
        """索引超上限 → 按 ts 淘汰最旧条目"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = True
            mock_settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD = 0.92
            mock_settings.SEMANTIC_CACHE_TTL = 600
            mock_settings.SEMANTIC_CACHE_MAX_ENTRIES = 2

            write_pipe = _make_pipe()
            evict_pipe = _make_pipe()
            mock_redis_client.pipeline = MagicMock(side_effect=[write_pipe, evict_pipe])
            mock_redis_client.hlen = AsyncMock(return_value=3)
            mock_redis_client.hgetall = AsyncMock(return_value={
                "oldest": json.dumps({"e": [1.0], "ts": 100.0}),
                "mid": json.dumps({"e": [1.0], "ts": 200.0}),
                "newest": json.dumps({"e": [1.0], "ts": 300.0}),
            })

            await semantic_cache.store("测试查询", sample_embedding, sample_result)

            evict_pipe.hdel.assert_called_once()
            hdel_args = evict_pipe.hdel.call_args.args
            assert "oldest" in hdel_args
            assert "newest" not in hdel_args

    @pytest.mark.asyncio
    async def test_store_redis_unavailable(self, sample_embedding, sample_result):
        """Redis 不可用 → 不抛异常（优雅降级）"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = True

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()
            cache._get_redis_client = MagicMock(return_value=None)

            # 不应抛异常
            await cache.store("测试查询", sample_embedding, sample_result)

    @pytest.mark.asyncio
    async def test_store_disabled(self, sample_embedding, sample_result):
        """SEMANTIC_CACHE_ENABLED=False → 不写入"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = False

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()

            # 不应抛异常
            await cache.store("测试查询", sample_embedding, sample_result)

    @pytest.mark.asyncio
    async def test_store_exception_graceful(
        self, semantic_cache, mock_redis_client, sample_embedding, sample_result
    ):
        """pipeline execute 抛异常 → 不冒泡（优雅降级）"""
        pipe = _make_pipe()
        pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis_client.pipeline = MagicMock(return_value=pipe)

        # 不应抛异常
        await semantic_cache.store("测试查询", sample_embedding, sample_result)


# ===========================================================================
# 4. invalidate / purge 测试
# ===========================================================================

class TestInvalidate:
    """测试语义缓存失效（v3：INCR 版本号，O(1)）"""

    @pytest.mark.asyncio
    async def test_invalidate_incrs_version(self, semantic_cache, mock_redis_client):
        """invalidate 只需 INCR 版本号，不再 SCAN 全清"""
        await semantic_cache.invalidate()

        mock_redis_client.incr.assert_awaited_once_with("scqa:kb:version")
        mock_redis_client.scan.assert_not_awaited()
        mock_redis_client.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalidate_redis_unavailable(self):
        """Redis 不可用 → 不抛异常"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = True

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()
            cache._get_redis_client = MagicMock(return_value=None)

            # 不应抛异常
            await cache.invalidate()

    @pytest.mark.asyncio
    async def test_invalidate_incr_exception_graceful(self, semantic_cache, mock_redis_client):
        """INCR 抛异常 → 不冒泡（优雅降级）"""
        mock_redis_client.incr = AsyncMock(side_effect=ConnectionError("Redis down"))
        await semantic_cache.invalidate()


class TestPurge:
    """测试物理清除（SCAN 全清兜底）"""

    @pytest.mark.asyncio
    async def test_purge_clears_entries(self, semantic_cache, mock_redis_client):
        """purge 删除所有语义缓存 key（含索引 key）"""
        mock_redis_client.scan = AsyncMock(
            return_value=(0, [
                "scqa:semantic_cache:k1",
                "scqa:semantic_cache:k2",
                "scqa:semantic_cache:index",
            ])
        )

        await semantic_cache.purge()

        mock_redis_client.delete.assert_awaited_once_with(
            "scqa:semantic_cache:k1",
            "scqa:semantic_cache:k2",
            "scqa:semantic_cache:index",
        )

    @pytest.mark.asyncio
    async def test_purge_redis_unavailable(self):
        """Redis 不可用 → 不抛异常"""
        with patch("app.core.semantic_cache.settings") as mock_settings:
            mock_settings.SEMANTIC_CACHE_ENABLED = True

            from app.core.semantic_cache import SemanticCache
            cache = SemanticCache()
            cache._get_redis_client = MagicMock(return_value=None)

            # 不应抛异常
            await cache.purge()

    @pytest.mark.asyncio
    async def test_purge_empty_cache(self, semantic_cache, mock_redis_client):
        """没有缓存条目 → delete 不被调用"""
        mock_redis_client.scan = AsyncMock(return_value=(0, []))

        await semantic_cache.purge()

        mock_redis_client.delete.assert_not_awaited()


# ===========================================================================
# 4.5 版本号失效机制测试
# ===========================================================================

class TestVersionInvalidation:
    """知识库版本号 epoch 失效：stale 条目跳过 + 惰性清理"""

    @pytest.mark.asyncio
    async def test_store_writes_current_version(
        self, semantic_cache, mock_redis_client, sample_embedding, sample_result
    ):
        """store 把当前版本号写入索引条目和结果条目"""
        mock_redis_client.get = AsyncMock(return_value="3")  # 当前版本 3
        pipe = _make_pipe()
        mock_redis_client.pipeline = MagicMock(return_value=pipe)

        await semantic_cache.store("测试查询", sample_embedding, sample_result)

        index_data = json.loads(pipe.hset.call_args.args[2])
        assert index_data["v"] == 3
        stored = json.loads(pipe.set.call_args.args[1])
        assert stored["v"] == 3

    @pytest.mark.asyncio
    async def test_lookup_skips_stale_version_entries(
        self, semantic_cache, mock_redis_client, sample_embedding
    ):
        """invalidate（版本号递增）后，旧版本条目 lookup miss 并被惰性清理"""
        mock_redis_client.get = AsyncMock(return_value="2")  # 当前版本 2
        mock_redis_client.hgetall = AsyncMock(return_value={
            "old_entry": _index_entry(sample_embedding, version=1),  # 旧版本
        })
        cleanup_pipe = _make_pipe()
        mock_redis_client.pipeline = MagicMock(return_value=cleanup_pipe)

        result = await semantic_cache.lookup("测试查询", sample_embedding)

        assert result is None
        # 惰性清理：HDEL 索引 field + DEL 结果 key
        cleanup_pipe.hdel.assert_called_once()
        assert "old_entry" in cleanup_pipe.hdel.call_args.args
        cleanup_pipe.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_lookup_hits_same_version_entry(
        self, semantic_cache, mock_redis_client, sample_embedding, sample_result
    ):
        """版本号一致的条目正常命中"""
        mock_redis_client.get = AsyncMock(
            side_effect=["2", _result_entry(sample_result, version=2)]
        )
        mock_redis_client.hgetall = AsyncMock(return_value={
            "cur_entry": _index_entry(sample_embedding, version=2),
        })

        result = await semantic_cache.lookup("测试查询", sample_embedding)

        assert result is not None
        assert result["confidence"] == sample_result["confidence"]

    @pytest.mark.asyncio
    async def test_mixed_versions_only_current_matched(
        self, semantic_cache, mock_redis_client, sample_result
    ):
        """新旧版本混合：只有当前版本条目参与匹配"""
        query_embedding = [1.0, 0.0, 0.0]
        mock_redis_client.get = AsyncMock(
            side_effect=["5", _result_entry(sample_result, version=5)]
        )
        mock_redis_client.hgetall = AsyncMock(return_value={
            # 旧版本条目与 query 完全相同（若不过滤会优先命中）
            "stale_exact": _index_entry([1.0, 0.0, 0.0], version=4),
            "cur_close": _index_entry([0.99, 0.01, 0.0], version=5),
        })
        cleanup_pipe = _make_pipe()
        mock_redis_client.pipeline = MagicMock(return_value=cleanup_pipe)

        result = await semantic_cache.lookup("测试查询", query_embedding)

        assert result is not None
        # 命中的应是当前版本的 cur_close，而非旧版本的 stale_exact
        get_key = mock_redis_client.get.await_args_list[1].args[0]
        assert get_key.endswith("cur_close")


# ===========================================================================
# 5. _make_key / _make_field 测试
# ===========================================================================

class TestMakeKey:
    """测试 Redis key 生成"""

    def test_key_format(self):
        """key 格式: scqa:semantic_cache:{md5_hash}"""
        from app.core.semantic_cache import SemanticCache
        key = SemanticCache._make_key("测试查询")
        assert key.startswith("scqa:semantic_cache:")
        # MD5 hash 部分应为 32 位十六进制
        hash_part = key.split(":")[-1]
        assert len(hash_part) == 32
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_same_query_same_key(self):
        """相同查询 → 相同 key"""
        from app.core.semantic_cache import SemanticCache
        key1 = SemanticCache._make_key("库存查询")
        key2 = SemanticCache._make_key("库存查询")
        assert key1 == key2

    def test_different_query_different_key(self):
        """不同查询 → 不同 key"""
        from app.core.semantic_cache import SemanticCache
        key1 = SemanticCache._make_key("库存查询")
        key2 = SemanticCache._make_key("查一下库存")
        assert key1 != key2

    def test_field_matches_key_hash(self):
        """_make_field 与 _make_key 的 hash 部分一致（索引与结果 key 对应）"""
        from app.core.semantic_cache import SemanticCache
        field = SemanticCache._make_field("库存查询")
        key = SemanticCache._make_key("库存查询")
        assert key.endswith(field)


# ===========================================================================
# 6. 配置集成测试
# ===========================================================================

class TestConfigIntegration:
    """测试配置项集成"""

    def test_settings_have_semantic_cache_fields(self):
        """Settings 类包含语义缓存相关字段"""
        from app.config import get_settings
        s = get_settings()
        assert hasattr(s, "SEMANTIC_CACHE_ENABLED")
        assert hasattr(s, "SEMANTIC_CACHE_SIMILARITY_THRESHOLD")
        assert hasattr(s, "SEMANTIC_CACHE_TTL")
        assert hasattr(s, "SEMANTIC_CACHE_MAX_ENTRIES")

    def test_settings_defaults(self):
        """语义缓存配置有合理的默认值"""
        from app.config import get_settings
        s = get_settings()
        assert s.SEMANTIC_CACHE_ENABLED is True
        assert s.SEMANTIC_CACHE_SIMILARITY_THRESHOLD == 0.92
        assert s.SEMANTIC_CACHE_TTL == 600
        assert s.SEMANTIC_CACHE_MAX_ENTRIES == 200

    def test_module_singleton_exists(self):
        """模块级单例 semantic_cache 存在（供 cache_manager 委托）"""
        from app.core.semantic_cache import semantic_cache, SemanticCache
        assert isinstance(semantic_cache, SemanticCache)
