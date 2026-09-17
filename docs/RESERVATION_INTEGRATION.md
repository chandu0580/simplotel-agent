# Reservation integration contract

The assistant reads inventory and changes bookings only through `ReservationProvider` (`backend/app/reservations/provider.py`). Two implementations exist: `MockReservationProvider` (default, deterministic demo inventory from per-hotel `inventory.json`) and `CloudbedsReservationProvider` (`RESERVATION_PROVIDER=cloudbeds`, live availability and rates from the Cloudbeds PMS). This page states what an adapter for any such system must guarantee, so another one can be added without touching the assistant, tools or API.

Status: interface, mock, resilience wrapper and the Cloudbeds adapter are **IMPLEMENTED + TESTED** against stub transports. Whether the Cloudbeds adapter has been run against a real account is recorded in [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md); booking writes are **NOT IMPLEMENTED** by design.

## Interface

```python
class ReservationProvider(Protocol):
    name: str
    def check_availability(self, ctx: TenantContext, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult: ...
    def get_room(self, ctx: TenantContext, kb: KnowledgeBase, room_id: str) -> Room | None: ...
    def create_booking(self, ctx: TenantContext, kb: KnowledgeBase, request: BookingRequest, idempotency_key: str, today: date) -> Booking: ...
    def modify_booking(self, ctx: TenantContext, booking_id: str, changes: dict, idempotency_key: str) -> Booking: ...
    def cancel_booking(self, ctx: TenantContext, booking_id: str, idempotency_key: str) -> Booking: ...
    def is_healthy(self) -> bool: ...
```

| Concern | Contract |
|---|---|
| Tenant context | Every call receives `TenantContext` (tenant, hotel, channel, request and trace ids). The adapter must map the hotel to the external property and must never return data for another hotel. The mock rejects a knowledge snapshot from a different hotel |
| Typed results | Pydantic models: `AvailabilityResult` (rooms, prices, nights, sold-out names, message), `Booking` (id, tenant, hotel, status, dates, party, total, currency, created_at). No raw vendor payloads leave the adapter |
| Guest input errors | Raise `AvailabilityValidationError` (past dates, check-out before check-in, party too large). These go back to the guest as form errors and **never** count toward the circuit breaker |
| Business errors | Raise `ReservationError` with `NOT_AVAILABLE`, `INVALID_REQUEST`, `NOT_SUPPORTED`, `IDEMPOTENCY_CONFLICT` or `IN_PROGRESS`. Not retried, and they don't trip the breaker |
| Dependency errors | Raise `ReservationError(UNAVAILABLE, retryable=True)`, `ConnectionError` or let the wrapper time out. These are retried (reads only) and counted by the breaker. The API returns 503 `RESERVATION_UNAVAILABLE` with `Retry-After: 30`, and chat returns a safe reply with the front-desk contact |
| Idempotency | `create_booking`, `modify_booking` and `cancel_booking` take an idempotency key (at least 8 characters). The same key and request return the original result; the same key with a different request raises `IDEMPOTENCY_CONFLICT`. The adapter should also **forward the key to the external system** if it supports one (see below) |
| Money | Integer amounts in the hotel currency; prices come only from the provider, never from the model |
| Health | `is_healthy()` is cheap and never raises. Readiness reports `reservations: degraded` but keeps the instance in service |

## What the platform adds around every adapter

`ResilientReservationProvider` wraps whatever the container builds (`backend/app/container.py`):

| Policy | Behaviour | Test |
|---|---|---|
| Timeout | Each attempt runs in a bounded executor with `RESERVATION_TIMEOUT_SECONDS` (5 s) | `test_timeouts_become_unavailable` |
| Retries | Reads only (`check_availability`, `get_room`): up to `RESERVATION_READ_RETRIES` (2) with jittered backoff, only on timeouts and connection errors. **Mutations are never retried** | `test_reads_are_retried_through_transient_failures`, `test_mutations_are_never_retried` |
| Overall deadline | No new attempt starts after `timeout × (retries + 1) + 1 s` (16 s). That budget is below the `check_availability` tool timeout (20 s) | `test_retry_deadline_stops_new_attempts`, `test_reservation_deadline_is_below_the_tool_timeout` |
| Circuit breaker | Opens after `CIRCUIT_BREAKER_FAILURES` logical-call failures (a call and its retries count once). After `CIRCUIT_BREAKER_RESET_SECONDS` it allows exactly one trial call, concurrent callers fail fast, and a failed trial reopens it. Guest-input and business errors never count | `test_half_open_allows_exactly_one_concurrent_trial`, `test_one_logical_call_counts_as_one_breaker_failure`, `test_business_errors_do_not_trip_the_circuit` |
| Read cache | Availability results cached per process for `AVAILABILITY_CACHE_TTL_SECONDS` (15 s; 0 disables) | `test_availability_cache_respects_ttl` |
| Idempotency store | In memory or Redis (`STATE_BACKEND`). With Redis, one replica takes a lease and runs the operation, the others wait for its stored result, and a key still in progress after the wait budget raises `IN_PROGRESS` | `test_idempotency_runs_once_across_stores`, `test_duplicate_booking_on_three_replicas_creates_one_booking` |
| Audit | The booking tool publishes `BookingRequested` per attempt and `BookingConfirmed` with a deterministic id (one durable row per booking). Availability searches publish `AvailabilityChecked`. Events carry ids and counts only | `test_idempotent_booking_replays_record_one_confirmation` |
| Authorization | `create_booking` is a MUTATING tool: behind `booking_tools_enabled`, not exposed to the model, requires an authenticated guest principal for that hotel, explicit guest confirmation and an idempotency key | `test_mutating_tool_authorization` |

