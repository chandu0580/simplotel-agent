"""Redis implementations of the ephemeral-state interfaces.

Redis holds only state that may be lost without losing business records: conversations (TTL-bound),
rate-limit windows, idempotency results, and locks. Durable records (bookings, audit events) belong in
PostgreSQL (app.db).

* Every multi-step operation is one Lua script, so replicas can't interleave inside it.
* Time-based decisions use the Redis server clock (`TIME`), so replica clock skew doesn't matter.
* Keys never contain raw IP addresses or idempotency keys; those are hashed.
* Redis failures surface as 503 `STATE_UNAVAILABLE` (conversations, locks) or `ReservationError(UNAVAILABLE)`
  (idempotency). The rate limiter fails open and logs, because rejecting every guest during a Redis
  outage would be worse than briefly unenforced limits.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import json
import logging
import secrets
import time
from typing import TypeVar

from pydantic import BaseModel
import redis

from ..conversations.models import Conversation
from ..conversations.repository import ConversationConflict
from ..core.errors import AppError, ErrorCode
from ..core.rate_limit import RateLimitDecision, RateLimitRule
from ..core.versioning import content_hash
from ..reservations.models import ReservationError, ReservationErrorCode

logger = logging.getLogger("hotel_assistant.state")
T = TypeVar("T")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def connect(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0, health_check_interval=30, decode_responses=True)


@contextmanager
def _state_errors() -> Iterator[None]:
    try:
        yield
    except redis.RedisError as exc:
        logger.error("state_backend_error error=%s", type(exc).__name__)
        raise AppError(ErrorCode.STATE_UNAVAILABLE, "The service is temporarily unavailable. Please try again shortly.", 503, headers={"Retry-After": "5"}) from exc


_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


class RedisLockStore:
    def __init__(self, client: redis.Redis, prefix: str, poll_seconds: float = 0.02):
        self.client = client
        self.prefix = prefix
        self.poll = poll_seconds
        self._release = client.register_script(_RELEASE)

    def _key(self, key: str) -> str:
        return f"{self.prefix}:lock:{key}"

    def acquire(self, key: str, lease_seconds: float, wait_seconds: float) -> str | None:
        token = secrets.token_hex(16)
        deadline = time.monotonic() + wait_seconds
        with _state_errors():
            while True:
                if self.client.set(self._key(key), token, nx=True, px=int(lease_seconds * 1000)):
                    return token
                if time.monotonic() >= deadline:
                    return None
                time.sleep(self.poll)

    def release(self, key: str, token: str) -> None:
        try:
            self._release(keys=[self._key(key)], args=[token])
        except redis.RedisError as exc:  # the lease expires on its own
            logger.warning("lock_release_failed error=%s", type(exc).__name__)


# Compare-and-set on the stored conversation version. Returns 1 saved, 0 version conflict, -1 missing.
_SAVE_CONVERSATION = """
local current = redis.call('GET', KEYS[1])
if ARGV[3] == '' then
  if current then return 0 end
else
  if not current then return -1 end
  if tostring(cjson.decode(current)['version']) ~= ARGV[3] then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
return 1
"""


class RedisConversationRepository:
    def __init__(self, client: redis.Redis, prefix: str, clock):
        self.client = client
        self.prefix = prefix
        self.clock = clock
        self._save = client.register_script(_SAVE_CONVERSATION)

    def _key(self, tenant_id: str, hotel_id: str, conversation_id: str) -> str:
        return f"{self.prefix}:conv:{tenant_id}:{hotel_id}:{conversation_id}"

    def get(self, tenant_id: str, hotel_id: str, conversation_id: str) -> Conversation | None:
        with _state_errors():
            raw = self.client.get(self._key(tenant_id, hotel_id, conversation_id))
        return Conversation.model_validate_json(raw) if raw else None

    def save(self, conversation: Conversation, expected_version: int | None = None) -> None:
        ttl_ms = int((conversation.expires_at - self.clock.now()).total_seconds() * 1000)
        with _state_errors():
            result = self._save(
                keys=[self._key(conversation.tenant_id, conversation.hotel_id, conversation.id)],
                args=[conversation.model_dump_json(), max(ttl_ms, 1), "" if expected_version is None else str(expected_version)],
            )
        if result == -1:
            raise AppError(ErrorCode.CONVERSATION_NOT_FOUND, "Conversation not found or expired.", 404)
        if result == 0:
            raise ConversationConflict(conversation.id)

    def delete(self, tenant_id: str, hotel_id: str, conversation_id: str) -> bool:
        with _state_errors():
            return bool(self.client.delete(self._key(tenant_id, hotel_id, conversation_id)))

    def purge_expired(self) -> int:
        return 0  # Redis expires keys itself

    def is_healthy(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False


# Sliding window over a sorted set of hit timestamps (server clock, milliseconds).
_SLIDING_WINDOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, 0, window - (now - tonumber(oldest[2]))}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, limit - count - 1, 0}
"""


