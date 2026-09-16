"""Operational endpoints. /metrics is meant for the internal network only (blocked at the edge)."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST

from ..core.errors import AppError, ErrorCode
from .deps import container_of

router = APIRouter(tags=["operations"])


@router.get("/health")
async def health():  # async: served on the event loop, so a saturated worker thread pool can't fail liveness
    """Liveness: the process is up and serving requests. No dependency checks."""
    return {"status": "ok"}


@router.get("/ready", responses={200: {"description": "ready"}, 503: {"description": "not_ready: same body with status \"not_ready\" and the failing checks"}})
def ready(request: Request):
    """Readiness: can this instance usefully serve guests?

    Required: the knowledge store and the conversation state store (Redis when STATE_BACKEND=redis).
    The audit store is reported but not required: events are buffered and dropping them is logged. A reservation outage is reported as "degraded" but keeps the
    instance in service: every replica shares the same PMS, so failing readiness would remove all of them
    and stop FAQ answers that still work. The LLM is optional too (offline fallback exists).
    """
    c = container_of(request)
    checks = {
        "knowledge": "ok" if c.knowledge.is_healthy(c.tenants.hotel_ids()) else "failing",
        "state": "ok" if c.conversations.repository.is_healthy() else "failing",
        "reservations": "ok" if c.reservations.is_healthy() else "degraded",
        "llm": "configured" if c.llm_provider is not None else "not_configured",
        "audit_store": c.audit.status() if c.audit else "not_configured",
    }
    ok = checks["knowledge"] == "ok" and checks["state"] == "ok"
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    c = container_of(request)
    if not c.settings.metrics_enabled:
        raise AppError(ErrorCode.NOT_FOUND, "Not found.", 404)
    return Response(content=c.metrics.render(), media_type=CONTENT_TYPE_LATEST)
