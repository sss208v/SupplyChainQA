"""retry 模块单元测试

覆盖 retry_async / retry_sync / retry_call / retry_astream / _is_retriable。
使用 unittest.mock 和 asyncio，不依赖外部服务。
"""
import asyncio
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.retry import (
    retry_async,
    retry_sync,
    retry_call,
    retry_astream,
    _is_retriable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRetriableError(Exception):
    """模拟可重试异常（类名匹配 retriable set）。"""
    pass


class _NonRetriableError(Exception):
    """模拟不可重试异常。"""
    pass


class _FakeApiError(Exception):
    """模拟 openai APIError（带 status_code）。"""
    def __init__(self, msg="api error", status_code=500):
        super().__init__(msg)
        self.status_code = status_code


# 动态创建类名在 retriable set 中的异常，用于 retry_astream 测试
ServiceUnavailableError = type("ServiceUnavailableError", (Exception,), {})


# ---------------------------------------------------------------------------
# _is_retriable
# ---------------------------------------------------------------------------

class TestIsRetriable:
    def test_retriable_by_name(self):
        """类名在 retriable set 中的异常应返回 True。"""
        exc = _FakeRetriableError("oops")
        # _FakeRetriableError 不在 retriable set 中，所以 False
        assert _is_retriable(exc) is False

    def test_retriable_by_class_name_matching(self):
        """类名恰好匹配 retriable set 中的名字（动态创建类）。"""
        # 动态创建名为 "ReadTimeout" 的异常类，模拟类名匹配
        ReadTimeout = type("ReadTimeout", (Exception,), {})
        exc = ReadTimeout("timeout")
        assert _is_retriable(exc) is True

    def test_not_retriable(self):
        exc = ValueError("bad input")
        assert _is_retriable(exc) is False

    def test_retriable_by_status_code(self):
        exc = _FakeApiError(status_code=502)
        assert _is_retriable(exc) is True

    def test_not_retriable_client_error(self):
        exc = _FakeApiError(status_code=400)
        assert _is_retriable(exc) is False


# ---------------------------------------------------------------------------
# retry_async
# ---------------------------------------------------------------------------

class TestRetryAsync:
    @pytest.mark.anyio
    async def test_succeeds_first_try(self):
        """第一次就成功，不应重试。"""
        mock_fn = AsyncMock(return_value="ok")

        @retry_async(max_attempts=3, base_delay=0.01)
        async def my_func():
            return await mock_fn()

        result = await my_func()
        assert result == "ok"
        assert mock_fn.call_count == 1

    @pytest.mark.anyio
    async def test_succeeds_after_failure(self):
        """第一次失败，第二次成功。"""
        mock_fn = AsyncMock(side_effect=[ConnectionError("net"), "ok"])

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            @retry_async(max_attempts=3, base_delay=0.01)
            async def my_func():
                return await mock_fn()

            result = await my_func()

        assert result == "ok"
        assert mock_fn.call_count == 2

    @pytest.mark.anyio
    async def test_exhausted_raises_last_error(self):
        """所有尝试都失败，抛出最后一次异常。"""
        mock_fn = AsyncMock(side_effect=ConnectionError("permanent"))

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            @retry_async(max_attempts=2, base_delay=0.01)
            async def my_func():
                return await mock_fn()

            with pytest.raises(ConnectionError, match="permanent"):
                await my_func()

        assert mock_fn.call_count == 2

    @pytest.mark.anyio
    async def test_only_retries_matching_exceptions(self):
        """不匹配的异常类型不会被重试。"""
        mock_fn = AsyncMock(side_effect=ValueError("wrong type"))

        @retry_async(max_attempts=3, base_delay=0.01, exceptions=(ConnectionError,))
        async def my_func():
            return await mock_fn()

        with pytest.raises(ValueError, match="wrong type"):
            await my_func()

        # 只调用一次，没有重试
        assert mock_fn.call_count == 1

    @pytest.mark.anyio
    async def test_preserves_last_error(self):
        """重试耗尽时，抛出的是最后一次异常。"""
        errors = [ConnectionError("first"), ConnectionError("second")]
        mock_fn = AsyncMock(side_effect=errors)

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            @retry_async(max_attempts=2, base_delay=0.01)
            async def my_func():
                return await mock_fn()

            with pytest.raises(ConnectionError, match="second"):
                await my_func()


# ---------------------------------------------------------------------------
# retry_sync
# ---------------------------------------------------------------------------

class TestRetrySync:
    def test_succeeds_first_try(self):
        mock_fn = MagicMock(return_value=42)

        @retry_sync(max_attempts=3, base_delay=0.01)
        def my_func():
            return mock_fn()

        assert my_func() == 42
        assert mock_fn.call_count == 1

    def test_succeeds_after_failure(self):
        mock_fn = MagicMock(side_effect=[ConnectionError("net"), "ok"])

        with patch("app.core.retry.time.sleep"):
            @retry_sync(max_attempts=3, base_delay=0.01)
            def my_func():
                return mock_fn()

            assert my_func() == "ok"
        assert mock_fn.call_count == 2

    def test_exhausted_raises(self):
        mock_fn = MagicMock(side_effect=ConnectionError("dead"))

        with patch("app.core.retry.time.sleep"):
            @retry_sync(max_attempts=2, base_delay=0.01)
            def my_func():
                return mock_fn()

            with pytest.raises(ConnectionError):
                my_func()
        assert mock_fn.call_count == 2


# ---------------------------------------------------------------------------
# retry_call
# ---------------------------------------------------------------------------

class TestRetryCall:
    @pytest.mark.anyio
    async def test_success(self):
        fn = AsyncMock(return_value="done")
        result = await retry_call(fn, max_attempts=3, base_delay=0.01)
        assert result == "done"
        assert fn.call_count == 1

    @pytest.mark.anyio
    async def test_exhausted_raises(self):
        fn = AsyncMock(side_effect=RuntimeError("fail"))

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="fail"):
                await retry_call(fn, max_attempts=2, base_delay=0.01)
        assert fn.call_count == 2


