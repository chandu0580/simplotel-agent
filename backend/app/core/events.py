"""Domain events.

Events describe what happened, carry tenant context and never include guest message text
or other personal data. Today they are logged; the `EventPublisher` interface is where an
outbox table or message broker would plug in once analytics or notifications need them.
"""

from collections import deque
from datetime import UTC, datetime
import logging
from typing import Any, Literal, Protocol
import uuid

from pydantic import BaseModel, Field

from .observability import log_event

logger = logging.getLogger("hotel_assistant.events")

EventName = Literal[
    "ConversationStarted",
    "ConversationDeleted",
    "GuestQuestionAsked",
    "AssistantResponseGenerated",
    "AvailabilityChecked",
    "FallbackTriggered",
    "GuardrailTriggered",
    "ToolFailed",
    "BookingRequested",
    "BookingConfirmed",
]


class DomainEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: EventName
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str
    hotel_id: str
    conversation_id: str | None = None
    channel: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EventPublisher(Protocol):
    def publish(self, event: DomainEvent) -> None: ...


class LoggingEventPublisher:
    def publish(self, event: DomainEvent) -> None:
        log_event(logger, "domain_event", **event.model_dump(mode="json"))


class InMemoryEventPublisher:
    def __init__(self, maxlen: int = 1000):
        self.events: deque[DomainEvent] = deque(maxlen=maxlen)

    def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name for e in self.events]


class CompositeEventPublisher:
    def __init__(self, *publishers: EventPublisher):
        self.publishers = publishers

    def publish(self, event: DomainEvent) -> None:
        for publisher in self.publishers:
            try:
                publisher.publish(event)
            except Exception:
                logger.exception("event_publish_failed publisher=%s", type(publisher).__name__)
