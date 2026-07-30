"""Circuit Breaker 单元测试"""
import asyncio
import pytest
from app.core.circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError


@pytest.mark.asyncio
async def test_closed_allows_requests():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED
    cb.check()  # should not raise


@pytest.mark.asyncio
async def test_closed_trips_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_rejects_requests():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        cb.check()


@pytest.mark.asyncio
async def test_open_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.check()  # should not raise in HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_success_closes_circuit():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_failure_reopens_circuit():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_success_resets_failure_count():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb._failure_count == 0
    # 2 more failures should NOT trip (count was reset)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_reset_full_state():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0
    cb.check()  # should not raise
