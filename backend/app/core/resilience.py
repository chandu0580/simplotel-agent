"""Resilience primitives for third-party integrations: timeout, retry with backoff, circuit breaker.

Retries are only for idempotent reads. Mutations must not be retried blindly; they rely on
idempotency keys instead (see reservations/idempotency.py).
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
import contextvars
import random
import threading
import time
from typing import TypeVar

T = TypeVar("T")

# Separate pools: a tool running in TOOL_EXECUTOR may itself call an integration. Sharing one pool
# could deadlock under load, with every worker waiting on an inner call that can't get a worker.
INTEGRATION_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="integration")
TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="tool")


class CircuitOpenError(RuntimeError):
    pass


class IntegrationTimeout(TimeoutError):
    pass


def call_with_timeout(fn: Callable[[], T], timeout_seconds: float, executor: ThreadPoolExecutor = INTEGRATION_EXECUTOR) -> T:
    # Carry request context (request_id, tenant, ...) into the worker thread for logging.
    context = contextvars.copy_context()
    future = executor.submit(context.run, fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise IntegrationTimeout(f"call exceeded {timeout_seconds}s") from exc


def retry(
    fn: Callable[[], T],
    *,
    attempts: int,
    retry_on: tuple[type[BaseException], ...],
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            last = exc
            if attempt == attempts - 1:
                break
            sleep(min(max_delay, base_delay * (2**attempt)) * (0.5 + random.random() / 2))  # noqa: S311 - jitter, not cryptography
    assert last is not None
    raise last


class CircuitBreaker:
    """Closed → (N consecutive failures) → open → (reset timeout) → half-open → one trial call."""

    def __init__(self, name: str, failure_threshold: int, reset_timeout_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout:
            return "half_open"
        return "open"

    def call(self, fn: Callable[[], T]) -> T:
        with self._lock:
            if self._state_locked() == "open":
                raise CircuitOpenError(f"circuit '{self.name}' is open")
        try:
            result = fn()
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self.failure_threshold or self._opened_at is not None:
                    self._opened_at = self._clock()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
        return result
