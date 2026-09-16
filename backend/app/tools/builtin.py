"""Built-in tools.

* `check_availability` (read-only): deterministic search through the ReservationProvider.
* `request_booking_details` (read-only): asks the UI for a date/guest form.
* `create_booking` (mutating): not exposed to the model and behind `booking_tools_enabled`.
  It exists to exercise the authorization, confirmation and idempotency path end to end.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from ..auth.principal import Role
from ..core.events import DomainEvent, EventPublisher
from ..core.metrics import Metrics
from ..reservations.availability import AvailabilityValidationError
from ..reservations.models import AvailabilityQuery, BookingRequest, ReservationError, ReservationErrorCode
from ..reservations.provider import ReservationProvider
from ..schemas import AvailabilityResult, BookingPrefill
from .base import ToolContext, ToolDefinition, ToolErrorCode, ToolExecutionError, ToolPolicy


class NeedsDetails(BaseModel):
    """Outcome asking the guest for (more) booking details."""

    prefill: BookingPrefill
    message: str | None = None
    form_error: str | None = None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class BookingArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    adults: int | None = None
    children: int | None = None


def merge_with_context(args: BookingArgs, ctx: ToolContext) -> BookingPrefill:
    booking = ctx.booking_context
    check_in, check_out = _parse_date(args.check_in), _parse_date(args.check_out)
    # Reuse remembered dates only when no dates were given; mixing a new check-in with an old
    # check-out could produce an impossible stay.
    if args.check_in is None and args.check_out is None and booking:
        check_in, check_out = booking.check_in, booking.check_out
    return BookingPrefill(
        check_in=check_in,
        check_out=check_out,
        adults=args.adults if args.adults is not None else (booking.adults if booking else None),
        children=args.children if args.children is not None else (booking.children if booking else None),
    )


class CheckAvailabilityTool:
    definition = ToolDefinition(
        name="check_availability",
        description=(
            "Look up live room availability and total prices for a stay. Call only when check-in date, "
            "check-out date and number of adults are all known. Returns the room types that fit the party "
            "and have inventory for every night; the website shows the result to the guest directly."
        ),
        args_model=BookingArgs,
        input_schema={
            "type": "object",
            "properties": {
                "check_in": {"type": "string", "description": "Check-in date, YYYY-MM-DD"},
                "check_out": {"type": "string", "description": "Check-out date, YYYY-MM-DD"},
                "adults": {"type": "integer", "description": "Number of adults (1 or more)"},
                "children": {"type": "integer", "description": "Number of children; 0 if not mentioned"},
            },
            "required": ["check_in", "check_out", "adults", "children"],
            "additionalProperties": False,
        },
        policy=ToolPolicy.READ_ONLY,
        timeout_seconds=20.0,  # above ResilientReservationProvider's retry deadline (16 s with defaults)
    )

    def __init__(self, reservations: ReservationProvider, metrics: Metrics, events: EventPublisher):
        self.reservations = reservations
        self.metrics = metrics
        self.events = events

    def run(self, ctx: ToolContext, args: BookingArgs) -> AvailabilityResult | NeedsDetails:
        prefill = merge_with_context(args, ctx)
        if not (prefill.check_in and prefill.check_out and prefill.adults):
            return NeedsDetails(prefill=prefill, message="Please confirm your dates and number of guests so I can check availability.")
        query = AvailabilityQuery.model_construct(
            check_in=prefill.check_in, check_out=prefill.check_out, adults=prefill.adults, children=prefill.children or 0
        )
        try:
            result = self.reservations.check_availability(ctx.tenant, ctx.kb, query, ctx.today)
        except AvailabilityValidationError as exc:
            return NeedsDetails(prefill=prefill, message=f"{exc} Please adjust your details.", form_error=str(exc))
        except ReservationError as exc:
            code = ToolErrorCode.DEPENDENCY_UNAVAILABLE if exc.code == ReservationErrorCode.UNAVAILABLE else ToolErrorCode.EXECUTION_FAILED
            raise ToolExecutionError(code, exc.message) from exc
        record_availability_search(self.metrics, self.events, ctx, result, source="assistant")
        return result


def record_availability_search(metrics: Metrics, events: EventPublisher, ctx: ToolContext, result: AvailabilityResult, source: str) -> None:
    metrics.availability_search_total.labels(source, str(result.available).lower()).inc()
    events.publish(
        DomainEvent(
            name="AvailabilityChecked",
            tenant_id=ctx.tenant.tenant_id,
            hotel_id=ctx.tenant.hotel_id,
            conversation_id=ctx.tenant.conversation_id,
            channel=ctx.tenant.channel,
            data={"source": source, "nights": result.nights, "guests": result.adults + result.children, "available": result.available, "room_types": len(result.rooms)},
        )
    )


class RequestBookingDetailsTool:
    definition = ToolDefinition(
        name="request_booking_details",
        description=(
            "Show the guest a form to pick check-in date, check-out date and number of guests. Use when the guest "
            "wants to check availability or prices but any of those details is missing or ambiguous. Pass any "
            "details already known so the form is pre-filled; use null for unknown values."
        ),
        args_model=BookingArgs,
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "One short sentence to the guest explaining what you need."},
                "check_in": {"type": ["string", "null"], "description": "YYYY-MM-DD if known"},
                "check_out": {"type": ["string", "null"], "description": "YYYY-MM-DD if known"},
                "adults": {"type": ["integer", "null"]},
                "children": {"type": ["integer", "null"]},
            },
            "required": ["message", "check_in", "check_out", "adults", "children"],
            "additionalProperties": False,
        },
        policy=ToolPolicy.READ_ONLY,
        timeout_seconds=2.0,
    )

    def run(self, ctx: ToolContext, args: BookingArgs) -> NeedsDetails:
        message = (args.message or "").strip() or "Please choose your dates and number of guests."
        return NeedsDetails(prefill=merge_with_context(args, ctx), message=message)


class CreateBookingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_id: str
    check_in: date
    check_out: date
    adults: int
    children: int = 0


class CreateBookingTool:
    definition = ToolDefinition(
        name="create_booking",
        description="Create a reservation for an authenticated guest after they explicitly confirm the room, dates and price.",
        args_model=CreateBookingArgs,
        input_schema={
            "type": "object",
            "properties": {
                "room_id": {"type": "string"},
                "check_in": {"type": "string"},
                "check_out": {"type": "string"},
                "adults": {"type": "integer"},
                "children": {"type": "integer"},
            },
            "required": ["room_id", "check_in", "check_out", "adults", "children"],
            "additionalProperties": False,
        },
        policy=ToolPolicy.MUTATING,
        timeout_seconds=20.0,
        exposed_to_model=False,
        required_flag="booking_tools_enabled",
        required_roles=frozenset({Role.GUEST}),
        requires_confirmation=True,
    )

    def __init__(self, reservations: ReservationProvider, events: EventPublisher):
        self.reservations = reservations
        self.events = events

    def run(self, ctx: ToolContext, args: CreateBookingArgs):
        assert ctx.principal is not None and ctx.idempotency_key  # enforced by the registry
        self.events.publish(
            DomainEvent(name="BookingRequested", tenant_id=ctx.tenant.tenant_id, hotel_id=ctx.tenant.hotel_id, conversation_id=ctx.tenant.conversation_id, channel=ctx.tenant.channel, data={"room_id": args.room_id})
        )
        request = BookingRequest(guest_reference=ctx.principal.subject, **args.model_dump())
        try:
            booking = self.reservations.create_booking(ctx.tenant, ctx.kb, request, ctx.idempotency_key, ctx.today)
        except AvailabilityValidationError as exc:
            raise ToolExecutionError(ToolErrorCode.BUSINESS_RULE, str(exc)) from exc
        except ReservationError as exc:
            code = ToolErrorCode.DEPENDENCY_UNAVAILABLE if exc.code == ReservationErrorCode.UNAVAILABLE else ToolErrorCode.BUSINESS_RULE
            raise ToolExecutionError(code, exc.message) from exc
        # Deterministic id: idempotent replays (on any replica) re-announce the same event, which the audit
        # store's primary key deduplicates, so one booking has exactly one durable confirmation.
        self.events.publish(
            DomainEvent(
                event_id=f"booking-confirmed-{booking.booking_id}",
                name="BookingConfirmed",
                tenant_id=ctx.tenant.tenant_id,
                hotel_id=ctx.tenant.hotel_id,
                conversation_id=ctx.tenant.conversation_id,
                channel=ctx.tenant.channel,
                data={"booking_id": booking.booking_id},
            )
        )
        return booking
