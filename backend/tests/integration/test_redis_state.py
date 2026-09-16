"""Redis-backed state and multi-replica behaviour (three independent app instances sharing one Redis)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
import time

from fastapi.testclient import TestClient
import pytest
import redis

from app.container import build_container
from app.conversations.models import Conversation
from app.conversations.repository import ConversationConflict
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.errors import AppError
from app.core.metrics import Metrics
from app.core.rate_limit import RateLimitRule
from app.llm.mock_provider import LatencyMockProvider
from app.main import create_app
from app.reservations.models import Booking, ReservationError, ReservationErrorCode
from app.state import redis_backend as rb
from tests.conftest import BLR, GOA, TODAY
from tests.test_tools_and_resilience import BOOKING, guest, tool_ctx

BASE = f"/api/v1/hotels/{GOA}"


def _conversation(clock, cid="conv_r1") -> Conversation:
    now = clock.now()
    return Conversation(id=cid, tenant_id="tenant-demo", hotel_id=GOA, channel="web", created_at=now, updated_at=now, expires_at=now + timedelta(minutes=30))


# ---------- individual stores ----------


def test_conversation_repository_cas_and_native_ttl(redis_url, redis_prefix):
    clock = FixedClock(TODAY)
    client = rb.connect(redis_url)
    repo = rb.RedisConversationRepository(client, redis_prefix, clock)
    conversation = _conversation(clock)
    repo.save(conversation, expected_version=None)
    with pytest.raises(ConversationConflict):
        repo.save(conversation, expected_version=None)

    a, b = repo.get("tenant-demo", GOA, "conv_r1"), repo.get("tenant-demo", GOA, "conv_r1")
    a.version += 1
    repo.save(a, expected_version=0)
    b.version += 1
    with pytest.raises(ConversationConflict):
        repo.save(b, expected_version=0)

    ttl_ms = client.pttl(f"{redis_prefix}:conv:tenant-demo:{GOA}:conv_r1")
    assert 29 * 60 * 1000 < ttl_ms <= 30 * 60 * 1000  # expiry is Redis-native
    assert repo.get("other-tenant", GOA, "conv_r1") is None  # tenant is part of the key
    assert repo.delete("tenant-demo", GOA, "conv_r1") and repo.get("tenant-demo", GOA, "conv_r1") is None
    with pytest.raises(AppError) as exc:
        repo.save(a, expected_version=1)
    assert exc.value.status == 404


def test_rate_limit_is_shared_between_limiters_and_keys_are_hashed(redis_url, redis_prefix):
    replicas = [rb.RedisSlidingWindowRateLimiter(rb.connect(redis_url), redis_prefix) for _ in range(3)]
    rule = RateLimitRule("ip", "203.0.113.9", limit=5, window_seconds=60)
    decisions = [replicas[i % 3].hit(rule) for i in range(9)]
    assert [d.allowed for d in decisions] == [True] * 5 + [False] * 4
    assert all(1 <= d.retry_after_seconds <= 60 for d in decisions[5:])
    keys = [k.decode() for k in redis.Redis.from_url(redis_url).scan_iter(f"{redis_prefix}:rl:*")]
    assert keys and not any("203.0.113.9" in k for k in keys)


def test_rate_limit_window_slides(redis_url, redis_prefix):
    limiter = rb.RedisSlidingWindowRateLimiter(rb.connect(redis_url), redis_prefix)
    rule = RateLimitRule("ip_burst", "198.51.100.1", limit=2, window_seconds=1)
    assert [limiter.hit(rule).allowed for _ in range(3)] == [True, True, False]
    time.sleep(1.1)
    assert limiter.hit(rule).allowed


def test_rate_limiter_fails_open_when_redis_is_down(redis_prefix):
    errors = []
    limiter = rb.RedisSlidingWindowRateLimiter(rb.connect("redis://127.0.0.1:1/0"), redis_prefix, on_error=lambda: errors.append(1))
    decision = limiter.hit(RateLimitRule("ip", "x", limit=1))
    assert decision.allowed and errors == [1]


def test_conversation_store_outage_is_503_state_unavailable(redis_prefix):
    repo = rb.RedisConversationRepository(rb.connect("redis://127.0.0.1:1/0"), redis_prefix, FixedClock(TODAY))
    with pytest.raises(AppError) as exc:
        repo.get("t", "h", "c")
    assert exc.value.status == 503 and exc.value.code == "STATE_UNAVAILABLE" and not repo.is_healthy()


def test_lock_store_across_clients(redis_url, redis_prefix):
    a, b = rb.RedisLockStore(rb.connect(redis_url), redis_prefix), rb.RedisLockStore(rb.connect(redis_url), redis_prefix)
    token = a.acquire("conv:1", lease_seconds=0.5, wait_seconds=0)
    assert token and b.acquire("conv:1", lease_seconds=5, wait_seconds=0.1) is None
    b.release("conv:1", "wrong-token")
    assert b.acquire("conv:1", lease_seconds=5, wait_seconds=0) is None
    assert b.acquire("conv:1", lease_seconds=5, wait_seconds=1.0)  # waits for the lease to expire


def test_idempotency_runs_once_across_stores(redis_url, redis_prefix):
    stores = [rb.RedisIdempotencyStore(rb.connect(redis_url), redis_prefix) for _ in range(3)]
    runs = []
    booking = Booking.model_validate(
        {"booking_id": "BK-1", "tenant_id": "t", "hotel_id": "h", "status": "confirmed", "room_id": "r", "check_in": "2026-10-07", "check_out": "2026-10-08",
         "adults": 2, "children": 0, "total_price": 100, "currency": "INR", "created_at": "2026-09-01T00:00:00Z"}
    )

    def operation():
        runs.append(1)
        time.sleep(0.3)  # long enough that the other replicas arrive while it runs
        return booking

    with ThreadPoolExecutor(9) as pool:
        results = list(pool.map(lambda i: stores[i % 3].run_once("booking:t:h", "idem-key-shared", {"room": "r"}, operation, result_model=Booking), range(9)))
    assert len(runs) == 1 and {r.booking_id for r in results} == {"BK-1"}
    with pytest.raises(ReservationError) as conflict:
        stores[0].run_once("booking:t:h", "idem-key-shared", {"room": "other"}, operation, result_model=Booking)
    assert conflict.value.code == ReservationErrorCode.IDEMPOTENCY_CONFLICT


def test_idempotency_reports_in_progress_after_wait_budget(redis_url, redis_prefix):
    slow = rb.RedisIdempotencyStore(rb.connect(redis_url), redis_prefix)
    impatient = rb.RedisIdempotencyStore(rb.connect(redis_url), redis_prefix, wait_seconds=0.2)
    started = threading.Event()

    def long_operation():
        started.set()
        time.sleep(1.0)
        raise ReservationError(ReservationErrorCode.NOT_AVAILABLE, "gone")

    worker = threading.Thread(target=lambda: pytest.raises(ReservationError, slow.run_once, "s", "idem-key-slow", {}, long_operation, Booking))
    worker.start()
    assert started.wait(5)
    with pytest.raises(ReservationError) as exc:
        impatient.run_once("s", "idem-key-slow", {}, lambda: None, result_model=Booking)
    worker.join()
    assert exc.value.code == ReservationErrorCode.IN_PROGRESS


# ---------- three replicas ----------


def _replica(redis_url, prefix, **overrides):
    settings = Settings.for_tests(state_backend="redis", redis_url=redis_url, redis_key_prefix=prefix, **overrides)
    return build_container(settings, clock=FixedClock(TODAY), llm_provider=LatencyMockProvider(latency_ms=150))


def test_concurrent_turns_on_three_replicas_lose_nothing(redis_url, redis_prefix):
    replicas = [_replica(redis_url, redis_prefix) for _ in range(3)]
    clients = [TestClient(create_app(container=c)) for c in replicas]
    try:
        cid = clients[0].post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        with ThreadPoolExecutor(6) as pool:
            statuses = list(pool.map(lambda i: clients[i % 3].post(f"{BASE}/conversations/{cid}/messages", json={"message": f"Question {i}: check-in time?"}).status_code, range(6)))
        view = clients[2].get(f"{BASE}/conversations/{cid}").json()
    finally:
        for c in clients:
            c.close()
    assert statuses == [200] * 6
    user_messages = [m["content"] for m in view["messages"] if m["role"] == "user"]
    assert sorted(user_messages) == sorted(f"Question {i}: check-in time?" for i in range(6))  # no lost update
    assert len(view["messages"]) == 12


def test_duplicate_booking_on_three_replicas_creates_one_booking(redis_url, redis_prefix):
    replicas = [_replica(redis_url, redis_prefix, feature_flags={"booking_tools_enabled": True}) for _ in range(3)]

    def book(i):
        container = replicas[i % 3]
        ctx = tool_ctx(container, principal=guest(), guest_confirmed=True, idempotency_key="idem-replicas-001")
        return container.tools.execute("create_booking", BOOKING, ctx, invoked_by="api")

    with ThreadPoolExecutor(9) as pool:
        results = list(pool.map(book, range(9)))
    assert all(r.ok for r in results), [r.error_code for r in results]
    assert len({r.data.booking_id for r in results}) == 1  # one logical booking
    ctx = replicas[0].tenants.resolve(GOA)
    assert sum(len(c.reservations.inner.bookings_for(ctx)) for c in replicas) == 1  # created on exactly one replica


def test_simultaneous_availability_and_shared_limits_on_three_replicas(redis_url, redis_prefix):
    replicas = [_replica(redis_url, redis_prefix, rate_limit_enabled=True, rate_limit_ip_per_minute=6, rate_limit_ip_burst=100) for _ in range(3)]
    clients = [TestClient(create_app(container=c)) for c in replicas]
    body = {"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2}
    try:
        with ThreadPoolExecutor(9) as pool:
            responses = list(pool.map(lambda i: clients[i % 3].post(f"{BASE}/availability", json=body), range(9)))
    finally:
        for c in clients:
            c.close()
    ok = [r for r in responses if r.status_code == 200]
    assert len(ok) == 6 and sum(r.status_code == 429 for r in responses) == 3  # one budget across replicas
    assert len({str(r.json()["rooms"]) for r in ok}) == 1  # every replica returns the same result


def test_cross_tenant_access_is_rejected_on_another_replica(redis_url, redis_prefix):
    a, b = (TestClient(create_app(container=_replica(redis_url, redis_prefix))) for _ in range(2))
    try:
        cid = a.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        other_hotel = b.get(f"/api/v1/hotels/{BLR}/conversations/{cid}")
        same_hotel = b.get(f"{BASE}/conversations/{cid}")
    finally:
        a.close()
        b.close()
    assert other_hotel.status_code == 404 and other_hotel.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert same_hotel.status_code == 200


def test_readiness_fails_when_redis_is_unreachable(redis_prefix):
    container = build_container(Settings.for_tests(state_backend="redis", redis_url="redis://127.0.0.1:1/0", redis_key_prefix=redis_prefix), clock=FixedClock(TODAY))
    with TestClient(create_app(container=container)) as client:
        ready = client.get("/ready")
        turn = client.post(f"{BASE}/conversations", json={})
    assert ready.status_code == 503 and ready.json()["checks"]["state"] == "failing"
    assert turn.status_code == 503 and turn.json()["error"]["code"] == "STATE_UNAVAILABLE"


def test_metrics_object_is_per_container():
    assert Metrics().registry is not Metrics().registry