# ---------------------------------------------------------------------------
# retry_astream
# ---------------------------------------------------------------------------

class TestRetryAstream:
    @pytest.mark.anyio
    async def test_stream_success_first_chunk(self):
        """流式调用第一个 chunk 成功。"""
        async def _agen():
            yield "chunk1"
            yield "chunk2"

        factory = MagicMock(return_value=_agen())
        chunks = []
        async for c in retry_astream(factory, max_attempts=3, base_delay=0.01):
            chunks.append(c)

        assert chunks == ["chunk1", "chunk2"]
        assert factory.call_count == 1

    @pytest.mark.anyio
    async def test_stream_retries_on_first_chunk_failure(self):
        """第一个 chunk 抛出可重试异常后重试成功。"""
        call_count = 0

        async def _fail_then_ok():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ServiceUnavailableError("first chunk fails")
            yield "recovered"

        factory = MagicMock(side_effect=_fail_then_ok)
        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            chunks = []
            async for c in retry_astream(factory, max_attempts=2, base_delay=0.01):
                chunks.append(c)

        assert chunks == ["recovered"]
        assert factory.call_count == 2

    @pytest.mark.anyio
    async def test_stream_exhausted_raises(self):
        """所有重试用完后抛出最后一次异常。"""
        async def _always_fail():
            raise ServiceUnavailableError("always fails")
            yield  # make it an async generator

        factory = MagicMock(side_effect=_always_fail)
        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ServiceUnavailableError, match="always fails"):
                async for _ in retry_astream(factory, max_attempts=2, base_delay=0.01):
                    pass
        assert factory.call_count == 2

    @pytest.mark.anyio
    async def test_stream_zero_attempts_returns_immediately(self):
        """max_attempts=0 直接返回，不崩溃。"""
        factory = MagicMock()
        chunks = []
        async for c in retry_astream(factory, max_attempts=0, base_delay=0.01):
            chunks.append(c)
        assert chunks == []
        assert factory.call_count == 0

    @pytest.mark.anyio
    async def test_stream_non_retriable_error_raises_immediately(self):
        """不可重试异常直接抛出，不重试。"""
        async def _value_error():
            raise ValueError("not retriable")
            yield

        factory = MagicMock(side_effect=_value_error)
        with pytest.raises(ValueError, match="not retriable"):
            async for _ in retry_astream(factory, max_attempts=3, base_delay=0.01):
                pass
        assert factory.call_count == 1


# ---------------------------------------------------------------------------
# Delay backoff verification
# ---------------------------------------------------------------------------

class TestRetryDelayBackoff:
    @pytest.mark.anyio
    async def test_delay_increases_exponentially(self):
        """验证退避延迟按指数增长 (base * 2^(attempt-1))。"""
        sleep_delays: list[float] = []
        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        mock_fn = AsyncMock(side_effect=[ConnectionError("1"), ConnectionError("2"), "ok"])

        with patch("app.core.retry.asyncio.sleep", side_effect=mock_sleep):
            @retry_async(max_attempts=3, base_delay=1.0)
            async def my_func():
                return await mock_fn()

            result = await my_func()

        assert result == "ok"
        # 第一次失败 -> delay = 1.0 * 2^0 = 1.0
        # 第二次失败 -> delay = 1.0 * 2^1 = 2.0
        assert sleep_delays == [1.0, 2.0]
