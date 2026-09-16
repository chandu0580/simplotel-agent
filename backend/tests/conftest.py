from datetime import date
from types import SimpleNamespace
from typing import Any
import json

import pytest
from fastapi.testclient import TestClient

from app import availability as availability_module
from app import main as main_module
from app.claude_assistant import ClaudeAssistant
from app.knowledge import get_knowledge_base
from app.offline import OfflineAssistant
from app.service import ChatService

# A Monday outside any pricing season, so weekday rules and prices are predictable.
TODAY = date(2026, 10, 5)


def text_response(payload: dict | str, stop_reason: str = "end_turn") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
    )


def tool_response(name: str, tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="toolu_1", name=name, input=tool_input)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
    )


class FakeMessages:
    """Stands in for `client.beta.messages`; records calls and replays scripted responses."""

    def __init__(self, responses: list[Any] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(availability_module, "hotel_today", lambda: TODAY)
    monkeypatch.setattr(main_module, "hotel_today", lambda: TODAY)


@pytest.fixture
def kb():
    return get_knowledge_base()


@pytest.fixture
def fake_messages():
    return FakeMessages()


def _client(service: ChatService):
    main_module.app.state.chat_service = service
    with TestClient(main_module.app) as client:
        yield client
    del main_module.app.state.chat_service


@pytest.fixture
def ai_client(kb, fake_messages):
    ai = ClaudeAssistant(kb, fake_messages, model="claude-opus-5", effort="low")
    yield from _client(ChatService(offline=OfflineAssistant(kb), ai=ai))


@pytest.fixture
def offline_client(kb):
    yield from _client(ChatService(offline=OfflineAssistant(kb), ai=None))
