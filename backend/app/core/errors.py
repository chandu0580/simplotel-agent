"""Stable, machine-readable error codes shared by every API version."""

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_BOOKING_DETAILS = "INVALID_BOOKING_DETAILS"
    HOTEL_NOT_FOUND = "HOTEL_NOT_FOUND"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    AVAILABILITY_UNAVAILABLE = "AVAILABILITY_UNAVAILABLE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
    FORBIDDEN = "FORBIDDEN"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


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
