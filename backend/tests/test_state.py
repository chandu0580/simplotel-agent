"""State boundaries with the default in-memory backend: locks, compare-and-set, rate-limit dimensions, config."""

from datetime import timedelta
import threading

from fastapi.testclient import TestClient
import pytest

from app.conversations.models import Conversation
from app.conversations.repository import ConversationConflict, InMemoryConversationRepository
from app.core.clock import FixedClock
from app.core.config import ConfigError, Settings
from app.core.errors import AppError
from app.core.locks import InMemoryLockStore
from app.main import create_app
from tests.conftest import GOA, TODAY, make_container

BASE = f"/api/v1/hotels/{GOA}"


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_lock_store_excludes_waits_and_expires_leases():
    clock = Tick()
    locks = InMemoryLockStore(clock=clock)
    token = locks.acquire("k", lease_seconds=10, wait_seconds=0)
    assert token and locks.acquire("k", lease_seconds=10, wait_seconds=0) is None
    locks.release("k", "not-the-owner")  # a stale holder can't release someone else's lock
    assert locks.acquire("k", lease_seconds=10, wait_seconds=0) is None
    clock.t = 11  # lease expired: a crashed holder doesn't block forever
    second = locks.acquire("k", lease_seconds=10, wait_seconds=0)
    assert second and second != token
    locks.release("k", second)
    assert locks.acquire("k", lease_seconds=10, wait_seconds=0)


def _conversation(clock) -> Conversation:
    now = clock.now()
    return Conversation(id="conv_1", tenant_id="t", hotel_id="h", channel="web", created_at=now, updated_at=now, expires_at=now + timedelta(hours=1))


def test_repository_save_is_compare_and_set():
    clock = FixedClock(TODAY)
    repo = InMemoryConversationRepository(clock)
    conversation = _conversation(clock)
    repo.save(conversation, expected_version=None)
    with pytest.raises(ConversationConflict):
        repo.save(conversation, expected_version=None)  # create must not overwrite

    first, second = repo.get("t", "h", "conv_1"), repo.get("t", "h", "conv_1")
    first.version += 1
    repo.save(first, expected_version=0)
    second.version += 1
    with pytest.raises(ConversationConflict):
        repo.save(second, expected_version=0)  # stale reader loses instead of overwriting
    assert repo.get("t", "h", "conv_1").version == 1

    repo.delete("t", "h", "conv_1")
    with pytest.raises(AppError) as exc:
        repo.save(first, expected_version=1)
    assert exc.value.status == 404  # deleted mid-turn: not silently resurrected


def test_turn_on_a_locked_conversation_is_409_busy():
    container = make_container(conversation_lock_wait_seconds=0.05)
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        token = container.conversations.locks.acquire(f"conv:tenant-demo:{GOA}:{cid}", lease_seconds=30, wait_seconds=0)
        response = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"})
        container.conversations.locks.release(f"conv:tenant-demo:{GOA}:{cid}", token)
        after = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "CONVERSATION_BUSY"
    assert response.headers["Retry-After"] == "2"
    assert after.status_code == 200


def test_version_increments_once_per_saved_turn():
    container = make_container()
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        threads = [threading.Thread(target=lambda: client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Is there a pool?"})) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    ctx = container.tenants.resolve(GOA)
    stored = container.conversations.get(ctx, cid)
    assert stored.version == 6 and len(stored.messages) == 12


def test_tenant_rate_limit_applies_across_endpoints():
    container = make_container(rate_limit_enabled=True, rate_limit_tenant_per_minute=2, rate_limit_ip_per_minute=100, rate_limit_hotel_per_minute=100)
    with TestClient(create_app(container=container)) as client:
        codes = [client.post(f"{BASE}/conversations", json={}).status_code for _ in range(3)]
        blocked = client.post(f"{BASE}/conversations", json={})
    assert codes[:2] == [201, 201] and codes[2] == 429
    assert blocked.json()["error"]["details"] == [{"dimension": "tenant"}] and int(blocked.headers["Retry-After"]) >= 1


def test_ip_burst_limit_rejects_rapid_fire():
    container = make_container(rate_limit_enabled=True, rate_limit_ip_burst=2, rate_limit_burst_window_seconds=5, rate_limit_ip_per_minute=100)
    with TestClient(create_app(container=container)) as client:
        responses = [client.get(BASE) for _ in range(3)]
    assert [r.status_code for r in responses[:2]] == [200, 200] and responses[2].status_code == 429
    assert responses[2].json()["error"]["details"] == [{"dimension": "ip_burst"}]
    assert 1 <= int(responses[2].headers["Retry-After"]) <= 5


def test_state_configuration_is_validated():
    with pytest.raises(ConfigError, match="REDIS_URL"):
        Settings.for_tests(state_backend="redis")
    with pytest.raises(ConfigError, match="STATE_BACKEND"):
        Settings.for_tests(state_backend="memcached")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        Settings.for_tests(database_url="mysql://x")
    with pytest.raises(ConfigError, match="LOCK_LEASE"):
        Settings.for_tests(conversation_lock_lease_seconds=10)


def test_state_urls_are_secrets():
    settings = Settings.from_env(
        {"STATE_BACKEND": "redis", "REDIS_URL": "redis://:redis-password-123@cache:6379/0", "DATABASE_URL": "postgresql://app:db-password-456@db/simplotel", "LLM_PROVIDER": "none"}
    )
    assert "redis-password-123" in settings.secret_values() and "db-password-456" in settings.secret_values()
    assert "password" not in repr(settings)


def test_blocking_state_calls_never_run_on_the_event_loop():
    """Rate limiting and state access do network I/O with Redis; only /health may be async."""
    import asyncio
    import time

    from app.core.rate_limit import RateLimitDecision

    app = create_app(container=make_container(rate_limit_enabled=True))
    def endpoints(routes):
        for route in routes:
            nested = getattr(route, "original_router", None) or (route if getattr(route, "routes", None) else None)
            if nested is not None:  # included routers are nested
                yield from endpoints(nested.routes)
            elif getattr(route, "endpoint", None) and getattr(route, "include_in_schema", False):
                yield getattr(route, "path", "?"), route.endpoint

    found = list(endpoints(app.routes))
    assert len(found) > 10
    assert [path for path, fn in found if asyncio.iscoroutinefunction(fn)] == ["/health"]

    class SlowLimiter:
        def hit(self, rule):
            time.sleep(0.5)  # a slow Redis round trip
            return RateLimitDecision(True, 10, 0)

    app.state.container.rate_limiter = SlowLimiter()
    with TestClient(app) as client:
        workers = [threading.Thread(target=client.post, args=(f"{BASE}/availability",), kwargs={"json": {"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2}}) for _ in range(4)]
        for w in workers:
            w.start()
        time.sleep(0.1)
        started = time.perf_counter()
        assert client.get("/health").status_code == 200
        health_ms = (time.perf_counter() - started) * 1000
        for w in workers:
            w.join()
    assert health_ms < 300, health_ms  # liveness stays responsive while guest requests wait on state I/O
