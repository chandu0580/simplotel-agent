"""Public guest API (v1). Hotel-scoped; unauthenticated by design; rate limited."""

from fastapi import APIRouter, Request, Response

from ..core.clock import local_today
from ..core.errors import DEGRADATION_CODES
from ..reservations.models import AvailabilityQuery
from ..schemas import (
    AvailabilityRequest,
    AvailabilityResult,
    ConversationCreated,
    ConversationTurnResponse,
    ConversationView,
    CreateConversationRequest,
    Degradation,
    MessageView,
    PostMessageRequest,
    ResponseMeta,
    V1ErrorResponse,
)
from .deps import container_of, enforce_rate_limits, resolve_guest_context

# Errors every guest endpoint can return; all use the V1ErrorResponse envelope.
_COMMON_ERRORS = {code: {"model": V1ErrorResponse} for code in (404, 413, 422, 429, 500, 503)}
_CONVERSATION_ERRORS = {409: {"model": V1ErrorResponse, "description": "CONVERSATION_BUSY: another turn on this conversation is in progress (Retry-After)"}}
router = APIRouter(prefix="/api/v1/hotels/{hotel_id}", tags=["guest v1"], responses=_COMMON_ERRORS)

DEGRADATION_MESSAGES = {
    "LLM_TIMEOUT": "The AI model did not respond in time; the answer came from the hotel FAQ.",
    "LLM_UNAVAILABLE": "The AI model was unavailable or returned an unusable reply; the answer came from the hotel FAQ.",
    "TOOL_TIMEOUT": "A booking system call timed out.",
    "TOOL_UNAVAILABLE": "A booking system call failed.",
    "RESERVATION_UNAVAILABLE": "Live availability is temporarily unavailable.",
}


def degradation_for(reason: str | None) -> Degradation | None:
    code = DEGRADATION_CODES.get(reason or "")
    return Degradation(code=code.value, message=DEGRADATION_MESSAGES[code.value]) if code else None


SUGGESTED_QUESTIONS = [
    "What time is check-in?",
    "Does the hotel have a swimming pool?",
    "Which room is suitable for three guests?",
    "Is breakfast included?",
    "What is the cancellation policy?",
    "Do you have rooms available this weekend?",
]


@router.get("")
def hotel_profile(hotel_id: str, request: Request):
    """Public, non-sensitive hotel profile for the guest UI (branding, languages, form limits)."""
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx)
    c = container_of(request)
    profile = c.knowledge.profile(hotel_id)
    today = local_today(c.clock, profile.timezone)
    kb = c.knowledge.snapshot(hotel_id, today)
    tenant = c.tenants.tenant(ctx.tenant_id)
    return {
        "hotel": profile.model_dump(),
        "today": today.isoformat(),
        "max_guests": max(r.max_occupancy for r in kb.rooms),
        # Published room content for the landing page. `base_rate` is the indicative "from" rate, not a
        # quote: real prices for real dates come from check_availability, which is seasonal.
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "beds": r.beds,
                "size_sqm": r.size_sqm,
                "max_occupancy": r.max_occupancy,
                "breakfast_included": r.breakfast_included,
                "base_rate": r.base_rate,
                "features": r.features,
            }
            for r in kb.rooms
        ],
        "suggested_questions": SUGGESTED_QUESTIONS,
        "features": {"ai_assistant": c.assistant.ai_available_for(tenant.feature_flags)},
    }


@router.post("/conversations", status_code=201, response_model=ConversationCreated)
def create_conversation(hotel_id: str, body: CreateConversationRequest, request: Request):
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx)
    conversation = container_of(request).conversations.start(ctx, body.locale)
    return ConversationCreated(conversation_id=conversation.id, hotel_id=hotel_id, channel=conversation.channel, locale=conversation.locale, expires_at=conversation.expires_at)


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
def get_conversation(hotel_id: str, conversation_id: str, request: Request):
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx, conversation_id)
    c = container_of(request).conversations.get(ctx, conversation_id)
    return ConversationView(
        conversation_id=c.id,
        hotel_id=c.hotel_id,
        channel=c.channel,
        locale=c.locale,
        created_at=c.created_at,
        updated_at=c.updated_at,
        expires_at=c.expires_at,
        active_intent=c.active_intent,
        availability_context=c.availability_context,
        messages=[MessageView(**m.model_dump()) for m in c.messages],
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(hotel_id: str, conversation_id: str, request: Request):
    """Guest-initiated deletion (data minimisation)."""
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx, conversation_id)
    container_of(request).conversations.delete(ctx, conversation_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages", response_model=ConversationTurnResponse, responses=_CONVERSATION_ERRORS)
def post_message(hotel_id: str, conversation_id: str, body: PostMessageRequest, request: Request):
    # Sync endpoint: FastAPI runs it in the worker thread pool, so the LLM call and the rate-limit/state
    # round trips (Redis in multi-replica mode) never block the event loop.
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx, conversation_id)
    conversation, outcome = container_of(request).conversations.post_message(ctx, conversation_id, body.message, body.locale)
    t = outcome.trace
    return ConversationTurnResponse(
        request_id=ctx.request_id,
        conversation_id=conversation.id,
        mode=outcome.mode,
        reply=outcome.reply,
        notice=outcome.notice,
        meta=ResponseMeta(
            trace_id=t.trace_id,
            prompt_version=t.prompt_version,
            tool_schema_version=t.tool_schema_version,
            knowledge_version=t.knowledge_version,
            degradation=degradation_for(t.fallback_reason),
        ),
    )


@router.post("/conversations/{conversation_id}/availability", response_model=AvailabilityResult, responses=_CONVERSATION_ERRORS)
def conversation_availability(hotel_id: str, conversation_id: str, body: AvailabilityRequest, request: Request):
    """Booking-form submission: deterministic search, recorded in the conversation's context."""
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx, conversation_id)
    return container_of(request).conversations.check_availability(ctx, conversation_id, AvailabilityQuery(**body.model_dump()))


@router.post("/availability", response_model=AvailabilityResult)
def availability(hotel_id: str, body: AvailabilityRequest, request: Request):
    """Stateless availability search (e.g. for a booking-engine widget)."""
    ctx = resolve_guest_context(request, hotel_id)
    enforce_rate_limits(request, ctx)
    return container_of(request).conversations.check_availability(ctx, None, AvailabilityQuery(**body.model_dump()))

