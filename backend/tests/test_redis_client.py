"""Redis 客户端单元测试 — 之前 0 覆盖"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _RecordingPipeline:
    """记录 pipeline 调用序列，execute 按序返回模拟结果"""

    def __init__(self):
        self.ops = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return _record

    async def execute(self):
        return [1 if name == "incr" else True for name, _a, _k in self.ops]


def make_fake_redis():
    """创建一个可 await 的 fake Redis client（用 MagicMock + AsyncMock）"""
    fake = MagicMock()
    fake.set = AsyncMock(return_value=True)
    fake.get = AsyncMock(return_value=None)
    fake.delete = AsyncMock(return_value=1)
    fake.ping = AsyncMock(return_value=True)
    fake.aclose = AsyncMock()
    fake.eval = AsyncMock(return_value=1)
    fake.lrange = AsyncMock(return_value=[])
    fake.llen = AsyncMock(return_value=0)
    fake.rpush = AsyncMock(return_value=1)
    fake.lpush = AsyncMock(return_value=1)
    fake.ltrim = AsyncMock(return_value=True)
    fake.incr = AsyncMock(return_value=1)
    fake.expire = AsyncMock(return_value=True)
    fake.scan = AsyncMock(return_value=(0, []))
    fake.scan_iter = MagicMock(return_value=iter([]))
    fake.delete_many = AsyncMock(return_value=0)
    fake.pipeline = MagicMock(return_value=_RecordingPipeline())
    return fake


class TestRedisManagerConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, monkeypatch):
        from app.core import redis_client as rc_mod

        fake = make_fake_redis()
        with patch.object(rc_mod.aioredis, "from_url", return_value=fake):
            mgr = rc_mod.RedisManager()
            await mgr.connect()
            assert mgr._pool is fake
            assert mgr.is_connected is True
            fake.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_failure_sets_pool_none(self, monkeypatch):
        """连接失败时 _pool 应被设为 None（保证 is_connected=False）"""
        from app.core import redis_client as rc_mod

        fake = MagicMock()
        fake.ping = AsyncMock(side_effect=ConnectionRefusedError("conn refused"))
        with patch.object(rc_mod.aioredis, "from_url", return_value=fake):
            mgr = rc_mod.RedisManager()
            with pytest.raises(ConnectionRefusedError):
                await mgr.connect()
            assert mgr._pool is None
            assert mgr.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_when_connected(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake
        await mgr.disconnect()
        fake.aclose.assert_awaited_once()
        assert mgr._pool is None

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected_is_noop(self):
        """未连接时 disconnect 应静默 no-op"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        # _pool 是 None，不抛错
        await mgr.disconnect()

    def test_client_property_raises_when_not_connected(self):
        """未连接时访问 client 应抛 RuntimeError"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        with pytest.raises(RuntimeError, match="Redis未连接"):
            _ = mgr.client

    def test_client_property_returns_pool_when_connected(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake
        assert mgr.client is fake

    def test_is_connected_reflects_pool_state(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        assert mgr.is_connected is False
        mgr._pool = make_fake_redis()
        assert mgr.is_connected is True


class TestRedisManagerLock:
    @pytest.mark.asyncio
    async def test_acquire_lock_success_returns_token(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(return_value=True)  # SET NX 成功
        mgr._pool = fake

        token = await mgr.acquire_lock("lock:tool:t1:sess1", expire=10)
        assert isinstance(token, str) and len(token) == 32
        fake.set.assert_awaited_once()
        # 锁值应为 token（而非固定字符串）
        assert fake.set.await_args.args[1] == token

    @pytest.mark.asyncio
    async def test_acquire_lock_already_held(self):
        """锁已被占用（SET NX 返回 None）→ None"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(return_value=None)  # NX 失败
        mgr._pool = fake

        token = await mgr.acquire_lock("lock:already-held", expire=5)
        assert token is None

    @pytest.mark.asyncio
    async def test_acquire_lock_with_retry_eventually_succeeds(self):
        """retry_times=2：前 2 次失败、第 3 次成功"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(side_effect=[None, None, True])  # 前 2 失败，第 3 成功
        mgr._pool = fake

        token = await mgr.acquire_lock("lock:retry", expire=5, retry_times=2)
        assert token is not None
        assert fake.set.await_count == 3

    @pytest.mark.asyncio
    async def test_release_lock_own_token(self):
        """释放自己持有的锁 → Lua 返回 1 → True"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.eval = AsyncMock(return_value=1)
        mgr._pool = fake

        assert await mgr.release_lock("lock:key", "tok-1") is True
        fake.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_lock_not_owner(self):
        """锁已易主（token 不匹配）→ Lua 返回 0 → False，不误删他人的锁"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.eval = AsyncMock(return_value=0)
        mgr._pool = fake

        assert await mgr.release_lock("lock:key", "stale-token") is False
        fake.delete.assert_not_awaited()


class TestRedisManagerIdempotent:
    @pytest.mark.asyncio
    async def test_try_begin_acquired(self):
        """SET NX 成功 → 'acquired'（本请求获得执行权）"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(return_value=True)
        mgr._pool = fake

        assert await mgr.try_begin_idempotent("idem:key") == "acquired"

    @pytest.mark.asyncio
    async def test_try_begin_pending(self):
        """键已存在且值为 pending → 'pending'（正在处理中）"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(return_value=None)
        fake.get = AsyncMock(return_value="pending")
        mgr._pool = fake

        assert await mgr.try_begin_idempotent("idem:pending") == "pending"

    @pytest.mark.asyncio
    async def test_try_begin_completed(self):
        """键已存在且值为 completed → 'completed'（拦截重复提交）"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        fake.set = AsyncMock(return_value=None)
        fake.get = AsyncMock(return_value="completed")
        mgr._pool = fake

        assert await mgr.try_begin_idempotent("idem:done") == "completed"

    @pytest.mark.asyncio
    async def test_concurrent_only_one_acquires(self):
        """10 个并发请求只有 1 个拿到 acquired，其余均为 pending"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        store = {}

        async def fake_set(key, value, nx=None, ex=None):
            if nx and key in store:
                return None
            store[key] = value
            return True

        async def fake_get(key):
            return store.get(key)

        fake = MagicMock()
        fake.set = AsyncMock(side_effect=fake_set)
        fake.get = AsyncMock(side_effect=fake_get)
        mgr._pool = fake

        results = await asyncio.gather(*[
            mgr.try_begin_idempotent("idem:concurrent") for _ in range(10)
        ])
        assert results.count("acquired") == 1
        assert results.count("pending") == 9

    @pytest.mark.asyncio
    async def test_mark_idempotent_sets_ttl(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake

        await mgr.mark_idempotent("idem:key", ttl=600)
        fake.set.assert_awaited_once_with("idem:key", "completed", ex=600)

    @pytest.mark.asyncio
    async def test_cancel_idempotent_deletes_key(self):
        """执行失败后撤销 pending 标记，允许重试"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake

        await mgr.cancel_idempotent("idem:key")
        fake.delete.assert_awaited_once_with("idem:key")


