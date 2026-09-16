"""Short-lived mutual exclusion with leases.

A lease guarantees a crashed holder can't block a key forever. Because a lease can expire while its
holder is still working, locks are an efficiency guard only; correctness comes from compare-and-set
writes (see ConversationRepository.save). `InMemoryLockStore` covers one process; `RedisLockStore`
(app.state.redis_backend) covers several replicas.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import secrets
import threading
import time
from typing import Protocol


class LockNotAcquired(Exception):
    """The key stayed locked for the whole wait budget."""


class LockStore(Protocol):
    def acquire(self, key: str, lease_seconds: float, wait_seconds: float) -> str | None:
        """Returns an ownership token, or None if the key stayed locked for `wait_seconds`."""

    def release(self, key: str, token: str) -> None:
        """Releases only if `token` still owns the key (an expired lease may belong to someone else now)."""


@contextmanager
def held(store: LockStore, key: str, *, lease_seconds: float, wait_seconds: float) -> Iterator[str]:
    token = store.acquire(key, lease_seconds, wait_seconds)
    if token is None:
        raise LockNotAcquired(key)
    try:
        yield token
    finally:
        store.release(key, token)


class InMemoryLockStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._owners: dict[str, tuple[str, float]] = {}
        self._cond = threading.Condition()
        self._clock = clock

    def acquire(self, key: str, lease_seconds: float, wait_seconds: float) -> str | None:
        deadline = self._clock() + wait_seconds
        token = secrets.token_hex(8)
        with self._cond:
            while True:
                now = self._clock()
                owner = self._owners.get(key)
                if owner is None or owner[1] <= now:
                    self._owners[key] = (token, now + lease_seconds)
                    return token
                remaining = min(deadline, owner[1]) - now
                if deadline <= now:
                    return None
                self._cond.wait(timeout=max(remaining, 0.01))

    def release(self, key: str, token: str) -> None:
        with self._cond:
            owner = self._owners.get(key)
            if owner and owner[0] == token:
                del self._owners[key]
                self._cond.notify_all()

    def forget(self, key: str) -> None:
        with self._cond:
            self._owners.pop(key, None)
            self._cond.notify_all()
