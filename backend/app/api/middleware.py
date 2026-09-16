"""HTTP middleware: request/trace ids, context binding, security headers, access logs, latency metrics."""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request

from ..core.errors import ErrorCode
from ..core.observability import bind_context, log_event, reset_context
from .errors import error_response

logger = logging.getLogger("hotel_assistant.api")
MAX_BODY_BYTES = 64 * 1024
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        container = request.app.state.container
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        traceparent = _TRACEPARENT.match(request.headers.get("traceparent", ""))
        trace_id = traceparent.group(1) if traceparent else uuid.uuid4().hex
        request.state.request_id, request.state.trace_id = request_id, trace_id
        reset_context()
        bind_context(request_id=request_id, trace_id=trace_id)

        started = time.perf_counter()
        declared = request.headers.get("content-length")
        try:
            too_large = declared is not None and int(declared) > MAX_BODY_BYTES
        except ValueError:
            too_large = True
        try:
            if too_large:
                response = error_response(request, 413, ErrorCode.PAYLOAD_TOO_LARGE, f"Request body exceeds {MAX_BODY_BYTES // 1024} KB.")
            else:
                response = await call_next(request)
        except Exception:
            logger.exception("unhandled_error path=%s", request.url.path)
            response = error_response(request, 500, ErrorCode.INTERNAL_ERROR, "Something went wrong on our side. Please try again.")

        latency_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if container.settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        container.metrics.request_latency_ms.labels(route_path, f"{response.status_code // 100}xx").observe(latency_ms)
        log_event(logger, "http_request", method=request.method, route=route_path, status=response.status_code, latency_ms=int(latency_ms))
        reset_context()
        return response
