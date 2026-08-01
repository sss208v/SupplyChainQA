"""
SupplyChainRAG - 重试装饰器

外部 API 调用（DeepSeek/MiniMax/Milvus）可能临时失败。
指数退避重试是最简单的容错策略。

我用指数退避（2s→4s→8s），最多3次，第3次失败才返回降级回答。
这和 Hermes Agent 的 retry 模式一致。

Async Generator Retry（REQ-1 新增）：
  流式调用 astream 不能简单用装饰器——需要特殊处理：
  1. 只在「第一个 chunk 到达之前」重试
  2. 如果已经有内容流出了，不重试（避免重复输出）
  3. 全部失败后抛异常，由调用方决定降级策略
"""
import asyncio
import logging
import time
from functools import wraps
from typing import AsyncIterator, Callable

logger = logging.getLogger(__name__)

# 可重试的网络异常类型（openai / httpx 底层）
try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        APIError,
        RateLimitError,
    )
    _RETRIABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError, APIError)
except ImportError:
    _RETRIABLE_EXCEPTIONS = (Exception,)

# gRPC 相关异常（用于 Milvus retry）
try:
    import grpc
    _GRPC_RETRIABLE = (grpc.RpcError,)
except ImportError:
    _GRPC_RETRIABLE = ()


def _is_retriable(exception: Exception) -> bool:
    """判断异常是否可重试（非客户端错误）"""
    # 用类名匹配兜底，避免依赖缺失导致漏判
    name = type(exception).__name__
    retriable_names = {
        'APIConnectionError', 'APITimeoutError', 'APIError',
        'RateLimitError', 'InternalServerError', 'ServiceUnavailableError',
        'ReadTimeout', 'ConnectTimeout', 'ConnectError', 'RemoteProtocolError',
        'RpcError', 'StatusCode',
    }
    if name in retriable_names:
        return True
    # 检查 5xx HTTP 状态码
    if hasattr(exception, 'status_code') and 500 <= getattr(exception, 'status_code', 0) < 600:
        return True
    # 检查异常链中是否有可重试的类型
    if isinstance(exception, _RETRIABLE_EXCEPTIONS):
        return True
    if _GRPC_RETRIABLE and isinstance(exception, _GRPC_RETRIABLE):
        return True
    return False


async def _log_and_backoff_async(context_name, attempt, max_attempts, base_delay, e, include_type=True, with_count=True):
    """记录重试失败日志并异步指数退避等待。"""
    detail = f"{type(e).__name__}: {e}" if include_type else str(e)
    if attempt < max_attempts:
        delay = base_delay * (2 ** (attempt - 1))
        logger.warning(
            f"[Retry] {context_name} 第{attempt}次失败: {detail}, "
            f"{delay:.1f}秒后重试..."
        )
        await asyncio.sleep(delay)
    else:
        suffix = f"({max_attempts})" if with_count else ""
        logger.error(
            f"[Retry] {context_name} 第{attempt}次失败: {detail}, "
            f"已达最大重试次数{suffix}"
        )


def _log_and_backoff_sync(context_name, attempt, max_attempts, base_delay, e, include_type=False, with_count=False):
    """记录重试失败日志并同步指数退避等待。"""
    detail = f"{type(e).__name__}: {e}" if include_type else str(e)
    if attempt < max_attempts:
        delay = base_delay * (2 ** (attempt - 1))
        logger.warning(
            f"[Retry] {context_name} 第{attempt}次失败: {detail}, "
            f"{delay:.1f}秒后重试..."
        )
        time.sleep(delay)
    else:
        suffix = f"({max_attempts})" if with_count else ""
        logger.error(
            f"[Retry] {context_name} 第{attempt}次失败: {detail}, 已达最大重试次数{suffix}"
        )


async def retry_astream(
    generator_factory: Callable[[], AsyncIterator],
    max_attempts: int = 3,
    base_delay: float = 2.0,
    context_name: str = "astream",
) -> AsyncIterator:
    """为 async generator 加 pre-first-chunk 指数退避重试

    流式调用不能简单用 @retry_async 装饰器包裹，因为：
    1. async generator 的异常处理需要特殊写法
    2. 如果第一个 chunk 已经 yield 出去了，重试会导致前端收到重复内容
    3. 所以只在「首次 chunk 到达之前」重试——之后任何异常都不重试

    用法:
        async for chunk in retry_astream(
            lambda: LLMFactory._raw_astream(messages),
            context_name="LLM astream"
        ):
            yield chunk

    Args:
        generator_factory: 无参函数，每次调用返回一个新的 async generator
        max_attempts: 最大尝试次数
        base_delay: 基础延迟（秒），每次翻倍
        context_name: 日志中的上下文名称
    """
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            gen = generator_factory()
            # 尝试拿第一个 chunk
            first_chunk = await gen.__anext__()
            # 成功——yield 第一个 chunk，然后继续 yield 剩余的
            yield first_chunk
            async for chunk in gen:
                yield chunk
            return  # 正常完成
        except StopAsyncIteration:
            # 空响应（没有 chunk），也算成功
            return
        except Exception as e:
            if not _is_retriable(e):
                logger.warning(f"[Retry] {context_name} 不可重试的错误: {type(e).__name__}: {e}")
                raise
            last_exception = e
            await _log_and_backoff_async(context_name, attempt, max_attempts, base_delay, e)
    if last_exception is not None:
        raise last_exception
    return  # max_attempts=0 时直接返回


def retry_async(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """异步重试装饰器（指数退避）

    Args:
        max_attempts: 最大尝试次数
        base_delay: 基础延迟（秒），每次翻倍
        exceptions: 需要重试的异常类型
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    await _log_and_backoff_async(
                        func.__name__, attempt, max_attempts, base_delay, e,
                        include_type=False, with_count=False,
                    )
            raise last_exception
        return wrapper
    return decorator


def retry_sync(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """同步重试装饰器（指数退避）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    _log_and_backoff_sync(func.__name__, attempt, max_attempts, base_delay, e)
            raise last_exception
        return wrapper
    return decorator


async def retry_call(
    func: Callable,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    context_name: str = "retry_call",
):
    """直接调用的异步重试（非装饰器），适用于 Circuit Breaker 等需要手动编排的场景。"""
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except Exception as e:
            last_exception = e
            await _log_and_backoff_async(context_name, attempt, max_attempts, base_delay, e)
    raise last_exception
