"""Idempotency for mutating reservation operations.

A retried `create_booking` with the same idempotency key and the same request returns the
original booking instead of creating a second one. Reusing a key for a *different* request
is rejected. Production would back this with a unique-constrained table in the booking
database, written in the same transaction as the booking.
"""

from collections.abc import Callable
import threading
import time
from typing import Protocol, TypeVar

from ..core.versioning import content_hash
from .models import ReservationError, ReservationErrorCode

T = TypeVar("T")


class IdempotencyStore(Protocol):
    def run_once(self, scope: str, key: str, request_fingerprint: object, operation: Callable[[], T]) -> T: ...


class InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: float = 24 * 3600, clock: Callable[[], float] = time.monotonic):
        self._results: dict[str, tuple[float, str, object]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._ttl = ttl_seconds
        self._clock = clock

    def run_once(self, scope: str, key: str, request_fingerprint: object, operation: Callable[[], T]) -> T:
        if not key or len(key) < 8:
            raise ReservationError(ReservationErrorCode.INVALID_REQUEST, "An idempotency key of at least 8 characters is required.")
        full_key = f"{scope}:{key}"
        fingerprint = content_hash(request_fingerprint, 32)
        with self._guard:
            lock = self._locks.setdefault(full_key, threading.Lock())
        with lock:  # concurrent duplicates wait for the first attempt instead of racing
            stored = self._results.get(full_key)
            if stored and stored[0] > self._clock():
                if stored[1] != fingerprint:
                    raise ReservationError(ReservationErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key was already used for a different request.")
                return stored[2]  # type: ignore[return-value]
            result = operation()
            self._results[full_key] = (self._clock() + self._ttl, fingerprint, result)
            return result