## Known limits an adapter must account for

- **Timeouts don't cancel work.** When an attempt times out, the platform stops waiting, but the Python thread and any HTTP call it made keep running. A booking can still be created after the guest saw an error. The idempotency key is what makes a retry safe, so it must reach the external system.
- **Lease expiry.** The Redis idempotency lease is 60 s. If an external call outlives it, a second attempt can start. The durable backstop is the `bookings` table's unique `(tenant_id, hotel_id, idempotency_key)` constraint (schema exists, and the uniqueness is tested against PostgreSQL). Writing bookings to that table is **not wired** yet, because the mock keeps bookings in memory.
- **Per-process breaker.** Each replica learns about an outage separately. That delays detection by up to N failures per replica; it doesn't cause unbounded load.
- **Modify and cancel** raise `NOT_SUPPORTED` in the mock and have no API endpoints.

## Cloudbeds adapter (`RESERVATION_PROVIDER=cloudbeds`)

`app/reservations/cloudbeds_provider.py` implements the interface against the documented Cloudbeds API v1.3.

| Aspect | Behaviour |
|---|---|
| Endpoint | `GET {CLOUDBEDS_BASE_URL}/getAvailableRoomTypes` with `startDate`, `endDate`, `rooms=1`, `adults`, `children`, `propertyIDs`, `detailedRates=true` |
| Auth | `x-api-key` header, sent per request; the key is a secret (never logged, excluded from `repr`) |
| Hotel → property | `CLOUDBEDS_PROPERTY_IDS` (`<hotel_id>=<propertyID>`). An unmapped hotel is `INVALID_REQUEST`, not a crash |
| Guest input | `validate_search` runs **before** any HTTP call, so past dates, inverted dates, stays over 30 nights and empty parties never reach the PMS and the guest sees the same messages as with the mock |
| Mapping | `roomTypeID` → `room_id`, `roomTypeName` → name, `maxGuests` → max occupancy, `roomsAvailable` → rooms left, `roomRateDetailed` summed (or `roomRate` × nights) → total, `propertyCurrency[0].currencyCode` → currency, HTML stripped from descriptions |
| Hotel content | Bed configuration, room size and breakfast inclusion are **not** PMS data. They come from the knowledge base when a room type matches by id or name; otherwise they are left empty and `breakfast_included` is `null`, so the UI shows no badge instead of a wrong claim |
| Sold out | Room types that fit the party with `roomsAvailable = 0` are reported in `sold_out_room_names` |
| Failures | Timeouts, transport errors, 429 and 5xx → `ConnectionError` (retried by the wrapper). 401/403 → `UNAVAILABLE` with a generic guest message, and only the status is logged. `success: false` or an unreadable body → `UNAVAILABLE`; the vendor message is logged, never shown to the guest |
| Bookings | `create_booking`, `modify_booking` and `cancel_booking` raise `NOT_SUPPORTED`. `postReservation` requires guest name, country, postcode, email and a payment method, which this assistant deliberately does not collect ([PRIVACY.md](PRIVACY.md)). Enabling it is a product decision, not a code gap |

Covered by `tests/test_cloudbeds_provider.py` (17 tests over a stub transport) and the shared contract test in `tests/test_contracts.py`. **Live verification against a Cloudbeds sandbox is a separate step; see [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md) for its current status.**

## Adding a real adapter (checklist)

1. Implement `ReservationProvider` and map vendor errors to the three categories above.
2. Pass the idempotency key to the vendor API, or keep a local outbox keyed by it.
3. Add the adapter to the `reservations` fixture in `tests/test_contracts.py`. It must pass `test_reservation_provider_contract` against a recorded or sandboxed vendor.
4. Select it in `build_container` from configuration. The wrapper, tools, API and evals need no changes.
