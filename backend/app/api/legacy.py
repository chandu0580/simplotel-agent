"""Pre-v1 endpoints for the default hotel, kept for backward compatibility.

They behave as before (stateless chat with client-sent history) and add `Deprecation` and
`Link: successor-version` headers pointing at /api/v1.
"""

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from ..assistant.turn import TurnRequest
from ..core.clock import local_today
from ..reservations.models import AvailabilityQuery
from ..schemas import AvailabilityRequest, AvailabilityResult, ChatRequest, ChatResponse, ErrorResponse
from .deps import container_of, enforce_rate_limits, resolve_guest_context
from .v1_guest import SUGGESTED_QUESTIONS

router = APIRouter(prefix="/api", tags=["legacy (deprecated)"], responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})


def _deprecate(response: Response, successor: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{successor}>; rel="successor-version"'


@router.get("/health", deprecated=True)
def health(request: Request, response: Response):
    c = container_of(request)
    _deprecate(response, "/health")
    tenant = c.tenants.tenant_for_hotel(c.tenants.default_hotel_id)
    return {"status": "ok", "mode": "ai" if c.assistant.ai_available_for(tenant.feature_flags) else "offline"}


@router.get("/hotel", deprecated=True)
def hotel_info(request: Request, response: Response):
    c = container_of(request)
    hotel_id = c.tenants.default_hotel_id
    _deprecate(response, f"/api/v1/hotels/{hotel_id}")
    profile = c.knowledge.profile(hotel_id)
    today = local_today(c.clock, profile.timezone)
    kb = c.knowledge.snapshot(hotel_id, today)
    return {"hotel": profile.model_dump(), "today": today.isoformat(), "max_guests": max(r.max_occupancy for r in kb.rooms), "suggested_questions": SUGGESTED_QUESTIONS}


@router.post("/chat", response_model=ChatResponse, deprecated=True)
async def chat(body: ChatRequest, request: Request, response: Response):
    c = container_of(request)
    ctx = resolve_guest_context(request, c.tenants.default_hotel_id)
    enforce_rate_limits(request, ctx)
    _deprecate(response, f"/api/v1/hotels/{ctx.hotel_id}/conversations")
    outcome = await run_in_threadpool(
        c.assistant.handle, TurnRequest(tenant=ctx, message=body.message, history=body.history, booking_context=body.booking_context)
    )
    return ChatResponse(request_id=ctx.request_id, mode=outcome.mode, reply=outcome.reply, notice=outcome.notice)


@router.post("/availability", response_model=AvailabilityResult, deprecated=True)
async def availability(body: AvailabilityRequest, request: Request, response: Response):
    c = container_of(request)
    ctx = resolve_guest_context(request, c.tenants.default_hotel_id)
    enforce_rate_limits(request, ctx)
    _deprecate(response, f"/api/v1/hotels/{ctx.hotel_id}/availability")
    return await run_in_threadpool(c.conversations.check_availability, ctx, None, AvailabilityQuery(**body.model_dump()))