class RedisSlidingWindowRateLimiter:
    def __init__(self, client: redis.Redis, prefix: str, on_error: Callable[[], None] | None = None):
        self.client = client
        self.prefix = prefix
        self.on_error = on_error
        self._hit = client.register_script(_SLIDING_WINDOW)

    def hit(self, rule: RateLimitRule) -> RateLimitDecision:
        key = f"{self.prefix}:rl:{rule.dimension}:{_digest(rule.key)}:{int(rule.window_seconds)}"
        try:
            allowed, remaining, retry_ms = self._hit(keys=[key], args=[rule.limit, int(rule.window_seconds * 1000), secrets.token_hex(8)])
        except redis.RedisError as exc:
            logger.warning("rate_limiter_unavailable dimension=%s error=%s", rule.dimension, type(exc).__name__)
            if self.on_error:
                self.on_error()
            return RateLimitDecision(True, rule.limit, 0)  # fail open (see module docstring)
        return RateLimitDecision(bool(allowed), int(remaining), max(1, -(-int(retry_ms) // 1000)) if not allowed else 0)


class RedisIdempotencyStore:
    """Same contract as InMemoryIdempotencyStore, shared by every replica.

    One replica takes a lease on the key and runs the operation; the others wait for its stored result.
    The lease must outlast the operation. If it expires first, a second attempt can start, so the
    downstream system (PMS) should also receive the idempotency key; the booking table's unique
    (tenant_id, hotel_id, idempotency_key) constraint is the durable backstop.
    """

    def __init__(self, client: redis.Redis, prefix: str, ttl_seconds: int = 24 * 3600, lease_seconds: float = 60.0, wait_seconds: float = 15.0):
        self.client = client
        self.prefix = prefix
        self.ttl = ttl_seconds
        self.lease = lease_seconds
        self.wait = wait_seconds
        self._release = client.register_script(_RELEASE)

    def _stored(self, key: str, fingerprint: str, result_model: type[BaseModel]):
        raw = self.client.get(key)
        if raw is None:
            return None
        record = json.loads(raw)
        if record["fingerprint"] != fingerprint:
            raise ReservationError(ReservationErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key was already used for a different request.")
        return result_model.model_validate(record["result"])

    def run_once(self, scope: str, key: str, request_fingerprint: object, operation: Callable[[], T], result_model: type[BaseModel] | None = None) -> T:
        if not key or len(key) < 8:
            raise ReservationError(ReservationErrorCode.INVALID_REQUEST, "An idempotency key of at least 8 characters is required.")
        if result_model is None:
            raise TypeError("RedisIdempotencyStore needs result_model to store results across replicas")
        base = f"{self.prefix}:idem:{_digest(scope)}:{_digest(key)}"
        result_key, lock_key = f"{base}:result", f"{base}:lock"
        fingerprint = content_hash(request_fingerprint, 32)
        deadline = time.monotonic() + self.wait
        try:
            while True:
                stored = self._stored(result_key, fingerprint, result_model)
                if stored is not None:
                    return stored  # type: ignore[return-value]
                token = secrets.token_hex(16)
                if self.client.set(lock_key, token, nx=True, px=int(self.lease * 1000)):
                    try:
                        stored = self._stored(result_key, fingerprint, result_model)  # finished between our checks
                        if stored is not None:
                            return stored  # type: ignore[return-value]
                        result = operation()
                        record = {"fingerprint": fingerprint, "result": result.model_dump(mode="json")}  # type: ignore[attr-defined]
                        self.client.set(result_key, json.dumps(record), ex=self.ttl)
                        return result
                    finally:
                        self._release(keys=[lock_key], args=[token])
                if time.monotonic() >= deadline:
                    raise ReservationError(ReservationErrorCode.IN_PROGRESS, "A request with this idempotency key is still being processed.", retryable=True)
                time.sleep(0.05)
        except redis.RedisError as exc:
            logger.error("idempotency_store_unavailable error=%s", type(exc).__name__)
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "Booking service temporarily unavailable.", retryable=True) from exc
