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
    deadline_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Retry `fn` on `retry_on` errors with jittered backoff.

    `deadline_seconds` bounds the whole sequence: no new attempt starts once the budget is spent,
    so retries can never outlast the caller's own timeout.
    """
    started = clock()
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**attempt)) * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not cryptography
            if deadline_seconds is not None and clock() - started + delay >= deadline_seconds:
                break
            sleep(delay)
    assert last is not None
    raise last


class CircuitBreaker:
    """CLOSED → (N consecutive dependency failures) → OPEN → (cooldown) → HALF_OPEN → one trial call.

    * In HALF_OPEN exactly one caller gets the trial permit; concurrent callers fail fast with
      CircuitOpenError instead of piling onto a dependency that may still be down.
    * Trial success → CLOSED (counters reset). Trial failure → OPEN again for a full cooldown.
    * Only exceptions classified by `is_failure` count; callers shield guest-input/business errors
      (see ResilientReservationProvider) so validation problems never trip the breaker.

    State is per process. With several replicas each keeps its own view, which only delays how
    quickly every replica notices an outage; it never lets a failing dependency be hammered.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        is_failure: Callable[[BaseException], bool] = lambda exc: True,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout_seconds
        self._clock = clock
        self._is_failure = is_failure
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
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
            state = self._state_locked()
            if state == "open" or (state == "half_open" and self._probe_in_flight):
                raise CircuitOpenError(f"circuit '{self.name}' is {state}")
            is_probe = state == "half_open"
            if is_probe:
                self._probe_in_flight = True
        try:
            result = fn()
        except BaseException as exc:
            with self._lock:
                if is_probe:
                    self._probe_in_flight = False
                if self._is_failure(exc):
                    self._failures += 1
                    if is_probe or self._failures >= self.failure_threshold:
                        self._opened_at = self._clock()
                elif is_probe:
                    self._opened_at = None  # the dependency answered; the error was not its fault
                    self._failures = 0
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
        return result
