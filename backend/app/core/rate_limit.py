"""Rate limiting boundary.

`InMemorySlidingWindowRateLimiter` is correct for a single process. With several replicas,
implement `RateLimiter` on Redis (or enforce at the API gateway) so limits are shared.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import math
import threading
import time
from typing import Protocol


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


@dataclass(frozen=True)
class RateLimitRule:
    dimension: str  # ip | hotel | conversation | api_key | tenant
    key: str
    limit: int
    window_seconds: float = 60.0


class RateLimiter(Protocol):
    def hit(self, rule: RateLimitRule) -> RateLimitDecision: ...


class InMemorySlidingWindowRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 100_000):
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._max_keys = max_keys

    def hit(self, rule: RateLimitRule) -> RateLimitDecision:
        now = self._clock()
        bucket_key = f"{rule.dimension}:{rule.key}"
        with self._lock:
            if len(self._hits) > self._max_keys:
                self._evict(now, rule.window_seconds)
            hits = self._hits.setdefault(bucket_key, deque())
            while hits and hits[0] <= now - rule.window_seconds:
                hits.popleft()
            if len(hits) >= rule.limit:
                retry_after = math.ceil(hits[0] + rule.window_seconds - now)
                return RateLimitDecision(False, 0, max(retry_after, 1))
            hits.append(now)
            return RateLimitDecision(True, rule.limit - len(hits), 0)

    def _evict(self, now: float, window: float) -> None:
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= now - window]
        for key in stale:
            del self._hits[key]
