"""Conversation state.

Data minimisation: a conversation holds only what the guest journey needs, which is message text,
reply types and the last booking details (dates and guest counts). No names, contact details or
payment data are stored, and conversations expire (default 24 h).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import BookingContext


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    reply_type: str | None = None
    created_at: datetime


class Conversation(BaseModel):
    id: str
    tenant_id: str
    hotel_id: str
    channel: str
    locale: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int = 0  # incremented on every save; used for compare-and-set across replicas
    messages: list[StoredMessage] = Field(default_factory=list)
    active_intent: Literal["information", "availability"] | None = None
    availability_context: BookingContext | None = None
