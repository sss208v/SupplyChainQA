"""Rate Limiter 单元测试"""
import time
import pytest
from unittest.mock import MagicMock, AsyncMock
from app.core.rate_limiter import (
    RateLimitMiddleware, _classify_endpoint, _client_key, _DEFAULT_LIMIT,
)


def test_classify_chat_endpoint():
    assert _classify_endpoint("/api/v1/chat/stream") == 20


def test_classify_auth_endpoint():
    assert _classify_endpoint("/api/v1/auth/login") == 30


def test_classify_unknown_endpoint():
    assert _classify_endpoint("/api/v1/unknown") == _DEFAULT_LIMIT


def test_client_key_with_token():
    request = MagicMock()
    request.headers = {"authorization": "Bearer abcdef1234567890"}
    request.client.host = "127.0.0.1"
    key = _client_key(request)
    assert key.startswith("rl:token:")


def test_client_key_without_token():
    request = MagicMock()
    request.headers = {}
    request.client.host = "192.168.1.1"
    key = _client_key(request)
    assert key.startswith("rl:ip:192.168.1.1")


def test_memory_rate_limit_allows_within_threshold():
    middleware = RateLimitMiddleware(app=MagicMock(), redis_client=None)
    now = time.time()
    window_start = now - 60

    for _ in range(5):
        allowed, remaining, retry_after = middleware._check_rate_memory("test:key", now, window_start, 10)
        assert allowed is True
        assert retry_after == 0

    # 6th request
    allowed, remaining, retry_after = middleware._check_rate_memory("test:key", now, window_start, 10)
    assert allowed is True


def test_memory_rate_limit_blocks_over_threshold():
    middleware = RateLimitMiddleware(app=MagicMock(), redis_client=None)
    now = time.time()
    window_start = now - 60

    for _ in range(10):
        middleware._check_rate_memory("test:key", now, window_start, 10)

    allowed, remaining, retry_after = middleware._check_rate_memory("test:key", now, window_start, 10)
    assert allowed is False
    assert remaining == 0
    # retry_after 从窗口内最老记录推算，应在 (0, 窗口+1] 内（旧实现恒为 1 是 bug）
    assert 1 <= retry_after <= 61


def test_memory_rate_limit_records_first_request():
    """空窗口的首个请求也必须被记录（旧实现不记录导致限流失效）"""
    middleware = RateLimitMiddleware(app=MagicMock(), redis_client=None)
    now = time.time()
    window_start = now - 60

    middleware._check_rate_memory("fresh:key", now, window_start, 10)
    assert len(middleware._memory_store["fresh:key"]) == 1


async def test_redis_rate_limit_rejected_request_not_counted():
    """Redis 路径超限时必须 zrem 本次写入：被拒请求不占窗口（旧实现会让重试不断续期锁死状态）"""
    now = time.time()
    redis_client = MagicMock()
    redis_client.is_connected = True
    pipe = MagicMock()
    # pipeline 链式调用均为同步 mock，execute 返回 [zremrangebyscore, zadd, zcard, zrange, expire]
    pipe.execute = AsyncMock(return_value=[0, 1, 11, [(b"m", now - 30)], True])
    redis_client.client.pipeline.return_value = pipe
    redis_client.client.zrem = AsyncMock()

    middleware = RateLimitMiddleware(app=MagicMock(), redis_client=redis_client)
    allowed, remaining, retry_after = await middleware._check_rate_redis("test:key", now, now - 60, 10)

    assert allowed is False
    assert remaining == 0
    redis_client.client.zrem.assert_awaited_once()
    # 最老记录在 30s 前，retry_after 应约为剩余窗口时长
    assert 1 <= retry_after <= 61


async def test_redis_rate_limit_allows_within_threshold():
    """Redis 路径未超限时正常放行且不调 zrem"""
    now = time.time()
    redis_client = MagicMock()
    redis_client.is_connected = True
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 1, 3, [(b"m", now - 10)], True])
    redis_client.client.pipeline.return_value = pipe
    redis_client.client.zrem = AsyncMock()

    middleware = RateLimitMiddleware(app=MagicMock(), redis_client=redis_client)
    allowed, remaining, retry_after = await middleware._check_rate_redis("test:key", now, now - 60, 10)

    assert allowed is True
    assert remaining == 7
    redis_client.client.zrem.assert_not_awaited()
