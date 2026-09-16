from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AvailabilityQuery(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1)
    children: int = Field(default=0, ge=0)


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class BookingRequest(BaseModel):
    room_id: str
    check_in: date
    check_out: date
    adults: int = Field(ge=1)
    children: int = Field(default=0, ge=0)
    # Opaque reference to the authenticated guest; never raw contact details.
    guest_reference: str


class Booking(BaseModel):
    booking_id: str
    tenant_id: str
    hotel_id: str
    status: BookingStatus
    room_id: str
    check_in: date
    check_out: date
    adults: int
    children: int
    total_price: int
    currency: str
    created_at: datetime


class ReservationErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"  # provider down / timeout / circuit open
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IN_PROGRESS = "IN_PROGRESS"  # same idempotency key is being processed by another request


class ReservationError(Exception):
    def __init__(self, code: ReservationErrorCode, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