class TestEnsureConnected:
    @pytest.mark.asyncio
    async def test_returns_true_when_connected(self):
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        mgr._pool = make_fake_redis()
        assert await mgr.ensure_connected() is True

    @pytest.mark.asyncio
    async def test_reconnect_failure_throttled(self):
        """重连失败后节流期内不再重试 connect"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        mgr.connect = AsyncMock(side_effect=ConnectionError("down"))

        assert await mgr.ensure_connected() is False
        assert await mgr.ensure_connected() is False  # 节流：不触发第二次 connect
        mgr.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_after_interval_succeeds(self):
        """超过节流间隔后重新尝试并成功"""
        from app.core import redis_client as rc_mod
        mgr = rc_mod.RedisManager()
        fake = make_fake_redis()

        async def _ok():
            mgr._pool = fake

        mgr.connect = AsyncMock(side_effect=_ok)
        mgr._last_connect_attempt = -999999.0  # 模拟节流窗口已过
        assert await mgr.ensure_connected() is True
        assert mgr._pool is fake


class TestChatMemory:
    def test_key_without_user(self):
        """匿名会话落入 anon 前缀空间（与登录用户 key 隔离，防会话窃取）"""
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        assert mem._key("sess1") == "scqa:chat:anon:sess1"

    def test_key_with_user(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        assert mem._key("sess1", "u1") == "scqa:chat:u1:sess1"

    def test_key_isolation_between_users(self):
        """同一 session_id 不同用户 → 不同 key（用户 B 拿着 A 的 session_id 读不到 A 的历史）"""
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        key_a = mem._key("shared-sess", "user_a")
        key_b = mem._key("shared-sess", "user_b")
        key_anon = mem._key("shared-sess")
        assert len({key_a, key_b, key_anon}) == 3

    def test_summary_key_without_user(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        assert mem._summary_key("sess1") == "scqa:chat_summary:anon:sess1"

    def test_summary_key_with_user(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        assert mem._summary_key("sess1", "u1") == "scqa:chat_summary:u1:sess1"

    def test_count_key(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mem = ChatMemory(RedisManager())
        assert mem._count_key("sess1") == "scqa:chat_count:anon:sess1"

    @pytest.mark.asyncio
    async def test_add_message_uses_single_pipeline(self):
        """add_message 应把 lpush+ltrim+expire+incr+expire 合并进单次 pipeline"""
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake
        mem = ChatMemory(mgr)

        await mem.add_message("sess1", "user", "你好", user_id="u1")
        fake.pipeline.assert_called_once()
        op_names = [name for name, _a, _k in fake.pipeline.return_value.ops]
        assert op_names == ["lpush", "ltrim", "expire", "incr", "expire"]
        # 单条消息未达摘要阈值，不应重置计数
        fake.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_message_skips_when_redis_down(self):
        """Redis 不可用时 add_message 应降级跳过而非报错"""
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        mgr.connect = AsyncMock(side_effect=ConnectionError("down"))
        mem = ChatMemory(mgr)

        # 不抛异常，静默降级
        await mem.add_message("sess1", "user", "你好")

    @pytest.mark.asyncio
    async def test_get_session_list_parses_user_scoped_keys(self):
        """带 user_id 的键 scqa:chat:{user_id}:{session_id} 应只返回 session_id"""
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        fake.scan = AsyncMock(return_value=(0, [
            "scqa:chat:u1:sess-a",
            "scqa:chat:sess-b",
            "scqa:chat_summary:u1:sess-a",  # 应被跳过
        ]))
        mgr._pool = fake
        mem = ChatMemory(mgr)

        sessions = await mem.get_session_list()
        assert set(sessions) == {"sess-a", "sess-b"}

    @pytest.mark.asyncio
    async def test_get_messages_empty_session(self):
        """空 session 应返回空列表"""
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        fake.lrange = AsyncMock(return_value=[])
        mgr._pool = fake
        mem = ChatMemory(mgr)

        result = await mem.get_messages("sess1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_messages_parses_json_with_reverse(self):
        """lpush 导致 lrange 返回倒序，get_messages 应 reversed() 还原正序"""
        import json
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        # lrange 返回的顺序（lpush 后倒序）：最新的在前
        msgs = [
            json.dumps({"role": "assistant", "content": "你好！"}),  # 最新
            json.dumps({"role": "user", "content": "你好"}),  # 最早
        ]
        fake.lrange = AsyncMock(return_value=msgs)
        mgr._pool = fake
        mem = ChatMemory(mgr)

        result = await mem.get_messages("sess1")
        # reversed 后：最早在前（正序）
        assert len(result) == 2
        assert result[0]["role"] == "user"  # 最早
        assert result[0]["content"] == "你好"
        assert result[1]["role"] == "assistant"  # 最新

    @pytest.mark.asyncio
    async def test_clear_session(self):
        """clear_session 应删除对话 + summary + count"""
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake
        mem = ChatMemory(mgr)

        await mem.clear_session("sess1", "u1")
        # 应调用 delete（实际可能是 delete 多个 key）
        assert fake.delete.await_count >= 1 or fake.delete_many.await_count >= 1

    @pytest.mark.asyncio
    async def test_save_and_get_summary(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        mgr._pool = fake
        mem = ChatMemory(mgr)

        await mem.save_summary("sess1", "对话摘要", "u1")
        fake.set.assert_awaited_once()

        # 验证 get_summary
        fake.get = AsyncMock(return_value="对话摘要")
        result = await mem.get_summary("sess1", "u1")
        assert result == "对话摘要"

    @pytest.mark.asyncio
    async def test_get_summary_returns_none_when_missing(self):
        from app.core.redis_client import ChatMemory, RedisManager
        mgr = RedisManager()
        fake = make_fake_redis()
        fake.get = AsyncMock(return_value=None)
        mgr._pool = fake
        mem = ChatMemory(mgr)

        result = await mem.get_summary("sess-missing")
        assert result is None
