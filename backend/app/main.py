from contextlib import asynccontextmanager
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .availability import AvailabilityValidationError, check_availability, hotel_today
from .claude_assistant import build_claude_assistant
from .config import get_settings
from .knowledge import get_knowledge_base
from .offline import OfflineAssistant
from .schemas import AvailabilityRequest, AvailabilityResult, ChatRequest, ChatResponse, ErrorResponse
from .service import ChatService

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("hotel_assistant.api")


def create_chat_service() -> ChatService:
    kb = get_knowledge_base()
    ai = None
    if settings.ai_enabled and settings.anthropic_api_key:
        ai = build_claude_assistant(
            kb,
            api_key=settings.anthropic_api_key,
            model=settings.model,
            effort=settings.effort,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            refusal_fallback=settings.refusal_fallback,
        )
    return ChatService(offline=OfflineAssistant(kb), ai=ai)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "chat_service"):
        app.state.chat_service = create_chat_service()
    mode = "ai" if app.state.chat_service.ai else "offline"
    logger.info(
        "startup mode=%s model=%s effort=%s refusal_fallback=%s", mode, settings.model, settings.effort, settings.refusal_fallback
    )
    yield


app = FastAPI(title="Hotel Guest Assistant API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_error request_id=%s path=%s", request_id, request.url.path)
        response = _error(request_id, 500, "internal_error", "Something went wrong on our side. Please try again.")
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request request_id=%s method=%s path=%s status=%s latency_ms=%d",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


def _error(request_id: str, status: int, code: str, message: str, details: list[dict] | None = None) -> JSONResponse:
    body = ErrorResponse(request_id=request_id, error={"code": code, "message": message, "details": details})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]} for err in exc.errors()]
    return _error(request.state.request_id, 422, "validation_error", "Some of the request fields are invalid.", details)


@app.exception_handler(AvailabilityValidationError)
async def availability_error_handler(request: Request, exc: AvailabilityValidationError):
    return _error(request.state.request_id, 422, "invalid_booking_details", str(exc))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return _error(request.state.request_id, exc.status_code, "http_error", str(exc.detail))


@app.get("/api/health")
def health(request: Request):
    return {"status": "ok", "mode": "ai" if request.app.state.chat_service.ai else "offline"}


@app.get("/api/hotel")
def hotel_info():
    """Public, non-sensitive hotel profile the UI uses for its header and form limits."""
    kb = get_knowledge_base()
    return {
        "hotel": kb.hotel.model_dump(),
        "today": hotel_today().isoformat(),
        "max_guests": max(r.max_occupancy for r in kb.rooms),
        "suggested_questions": [
            "What time is check-in?",
            "Does the hotel have a swimming pool?",
            "Which room is suitable for three guests?",
            "Is breakfast included?",
            "What is the cancellation policy?",
            "Do you have rooms available this weekend?",
        ],
    }


@app.post("/api/chat", response_model=ChatResponse, responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
async def chat(body: ChatRequest, request: Request):
    service: ChatService = request.app.state.chat_service
    # The Anthropic client is synchronous; keep it off the event loop.
    return await run_in_threadpool(service.handle, body, request.state.request_id, hotel_today())


@app.post("/api/availability", response_model=AvailabilityResult, responses={422: {"model": ErrorResponse}})
def availability(body: AvailabilityRequest, request: Request):
    """Deterministic endpoint used by the booking form; no LLM involved."""
    result = check_availability(body.check_in, body.check_out, body.adults, body.children)
    logger.info(
        "availability_search request_id=%s check_in=%s check_out=%s adults=%s children=%s available=%s",
        request.state.request_id,
        body.check_in,
        body.check_out,
        body.adults,
        body.children,
        result.available,
    )
    return result
