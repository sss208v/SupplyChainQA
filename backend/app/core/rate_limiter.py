"""
SupplyChainRAG - 滑动窗口限流中间件

基于 Redis Sorted Set 的滑动窗口限流，按端点分级配置阈值。
Redis 不可用时自动降级为内存计数（单进程有效）。
"""
import hashlib
import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


# ---- 端点分级限流配置（每分钟请求数）----
_ENDPOINT_LIMITS: dict[str, int] = {
    "/api/v1/chat/stream": 20,
    "/api/v1/chat/completions": 20,
    "/api/v1/auth/login": 30,
    "/api/v1/auth/register": 10,
    "/api/v1/knowledge/upload": 10,
}
_DEFAULT_LIMIT = 60
_WINDOW_SECONDS = 60


def _classify_endpoint(path: str) -> int:
    """根据路径前缀匹配限流阈值。"""
    for prefix, limit in _ENDPOINT_LIMITS.items():
        if path.startswith(prefix):
            return limit
    return _DEFAULT_LIMIT


def _client_key(request: Request) -> str:
    """提取客户端标识：优先用 token（SHA-256 哈希），其次用 IP。"""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 7:
        token_hash = hashlib.sha256(auth[7:].encode()).hexdigest()[:16]
        return f"rl:token:{token_hash}"
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return f"rl:ip:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI 限流中间件，支持 Redis 滑动窗口 + 内存降级。"""

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client
        self._memory_store: dict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # OPTIONS 预检请求不限流
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        limit = _classify_endpoint(path)
        key = _client_key(request)
        now = time.time()
        window_start = now - _WINDOW_SECONDS

        allowed, remaining, retry_after = await self._check_rate(key, now, window_start, limit)

        if not allowed:
            return Response(
                content='{"detail":"请求过于频繁，请稍后重试"}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining - 1))
        return response

    async def _check_rate(
        self, key: str, now: float, window_start: float, limit: int
    ) -> tuple[bool, int, int]:
        """检查是否超过限流阈值，返回 (allowed, remaining, retry_after)。"""
        # 优先用 Redis
        if self._redis and self._redis.is_connected:
            return await self._check_rate_redis(key, now, window_start, limit)
        # 降级到内存
        return self._check_rate_memory(key, now, window_start, limit)

    async def _check_rate_redis(
        self, key: str, now: float, window_start: float, limit: int
    ) -> tuple[bool, int, int]:
        """Redis Sorted Set 滑动窗口。"""
        try:
            # member 加随机后缀，避免多 worker 同一时间戳互相覆盖导致少计数
            member = f"{now}:{uuid.uuid4().hex[:8]}"
            pipe = self._redis.client.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            pipe.expire(key, _WINDOW_SECONDS + 10)
            results = await pipe.execute()
            count = results[2]
            oldest = results[3]
            if count > limit:
                # 超限时移除本次写入的 member：被拒请求不占窗口（与内存路径语义对齐，
                # 否则重试会不断填满窗口导致锁死状态被持续续期）
                await self._redis.client.zrem(key, member)
                # Retry-After = 窗口内最老记录滑出所需时间
                oldest_ts = oldest[0][1] if oldest else now
                retry_after = max(1, int(oldest_ts + _WINDOW_SECONDS - now) + 1)
                return False, 0, retry_after
            return True, limit - count, 0
        except Exception as e:
            logger.warning(f"Redis 限流检查失败，降级内存: {e}")
            return self._check_rate_memory(key, now, window_start, limit)

    def _check_rate_memory(
        self, key: str, now: float, window_start: float, limit: int
    ) -> tuple[bool, int, int]:
        """内存滑动窗口（单进程降级）。"""
        timestamps = self._memory_store[key]
        # 清除窗口外的记录（in-place 操作 defaultdict 内部列表）
        timestamps[:] = [t for t in timestamps if t > window_start]
        count = len(timestamps)
        if count >= limit:
            # 从最老记录推算重试等待时间
            retry_after = max(1, int(timestamps[0] + _WINDOW_SECONDS - now) + 1)
            return False, 0, retry_after
        # 记录本次请求（旧实现在空窗口时不记录，导致限流失效）
        timestamps.append(now)
        return True, limit - count - 1, 0
