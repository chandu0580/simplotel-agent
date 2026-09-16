"""API contract shared by the chat and availability endpoints."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- Availability ----------


class AvailabilityRequest(StrictModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)
    children: int = Field(default=0, ge=0, le=6)


class RoomOffer(BaseModel):
    room_id: str
    name: str
    description: str
    beds: str
    size_sqm: int
    max_occupancy: int
    breakfast_included: bool
    rooms_left: int
    nightly_rate: int
    total_price: int
    currency: str
    features: list[str]


class AvailabilityResult(BaseModel):
    check_in: date
    check_out: date
    nights: int
    adults: int
    children: int
    available: bool
    rooms: list[RoomOffer]
    # Room types that fit the party but are sold out, so the UI can say so explicitly.
    sold_out_room_names: list[str] = []
    message: str
    season_label: str | None = None


# ---------- Chat ----------


class ChatHistoryItem(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class BookingContext(StrictModel):
    """Last booking details the guest entered, so follow-ups like 'what about 3 adults?' work."""

    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = Field(default=None, ge=1, le=10)
    children: int | None = Field(default=None, ge=0, le=6)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[ChatHistoryItem] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    booking_context: BookingContext | None = None

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


ReplyType = Literal["answer", "clarification", "fallback", "availability", "collect_booking_details"]


class BookingPrefill(BaseModel):
    check_in: date | None = None
    check_out: date | None = None
    adults: int | None = None
    children: int | None = None


class Source(BaseModel):
    id: str
    title: str


class ChatReply(BaseModel):
    type: ReplyType
    text: str
    sources: list[Source] = []
    suggestions: list[str] = []
    availability: AvailabilityResult | None = None
    booking_prefill: BookingPrefill | None = None
    form_error: str | None = None


class ChatResponse(BaseModel):
    request_id: str
    mode: Literal["ai", "offline"]
    reply: ChatReply
    notice: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorBody


# ---------- API v1 ----------

LOCALE_PATTERN = r"^[a-z]{2}(-[A-Z]{2})?$"


class CreateConversationRequest(StrictModel):
    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN)


class ConversationCreated(BaseModel):
    conversation_id: str
    hotel_id: str
    channel: str
    locale: str | None
    expires_at: datetime


class PostMessageRequest(StrictModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class Degradation(BaseModel):
    code: str  # LLM_TIMEOUT | LLM_UNAVAILABLE | TOOL_TIMEOUT | TOOL_UNAVAILABLE | RESERVATION_UNAVAILABLE
    message: str


class ResponseMeta(BaseModel):
    trace_id: str
    degradation: Degradation | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    knowledge_version: str | None = None


class ConversationTurnResponse(BaseModel):
    request_id: str
    conversation_id: str
    mode: Literal["ai", "offline"]
    reply: ChatReply
    notice: str | None = None
    meta: ResponseMeta


class MessageView(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    reply_type: str | None = None
    created_at: datetime


class ConversationView(BaseModel):
    conversation_id: str
    hotel_id: str
    channel: str
    locale: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    active_intent: str | None
    availability_context: BookingContext | None
    messages: list[MessageView]


class V1ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict] | None = None


class V1ErrorResponse(BaseModel):
    error: V1ErrorBody
