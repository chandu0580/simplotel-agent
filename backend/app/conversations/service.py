"""Conversation service: server-side conversation state for every channel.

Context strategy:
* Short-term context is the last `context_window` messages plus the structured availability
  context (dates, guests). The server keeps it, so clients can't forge history.
* Stored messages are capped at `max_messages` (oldest dropped). There is no LLM summarisation
  yet; hotel conversations are short, and a summariser would add cost and a new failure mode.
* Long-lived guest preferences are deliberately not stored.
* Conversations expire after `ttl`; guests can delete theirs at any time.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
import uuid

from pydantic import ValidationError

from ..assistant.service import AssistantService, TurnOutcome
from ..assistant.turn import TurnRequest
from ..core.clock import Clock, local_today
from ..core.errors import AppError, ErrorCode
from ..core.events import DomainEvent, EventPublisher
from ..core.locks import LockNotAcquired, LockStore, held
from ..core.metrics import Metrics
from ..core.observability import bind_context
from ..knowledge.provider import KnowledgeProvider
from ..reservations.availability import AvailabilityValidationError
from ..reservations.models import AvailabilityQuery, ReservationError, ReservationErrorCode
from ..reservations.provider import ReservationProvider
from ..schemas import AvailabilityResult, BookingContext, ChatHistoryItem, ChatReply
from ..tenancy import TenantContext
from ..tools.base import ToolContext
from ..tools.builtin import record_availability_search
from .models import Conversation, StoredMessage
from .repository import ConversationConflict, ConversationRepository

_BUSY = "This conversation is busy with another message. Please try again in a moment."


def offer_lines(result: AvailabilityResult) -> list[str]:
    """What the guest was actually quoted, so a later turn cites those prices, not indicative rates."""
    return [f"{room.name}: {room.currency} {room.nightly_rate:,} per night, {room.currency} {room.total_price:,} total" for room in result.rooms]


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        locks: LockStore,
        assistant: AssistantService,
        knowledge: KnowledgeProvider,
        reservations: ReservationProvider,
        events: EventPublisher,
        metrics: Metrics,
        clock: Clock,
        *,
        ttl_seconds: int,
        max_messages: int,
        context_window: int,
        lock_lease_seconds: float = 120.0,
        lock_wait_seconds: float = 30.0,
    ):
        self.repository = repository
        self.locks = locks
        self.lock_lease = lock_lease_seconds
        self.lock_wait = lock_wait_seconds
        self.assistant = assistant
        self.knowledge = knowledge
        self.reservations = reservations
        self.events = events
        self.metrics = metrics
        self.clock = clock
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_messages = max_messages
        self.context_window = context_window

    @contextmanager
    def _exclusive(self, ctx: TenantContext, conversation_id: str) -> Iterator[None]:
        """Serialise turns on one conversation (across replicas with a shared LockStore).

        The lock keeps concurrent turns from wasting model calls; the version check in `save` is what
        guarantees no lost update if a lease expires mid-turn.
        """
        bind_context(conversation_id=conversation_id)
        try:
            with held(self.locks, f"conv:{ctx.tenant_id}:{ctx.hotel_id}:{conversation_id}", lease_seconds=self.lock_lease, wait_seconds=self.lock_wait):
                yield
        except LockNotAcquired as exc:
            raise AppError(ErrorCode.CONVERSATION_BUSY, _BUSY, 409, headers={"Retry-After": "2"}) from exc

    def start(self, ctx: TenantContext, locale: str | None = None) -> Conversation:
        now = self.clock.now()
        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex}",
            tenant_id=ctx.tenant_id,
            hotel_id=ctx.hotel_id,
            channel=ctx.channel,
            locale=locale,
            created_at=now,
            updated_at=now,
            expires_at=now + self.ttl,
        )
        self.repository.save(conversation, expected_version=None)
        bind_context(conversation_id=conversation.id)
        self._event("ConversationStarted", ctx.with_conversation(conversation.id), {"locale": locale})
        return conversation

    def get(self, ctx: TenantContext, conversation_id: str) -> Conversation:
        bind_context(conversation_id=conversation_id)
        conversation = self.repository.get(ctx.tenant_id, ctx.hotel_id, conversation_id)
        if conversation is None:
            raise AppError(ErrorCode.CONVERSATION_NOT_FOUND, "Conversation not found or expired.", 404)
        return conversation

    def delete(self, ctx: TenantContext, conversation_id: str) -> None:
        if not self.repository.delete(ctx.tenant_id, ctx.hotel_id, conversation_id):
            raise AppError(ErrorCode.CONVERSATION_NOT_FOUND, "Conversation not found or expired.", 404)
        self._event("ConversationDeleted", ctx.with_conversation(conversation_id), {})

    def post_message(self, ctx: TenantContext, conversation_id: str, message: str, locale: str | None = None) -> tuple[Conversation, TurnOutcome]:
        with self._exclusive(ctx, conversation_id):
            return self._post_message(ctx, conversation_id, message, locale)

    def _post_message(self, ctx: TenantContext, conversation_id: str, message: str, locale: str | None) -> tuple[Conversation, TurnOutcome]:
        conversation = self.get(ctx, conversation_id)
        ctx = ctx.with_conversation(conversation.id)
        if locale:
            conversation.locale = locale
        history = [ChatHistoryItem(role=m.role, content=m.content[:4000]) for m in conversation.messages[-self.context_window :]]
        request = TurnRequest(
            tenant=ctx,
            message=message,
            history=history,
            booking_context=conversation.availability_context,
            recent_offers=conversation.recent_offers,
            locale=conversation.locale,
        )
        outcome = self.assistant.handle(request)
        # handle() minimised request.message (app.core.privacy): store that, never the raw text.
        self._append(conversation, "user", request.message, None)
        self._append(conversation, "assistant", outcome.reply.text, outcome.reply.type)
        self._update_context(conversation, outcome.reply)
        self._touch_and_save(conversation)
        return conversation, outcome

    def check_availability(self, ctx: TenantContext, conversation_id: str | None, query: AvailabilityQuery) -> AvailabilityResult:
        """Deterministic search from the booking form (no LLM). Recorded in the conversation when one is given."""
        if conversation_id is None:
            return self._check_availability(ctx, None, query)
        with self._exclusive(ctx, conversation_id):  # same lock as chat turns
            return self._check_availability(ctx, conversation_id, query)

    def _check_availability(self, ctx: TenantContext, conversation_id: str | None, query: AvailabilityQuery) -> AvailabilityResult:
        conversation = self.get(ctx, conversation_id) if conversation_id else None
        if conversation:
            ctx = ctx.with_conversation(conversation.id)
        profile = self.knowledge.profile(ctx.hotel_id)
        today = local_today(self.clock, profile.timezone)
        kb = self.knowledge.snapshot(ctx.hotel_id, today)
        try:
            result = self.reservations.check_availability(ctx, kb, query, today)
        except AvailabilityValidationError as exc:
            raise AppError(ErrorCode.INVALID_BOOKING_DETAILS, str(exc), 422) from exc
        except ReservationError as exc:
            if exc.code != ReservationErrorCode.UNAVAILABLE:
                raise  # non-transient: a bug or misconfiguration, surfaced as a 500
            raise AppError(ErrorCode.RESERVATION_UNAVAILABLE, "Availability is temporarily unavailable. Please try again shortly.", 503, headers={"Retry-After": "30"}) from exc
        record_availability_search(self.metrics, self.events, ToolContext(tenant=ctx, kb=kb, today=today), result, source="form")
        if conversation:
            summary = f"Check availability: {query.check_in} to {query.check_out}, {query.adults} adults, {query.children} children"
            self._append(conversation, "user", summary, None)
            self._append(conversation, "assistant", result.message, "availability")
            conversation.availability_context = BookingContext(check_in=query.check_in, check_out=query.check_out, adults=query.adults, children=query.children)
            conversation.recent_offers = offer_lines(result)  # the form path shows prices too
            conversation.active_intent = "availability"
            self._touch_and_save(conversation)
        return result

    def _append(self, conversation: Conversation, role: str, content: str, reply_type: str | None) -> None:
        conversation.messages.append(StoredMessage(role=role, content=content, reply_type=reply_type, created_at=self.clock.now()))
        del conversation.messages[: -self.max_messages]

    def _update_context(self, conversation: Conversation, reply: ChatReply) -> None:
        if reply.availability:
            conversation.recent_offers = offer_lines(reply.availability)
        candidate: dict | None = None
        if reply.availability:
            a = reply.availability
            candidate = {"check_in": a.check_in, "check_out": a.check_out, "adults": a.adults, "children": a.children}
        elif reply.booking_prefill:
            known = {k: v for k, v in reply.booking_prefill.model_dump().items() if v is not None}
            candidate = {**(conversation.availability_context.model_dump() if conversation.availability_context else {}), **known}
        if candidate is not None:
            try:
                conversation.availability_context = BookingContext(**candidate)
            except ValidationError:
                pass  # out-of-range values (e.g. a 40-adult group) are not remembered as form defaults
        conversation.active_intent = "availability" if reply.type in ("availability", "collect_booking_details") else "information"

    def _touch_and_save(self, conversation: Conversation) -> None:
        now: datetime = self.clock.now()
        conversation.updated_at = now
        conversation.expires_at = now + self.ttl  # sliding expiry while the guest is active
        expected = conversation.version
        conversation.version += 1
        try:
            self.repository.save(conversation, expected_version=expected)
        except ConversationConflict as exc:
            self.metrics.conversation_conflicts_total.inc()
            raise AppError(ErrorCode.CONVERSATION_BUSY, _BUSY, 409, headers={"Retry-After": "2"}) from exc

    def _event(self, name: str, ctx: TenantContext, data: dict) -> None:
        self.events.publish(DomainEvent(name=name, tenant_id=ctx.tenant_id, hotel_id=ctx.hotel_id, conversation_id=ctx.conversation_id, channel=ctx.channel, data=data))
