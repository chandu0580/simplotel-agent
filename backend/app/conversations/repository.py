"""Conversation persistence boundary.

Every read and delete is keyed by (tenant_id, hotel_id, conversation_id): a conversation id from
Hotel A is simply not found when requested through Hotel B. `InMemoryConversationRepository`
suits a single process. For multiple replicas use Redis (TTL-native) or a relational table
indexed on (tenant_id, hotel_id, id).
"""

from collections import OrderedDict
from datetime import datetime
import threading
from typing import Protocol

from ..core.clock import Clock
from .models import Conversation


class ConversationRepository(Protocol):
    def get(self, tenant_id: str, hotel_id: str, conversation_id: str) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> None: ...
    def delete(self, tenant_id: str, hotel_id: str, conversation_id: str) -> bool: ...
    def purge_expired(self) -> int: ...


class InMemoryConversationRepository:
    def __init__(self, clock: Clock, max_conversations: int = 50_000):
        self._items: OrderedDict[tuple[str, str, str], Conversation] = OrderedDict()
        self._lock = threading.Lock()
        self._clock = clock
        self._max = max_conversations

    def _expired(self, conversation: Conversation, now: datetime) -> bool:
        return conversation.expires_at <= now

    def get(self, tenant_id: str, hotel_id: str, conversation_id: str) -> Conversation | None:
        key = (tenant_id, hotel_id, conversation_id)
        with self._lock:
            conversation = self._items.get(key)
            if conversation is None:
                return None
            if self._expired(conversation, self._clock.now()):
                del self._items[key]
                return None
            return conversation.model_copy(deep=True)

    def save(self, conversation: Conversation) -> None:
        key = (conversation.tenant_id, conversation.hotel_id, conversation.id)
        with self._lock:
            self._items[key] = conversation.model_copy(deep=True)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)  # evict least recently written

    def delete(self, tenant_id: str, hotel_id: str, conversation_id: str) -> bool:
        with self._lock:
            return self._items.pop((tenant_id, hotel_id, conversation_id), None) is not None

    def purge_expired(self) -> int:
        now = self._clock.now()
        with self._lock:
            expired = [k for k, c in self._items.items() if self._expired(c, now)]
            for key in expired:
                del self._items[key]
            return len(expired)
