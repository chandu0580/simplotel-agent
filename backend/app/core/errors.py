"""Stable, machine-readable error codes shared by every API version."""

from enum import StrEnum


class ErrorCode(StrEnum):
    # Request problems
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_BOOKING_DETAILS = "INVALID_BOOKING_DETAILS"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    NOT_FOUND = "NOT_FOUND"
    HOTEL_NOT_FOUND = "HOTEL_NOT_FOUND"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    # Access
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    CONVERSATION_BUSY = "CONVERSATION_BUSY"
    # Dependencies (HTTP errors when a request can't be served; otherwise reported as meta.degradation)
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    RESERVATION_UNAVAILABLE = "RESERVATION_UNAVAILABLE"
    KNOWLEDGE_UNAVAILABLE = "KNOWLEDGE_UNAVAILABLE"
    STATE_UNAVAILABLE = "STATE_UNAVAILABLE"
    # Business
    FEATURE_DISABLED = "FEATURE_DISABLED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Why a guest turn was answered in degraded mode (fallback_reason → public code).
DEGRADATION_CODES: dict[str, ErrorCode] = {
    "provider_timeout": ErrorCode.LLM_TIMEOUT,
    "provider_status": ErrorCode.LLM_UNAVAILABLE,
    "provider_connection": ErrorCode.LLM_UNAVAILABLE,
    "provider_sdk": ErrorCode.LLM_UNAVAILABLE,
    "provider_protocol": ErrorCode.LLM_UNAVAILABLE,
    "refusal": ErrorCode.LLM_UNAVAILABLE,
    "truncated": ErrorCode.LLM_UNAVAILABLE,
    "invalid_output": ErrorCode.LLM_UNAVAILABLE,
    "invalid_tool_call": ErrorCode.LLM_UNAVAILABLE,
    "tool_timeout": ErrorCode.TOOL_TIMEOUT,
    "tool_unavailable": ErrorCode.TOOL_UNAVAILABLE,
    "reservation_unavailable": ErrorCode.RESERVATION_UNAVAILABLE,
}


# The pre-v1 API used lowercase codes; keep them stable for existing clients.
LEGACY_CODES = {
    ErrorCode.VALIDATION_ERROR: "validation_error",
    ErrorCode.INVALID_BOOKING_DETAILS: "invalid_booking_details",
    ErrorCode.INTERNAL_ERROR: "internal_error",
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status: int,
        details: list[dict] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.headers = headers or {}
