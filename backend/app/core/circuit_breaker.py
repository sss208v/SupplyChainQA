"""
SupplyChainRAG - 熔断器（Circuit Breaker）

三态熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED
用于保护外部 API 调用（DeepSeek / MiniMax），防止级联超时。

状态说明：
  CLOSED   — 正常放行，连续失败计数；达到阈值后跳到 OPEN
  OPEN     — 直接拒绝请求，等待冷却时间后跳到 HALF_OPEN
  HALF_OPEN — 放行一次探测请求；成功回 CLOSED，失败回 OPEN
"""
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """熔断器打开时抛出，调用方应立即返回降级响应。"""

    def __init__(self, provider: str, remaining_seconds: float):
        self.provider = provider
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"Circuit breaker OPEN for '{provider}', "
            f"retry in {remaining_seconds:.0f}s"
        )


class CircuitBreaker:
    """纯 asyncio 实现的三态熔断器。"""

    def __init__(
        self,
        provider: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_probe_done = False

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        old = self._state
        self._state = new_state
        logger.info(
            "CircuitBreaker[%s] %s -> %s",
            self.provider, old.value, new_state.value,
        )

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.CLOSED)
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
            return

        if self._failure_count >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)

    def check(self) -> None:
        """在调用外部 API 前调用；熔断器打开时抛出 CircuitOpenError。"""
        current_state = self.state
        if current_state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            remaining = max(0.0, self.recovery_timeout - elapsed)
            raise CircuitOpenError(self.provider, remaining)

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0


# ---- 全局熔断器实例（按 provider 隔离）----
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    provider: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
) -> CircuitBreaker:
    if provider not in _breakers:
        _breakers[provider] = CircuitBreaker(
            provider=provider,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
    return _breakers[provider]
