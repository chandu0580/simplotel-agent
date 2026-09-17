"""Channel-independent turn types shared by the AI and offline assistants."""

from dataclasses import dataclass, field
from datetime import date

from ..knowledge.models import KnowledgeBase
from ..schemas import BookingContext, ChatHistoryItem, ChatReply
from ..tenancy import TenantContext


@dataclass
class TurnRequest:
    tenant: TenantContext
    message: str
    history: list[ChatHistoryItem] = field(default_factory=list)
    booking_context: BookingContext | None = None
    recent_offers: list[str] = field(default_factory=list)
    locale: str | None = None


@dataclass
class Turn:
    """A TurnRequest resolved against the hotel's data for today."""

    request: TurnRequest
    kb: KnowledgeBase
    today: date
    tenant_flags: dict[str, bool]
    message: str  # after input sanitisation

    @property
    def tenant(self) -> TenantContext:
        return self.request.tenant


class LLMError(Exception):
    """The model could not produce a usable reply; the offline engine should answer instead."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class DependencyUnavailable(Exception):
    """A required integration (e.g. live availability) is down; carries the safe reply to show."""

    def __init__(self, reply: ChatReply, reason: str):
        super().__init__(reason)
        self.reply = reply
        self.reason = reason
