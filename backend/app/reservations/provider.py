"""Reservation integration boundary.

`ReservationProvider` is the only way the platform reads inventory or changes bookings.
`MockReservationProvider` is the development implementation; a PMS, CRS or channel-manager
integration would implement the same interface. `ResilientReservationProvider` wraps any
implementation with timeouts, read retries, a circuit breaker and short-TTL read caching.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
import uuid

from ..core.cache import Cache
from ..core.resilience import CircuitBreaker, CircuitOpenError, IntegrationTimeout, call_with_timeout, retry
from ..knowledge.models import KnowledgeBase, Room
from ..schemas import AvailabilityResult
from ..tenancy import TenantContext
from .availability import AvailabilityValidationError, Inventory, check_availability, load_inventory
from .idempotency import IdempotencyStore
from .models import AvailabilityQuery, Booking, BookingRequest, BookingStatus, ReservationError, ReservationErrorCode


class ReservationProvider(Protocol):
    name: str

    def check_availability(self, ctx: TenantContext, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult:
        """Raises AvailabilityValidationError for business-rule violations, ReservationError otherwise."""

    def get_room(self, ctx: TenantContext, kb: KnowledgeBase, room_id: str) -> Room | None: ...
    def create_booking(self, ctx: TenantContext, kb: KnowledgeBase, request: BookingRequest, idempotency_key: str, today: date) -> Booking: ...
    def modify_booking(self, ctx: TenantContext, booking_id: str, changes: dict, idempotency_key: str) -> Booking: ...
    def cancel_booking(self, ctx: TenantContext, booking_id: str, idempotency_key: str) -> Booking: ...
    def is_healthy(self) -> bool: ...


class MockReservationProvider:
    """Deterministic inventory from per-hotel JSON. Bookings are held in memory only."""

    name = "mock"

    def __init__(self, data_dir: Path, idempotency: IdempotencyStore):
        self.hotels_dir = data_dir / "hotels"
        self.idempotency = idempotency
        self._inventories: dict[str, Inventory] = {}
        self._bookings: dict[str, Booking] = {}

    def _inventory(self, hotel_id: str) -> Inventory:
        if hotel_id not in self._inventories:
            self._inventories[hotel_id] = load_inventory(self.hotels_dir / hotel_id / "inventory.json")
        return self._inventories[hotel_id]

    def check_availability(self, ctx: TenantContext, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult:
        if kb.hotel.id != ctx.hotel_id:
            raise ReservationError(ReservationErrorCode.INVALID_REQUEST, "Knowledge snapshot does not belong to this hotel.")
        return check_availability(
            query.check_in, query.check_out, query.adults, query.children, today=today, kb=kb, inventory=self._inventory(ctx.hotel_id)
        )

    def get_room(self, ctx: TenantContext, kb: KnowledgeBase, room_id: str) -> Room | None:
        return kb.room(room_id) if kb.hotel.id == ctx.hotel_id else None

    def create_booking(self, ctx: TenantContext, kb: KnowledgeBase, request: BookingRequest, idempotency_key: str, today: date) -> Booking:
        def operation() -> Booking:
            result = self.check_availability(
                ctx, kb, AvailabilityQuery(check_in=request.check_in, check_out=request.check_out, adults=request.adults, children=request.children), today
            )
            offer = next((r for r in result.rooms if r.room_id == request.room_id), None)
            if offer is None:
                raise ReservationError(ReservationErrorCode.NOT_AVAILABLE, "That room is not available for these dates.")
            booking = Booking(
                booking_id=f"BK-{uuid.uuid4().hex[:10].upper()}",
                tenant_id=ctx.tenant_id,
                hotel_id=ctx.hotel_id,
                status=BookingStatus.CONFIRMED,
                room_id=request.room_id,
                check_in=request.check_in,
                check_out=request.check_out,
                adults=request.adults,
                children=request.children,
                total_price=offer.total_price,
                currency=offer.currency,
                created_at=datetime.now(UTC),
            )
            self._bookings[booking.booking_id] = booking
            return booking

        return self.idempotency.run_once(f"booking:{ctx.tenant_id}:{ctx.hotel_id}", idempotency_key, request.model_dump(mode="json"), operation, result_model=Booking)

    def modify_booking(self, ctx: TenantContext, booking_id: str, changes: dict, idempotency_key: str) -> Booking:
        raise ReservationError(ReservationErrorCode.NOT_SUPPORTED, "Booking changes are not supported by the mock provider.")

    def cancel_booking(self, ctx: TenantContext, booking_id: str, idempotency_key: str) -> Booking:
        raise ReservationError(ReservationErrorCode.NOT_SUPPORTED, "Cancellations are not supported by the mock provider.")

    def bookings_for(self, ctx: TenantContext) -> list[Booking]:
        return [b for b in self._bookings.values() if b.tenant_id == ctx.tenant_id and b.hotel_id == ctx.hotel_id]

    def is_healthy(self) -> bool:
        return True


class _BusinessOutcome:
    def __init__(self, error: Exception):
        self.error = error


class ResilientReservationProvider:
    """Timeouts everywhere; retries and caching only for reads; circuit breaker per wrapped provider."""

    def __init__(
        self,
        inner: ReservationProvider,
        breaker: CircuitBreaker,
        cache: Cache,
        *,
        timeout_seconds: float,
        read_retries: int,
        availability_ttl_seconds: int,
    ):
        self.inner = inner
        self.name = inner.name
        self.breaker = breaker
        self.cache = cache
        self.timeout = timeout_seconds
        self.read_retries = read_retries
        self.availability_ttl = availability_ttl_seconds
        # Overall budget for one logical call (all attempts + backoff). Tools that call this provider
        # use a timeout above this budget, so retries never outlast the caller.
        self.deadline = timeout_seconds * (read_retries + 1) + 1.0

    def _guarded(self, fn, *, retries: int):
        def shielded():
            # Business outcomes (invalid dates, sold out) are answers, not integration failures:
            # return them through the breaker so they never trip it, then re-raise.
            try:
                return call_with_timeout(fn, self.timeout)
            except AvailabilityValidationError as exc:
                return _BusinessOutcome(exc)
            except ReservationError as exc:
                if exc.code == ReservationErrorCode.UNAVAILABLE:
                    raise
                return _BusinessOutcome(exc)

        def with_retries():
            return retry(shielded, attempts=1 + retries, retry_on=(IntegrationTimeout, ConnectionError), deadline_seconds=self.deadline)

        try:
            # The breaker wraps the whole retry sequence: one logical call counts as one failure.
            result = self.breaker.call(with_retries)
        except CircuitOpenError as exc:
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "Reservation system is temporarily unavailable.", retryable=True) from exc
        except (IntegrationTimeout, ConnectionError) as exc:
            raise ReservationError(ReservationErrorCode.UNAVAILABLE, "Reservation system did not respond in time.", retryable=True) from exc
        if isinstance(result, _BusinessOutcome):
            raise result.error
        return result

    def check_availability(self, ctx: TenantContext, kb: KnowledgeBase, query: AvailabilityQuery, today: date) -> AvailabilityResult:
        key = f"availability:{ctx.tenant_id}:{ctx.hotel_id}:{today}:{query.model_dump_json()}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = self._guarded(lambda: self.inner.check_availability(ctx, kb, query, today), retries=self.read_retries)
        self.cache.set(key, result, self.availability_ttl)
        return result

    def get_room(self, ctx: TenantContext, kb: KnowledgeBase, room_id: str) -> Room | None:
        return self._guarded(lambda: self.inner.get_room(ctx, kb, room_id), retries=self.read_retries)

    # Mutations: never retried here. The idempotency key makes a *caller's* retry safe.
    def create_booking(self, ctx: TenantContext, kb: KnowledgeBase, request: BookingRequest, idempotency_key: str, today: date) -> Booking:
        return self._guarded(lambda: self.inner.create_booking(ctx, kb, request, idempotency_key, today), retries=0)

    def modify_booking(self, ctx: TenantContext, booking_id: str, changes: dict, idempotency_key: str) -> Booking:
        return self._guarded(lambda: self.inner.modify_booking(ctx, booking_id, changes, idempotency_key), retries=0)

    def cancel_booking(self, ctx: TenantContext, booking_id: str, idempotency_key: str) -> Booking:
        return self._guarded(lambda: self.inner.cancel_booking(ctx, booking_id, idempotency_key), retries=0)

    def is_healthy(self) -> bool:
        return self.breaker.state != "open" and self.inner.is_healthy()
