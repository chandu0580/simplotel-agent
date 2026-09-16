"""Tool framework (exposure, flags, validation, authorization, idempotency, timeouts) and integration resilience."""

from datetime import date
import threading
import time

from pydantic import BaseModel
import pytest

from app.auth.principal import Principal, Role
from app.core.cache import TTLCache
from app.core.flags import FeatureFlags
from app.core.resilience import CircuitBreaker, CircuitOpenError, retry
from app.reservations.availability import AvailabilityValidationError
from app.reservations.idempotency import InMemoryIdempotencyStore
from app.reservations.models import AvailabilityQuery, ReservationError, ReservationErrorCode
from app.reservations.provider import MockReservationProvider, ResilientReservationProvider
from app.tools.base import ToolContext, ToolDefinition, ToolErrorCode, ToolPolicy, ToolRegistry
from tests.conftest import GOA, TODAY, make_container, tool_response

WED, THU = date(2026, 10, 7), date(2026, 10, 8)
BOOKING = {"room_id": "garden-standard", "check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2, "children": 0}


def guest(hotel_id: str = GOA) -> Principal:
    return Principal("guest-123", "tenant-demo", frozenset({Role.GUEST}), frozenset({hotel_id}))


def tool_ctx(container, **kwargs) -> ToolContext:
    return ToolContext(tenant=container.tenants.resolve(GOA), kb=container.knowledge.snapshot(GOA, TODAY), today=TODAY, **kwargs)


# ---------- Exposure, flags, authorization ----------


def test_model_only_sees_exposed_read_only_tools(offline_container):
    names = [d.name for d in offline_container.tools.model_tools()]
    assert names == ["check_availability", "request_booking_details"]
    assert all(d.policy == ToolPolicy.READ_ONLY for d in offline_container.tools.model_tools())


def test_unknown_and_unexposed_tools_are_rejected(offline_container):
    ctx = tool_ctx(offline_container)
    assert offline_container.tools.execute("drop_database", {}, ctx).error_code == ToolErrorCode.UNKNOWN_TOOL
    assert offline_container.tools.execute("create_booking", BOOKING, ctx, invoked_by="model").error_code == ToolErrorCode.NOT_EXPOSED


@pytest.mark.parametrize(
    ("flags", "ctx_kwargs", "expected"),
    [
        ({}, {"principal": guest(), "guest_confirmed": True, "idempotency_key": "key-12345678"}, ToolErrorCode.FEATURE_DISABLED),
        ({"booking_tools_enabled": True}, {"guest_confirmed": True, "idempotency_key": "key-12345678"}, ToolErrorCode.AUTHENTICATION_REQUIRED),
        ({"booking_tools_enabled": True}, {"principal": guest("hotel-blr-001"), "guest_confirmed": True, "idempotency_key": "key-12345678"}, ToolErrorCode.FORBIDDEN),
        (
            {"booking_tools_enabled": True},
            {"principal": Principal("staff", "tenant-demo", frozenset({Role.HOTEL_STAFF})), "guest_confirmed": True, "idempotency_key": "key-12345678"},
            ToolErrorCode.FORBIDDEN,
        ),
        ({"booking_tools_enabled": True}, {"principal": guest(), "idempotency_key": "key-12345678"}, ToolErrorCode.CONFIRMATION_REQUIRED),
        ({"booking_tools_enabled": True}, {"principal": guest(), "guest_confirmed": True}, ToolErrorCode.IDEMPOTENCY_KEY_REQUIRED),
    ],
)
def test_mutating_tool_authorization(flags, ctx_kwargs, expected):
    container = make_container(feature_flags=flags)
    result = container.tools.execute("create_booking", BOOKING, tool_ctx(container, **ctx_kwargs), invoked_by="api")
    assert result.error_code == expected
    assert container.reservations.inner.bookings_for(container.tenants.resolve(GOA)) == []
    assert "ToolFailed" in container.recent_events.names()


def test_confirmed_authenticated_booking_is_idempotent():
    container = make_container(feature_flags={"booking_tools_enabled": True})
    ctx = tool_ctx(container, principal=guest(), guest_confirmed=True, idempotency_key="idem-key-0001")

    first = container.tools.execute("create_booking", BOOKING, ctx, invoked_by="api")
    retry_same = container.tools.execute("create_booking", BOOKING, ctx, invoked_by="api")
    reused_key = container.tools.execute("create_booking", {**BOOKING, "adults": 1}, ctx, invoked_by="api")

    assert first.ok and retry_same.ok
    assert first.data.booking_id == retry_same.data.booking_id
    assert len(container.reservations.inner.bookings_for(ctx.tenant)) == 1
    assert not reused_key.ok and reused_key.error_code == ToolErrorCode.BUSINESS_RULE
    assert {"BookingRequested", "BookingConfirmed"} <= set(container.recent_events.names())


def test_concurrent_duplicate_bookings_create_one_reservation():
    container = make_container(feature_flags={"booking_tools_enabled": True})
    ctx = tool_ctx(container, principal=guest(), guest_confirmed=True, idempotency_key="idem-concurrent-1")
    results = []
    threads = [threading.Thread(target=lambda: results.append(container.tools.execute("create_booking", BOOKING, ctx, invoked_by="api"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({r.data.booking_id for r in results}) == 1
    assert len(container.reservations.inner.bookings_for(ctx.tenant)) == 1


def test_invalid_arguments_and_string_nulls(offline_container):
    ctx = tool_ctx(offline_container)
    bad = offline_container.tools.execute("check_availability", {"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": "three", "children": 0}, ctx)
    nulls = offline_container.tools.execute("request_booking_details", {"message": "Dates?", "check_in": "null", "check_out": "None", "adults": "", "children": None}, ctx)

    assert bad.error_code == ToolErrorCode.INVALID_ARGUMENTS
    assert nulls.ok and nulls.data.prefill.check_in is None and nulls.data.prefill.adults is None


def test_slow_tool_times_out(offline_container):
    class Args(BaseModel):
        pass

    class SlowTool:
        definition = ToolDefinition("slow", "slow", Args, {"type": "object"}, ToolPolicy.READ_ONLY, timeout_seconds=0.05)

        def run(self, ctx, args):
            time.sleep(0.5)

    registry = ToolRegistry([SlowTool()], FeatureFlags(), offline_container.metrics, offline_container.events)
    result = registry.execute("slow", {}, tool_ctx(offline_container))
    assert result.error_code == ToolErrorCode.TIMEOUT


# ---------- Resilience primitives ----------


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_circuit_breaker_opens_half_opens_and_closes():
    clock = FakeClock()
    breaker = CircuitBreaker("x", failure_threshold=2, reset_timeout_seconds=10, clock=clock)

    def boom():
        raise ConnectionError

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(boom)
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "never runs")

    clock.t = 11
    assert breaker.state == "half_open"
    assert breaker.call(lambda: "ok") == "ok"
    assert breaker.state == "closed"


def _raise(exc):
    raise exc


def _open_breaker(clock, threshold=1, is_failure=lambda exc: True):
    breaker = CircuitBreaker("x", failure_threshold=threshold, reset_timeout_seconds=10, clock=clock, is_failure=is_failure)
    for _ in range(threshold):
        with pytest.raises(ConnectionError):
            breaker.call(lambda: _raise(ConnectionError()))
    clock.t += 11
    assert breaker.state == "half_open"
    return breaker


def test_half_open_allows_exactly_one_concurrent_trial():
    breaker = _open_breaker(FakeClock())
    entered, release = threading.Event(), threading.Event()
    trial_calls, outcomes = [], []

    def trial():
        trial_calls.append(1)
        entered.set()
        release.wait(5)
        return "ok"

    def caller():
        try:
            outcomes.append(breaker.call(trial))
        except CircuitOpenError:
            outcomes.append("rejected")

    first = threading.Thread(target=caller)
    first.start()
    assert entered.wait(5)
    others = [threading.Thread(target=caller) for _ in range(5)]
    for t in others:
        t.start()
    for t in others:
        t.join(5)
    assert outcomes.count("rejected") == 5 and len(trial_calls) == 1  # concurrent callers fail fast
    release.set()
    first.join(5)
    assert "ok" in outcomes and breaker.state == "closed"


def test_failed_half_open_trial_reopens_for_a_full_cooldown():
    clock = FakeClock()
    breaker = _open_breaker(clock, threshold=3)
    with pytest.raises(ConnectionError):
        breaker.call(lambda: _raise(ConnectionError()))
    assert breaker.state == "open"  # one failed trial reopens, regardless of the threshold
    clock.t += 5
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "never runs")
    clock.t += 6
    assert breaker.state == "half_open"


def test_non_failure_error_during_trial_releases_the_permit():
    clock = FakeClock()
    breaker = _open_breaker(clock, is_failure=lambda exc: not isinstance(exc, ValueError))
    with pytest.raises(ValueError):
        breaker.call(lambda: _raise(ValueError()))  # the dependency answered; a caller-side error
    assert breaker.state == "closed" and breaker.call(lambda: "ok") == "ok"


def test_retry_only_retries_listed_errors():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError
        return "ok"

    assert retry(flaky, attempts=3, retry_on=(ConnectionError,), sleep=lambda _s: None) == "ok"
    with pytest.raises(ValueError):
        retry(lambda: (_ for _ in ()).throw(ValueError()), attempts=3, retry_on=(ConnectionError,), sleep=lambda _s: None)


class FlakyProvider:
    """Wraps the mock provider with scripted failures and call counting."""

    name = "flaky"

    def __init__(self, inner, failures: int = 0, delay: float = 0.0):
        self.inner, self.failures, self.delay, self.calls = inner, failures, delay, 0

    def check_availability(self, ctx, kb, query, today):
        self.calls += 1
        time.sleep(self.delay)
        if self.calls <= self.failures:
            raise ConnectionError("PMS unreachable")
        return self.inner.check_availability(ctx, kb, query, today)

    def create_booking(self, ctx, kb, request, key, today):
        self.calls += 1
        raise ConnectionError("PMS unreachable")

    def get_room(self, ctx, kb, room_id):
        return self.inner.get_room(ctx, kb, room_id)

    def is_healthy(self):
        return True


def resilient(inner, *, failures_to_open=3, ttl=0, timeout=1.0, retries=2):
    return ResilientReservationProvider(
        inner, CircuitBreaker("test", failures_to_open, 30), TTLCache(), timeout_seconds=timeout, read_retries=retries, availability_ttl_seconds=ttl
    )


@pytest.fixture
def setup(offline_container):
    mock = MockReservationProvider(offline_container.settings.data_dir, InMemoryIdempotencyStore())
    return offline_container.tenants.resolve(GOA), offline_container.knowledge.snapshot(GOA, TODAY), mock


def test_reads_are_retried_through_transient_failures(setup):
    ctx, kb, mock = setup
    flaky = FlakyProvider(mock, failures=2)
    result = resilient(flaky, failures_to_open=5).check_availability(ctx, kb, AvailabilityQuery(check_in=WED, check_out=THU, adults=2), TODAY)
    assert result.available and flaky.calls == 3


def test_persistent_failure_opens_the_circuit_and_fails_fast(setup):
    ctx, kb, mock = setup
    flaky = FlakyProvider(mock, failures=100)
    provider = resilient(flaky, failures_to_open=3, retries=2)
    query = AvailabilityQuery(check_in=WED, check_out=THU, adults=2)

    # Three logical calls (each with its own 3 attempts) are three breaker failures, not nine.
    errors = []
    for _ in range(3):
        with pytest.raises(ReservationError) as exc:
            provider.check_availability(ctx, kb, query, TODAY)
        errors.append(exc.value)
        assert flaky.calls == 3 * len(errors)
    calls_after_open = flaky.calls
    with pytest.raises(ReservationError) as blocked:
        provider.check_availability(ctx, kb, query, TODAY)

    assert all(e.code == ReservationErrorCode.UNAVAILABLE for e in [*errors, blocked.value])
    assert flaky.calls == calls_after_open  # circuit open: the PMS isn't called again
    assert not provider.is_healthy()


def test_one_logical_call_counts_as_one_breaker_failure(setup):
    ctx, kb, mock = setup
    flaky = FlakyProvider(mock, failures=100)
    provider = resilient(flaky, failures_to_open=2, retries=2)
    with pytest.raises(ReservationError):
        provider.check_availability(ctx, kb, AvailabilityQuery(check_in=WED, check_out=THU, adults=2), TODAY)
    assert flaky.calls == 3 and provider.breaker.state == "closed"


def test_retry_deadline_stops_new_attempts():
    now = [0.0]
    calls = []

    def slow_failure():
        calls.append(1)
        now[0] += 1.0  # each attempt burns 1 s of the budget
        raise ConnectionError

    def fake_sleep(seconds):
        now[0] += seconds

    with pytest.raises(ConnectionError):
        retry(slow_failure, attempts=10, retry_on=(ConnectionError,), base_delay=0.5, deadline_seconds=2.5, sleep=fake_sleep, clock=lambda: now[0])
    assert len(calls) < 10 and now[0] <= 2.5


def test_reservation_deadline_is_below_the_tool_timeout(offline_container):
    from app.tools.builtin import CheckAvailabilityTool

    assert offline_container.reservations.deadline < CheckAvailabilityTool.definition.timeout_seconds


def test_business_errors_do_not_trip_the_circuit(setup):
    ctx, kb, mock = setup
    provider = resilient(mock, failures_to_open=2)
    for _ in range(5):
        with pytest.raises(AvailabilityValidationError):
            provider.check_availability(ctx, kb, AvailabilityQuery(check_in=date(2020, 1, 1), check_out=date(2020, 1, 2), adults=2), TODAY)
    assert provider.breaker.state == "closed"
    assert provider.check_availability(ctx, kb, AvailabilityQuery(check_in=WED, check_out=THU, adults=2), TODAY).available


def test_timeouts_become_unavailable(setup):
    ctx, kb, mock = setup
    provider = resilient(FlakyProvider(mock, delay=0.3), timeout=0.05, retries=0)
    with pytest.raises(ReservationError) as exc:
        provider.check_availability(ctx, kb, AvailabilityQuery(check_in=WED, check_out=THU, adults=2), TODAY)
    assert exc.value.code == ReservationErrorCode.UNAVAILABLE


def test_mutations_are_never_retried(setup):
    ctx, kb, mock = setup
    flaky = FlakyProvider(mock)
    with pytest.raises(ReservationError):
        resilient(flaky, retries=5).create_booking(ctx, kb, None, "idem-key-0001", TODAY)
    assert flaky.calls == 1


def test_availability_cache_respects_ttl(setup):
    ctx, kb, mock = setup
    query = AvailabilityQuery(check_in=WED, check_out=THU, adults=2)
    cached, uncached = FlakyProvider(mock), FlakyProvider(mock)
    provider = resilient(cached, ttl=60)
    no_cache = resilient(uncached, ttl=0)
    for _ in range(3):
        provider.check_availability(ctx, kb, query, TODAY)
        no_cache.check_availability(ctx, kb, query, TODAY)
    assert cached.calls == 1 and uncached.calls == 3


def test_idempotency_key_must_be_meaningful():
    store = InMemoryIdempotencyStore()
    with pytest.raises(ReservationError):
        store.run_once("s", "short", {}, lambda: 1)


# ---------- End to end: integration outage ----------


def test_availability_outage_returns_503_with_stable_code():
    container = make_container()
    container.reservations.inner = FlakyProvider(container.reservations.inner, failures=1000)
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(container=container)) as client:
        response = client.post(f"/api/v1/hotels/{GOA}/availability", json={"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RESERVATION_UNAVAILABLE"
    assert response.headers["Retry-After"] == "30"


def test_model_availability_call_during_outage_gives_a_safe_reply(fake_messages):
    container = make_container(fake_messages)
    container.reservations.inner = FlakyProvider(container.reservations.inner, failures=1000)
    fake_messages.responses.append(tool_response("check_availability", {"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 2, "children": 0}))

    from tests.conftest import turn_request

    outcome = container.assistant.handle(turn_request(container, "Rooms for 2 on 7 Oct?"))

    assert outcome.reply.type == "fallback"
    assert "can't check live availability" in outcome.reply.text
    assert outcome.trace.fallback_reason == "reservation_unavailable"
    assert outcome.trace.tool_calls[0].error_code == ToolErrorCode.DEPENDENCY_UNAVAILABLE
