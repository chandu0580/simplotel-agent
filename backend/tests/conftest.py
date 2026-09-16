from datetime import date
import json
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.assistant.turn import TurnRequest
from app.container import Container, build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.tracing import AITrace
from app.llm.anthropic_provider import AnthropicProvider
from app.main import create_app
from app.reservations.availability import load_inventory
from app.schemas import BookingContext
from app.tenancy import Channel

# A Monday outside any pricing season, so weekday rules and prices are predictable.
TODAY = date(2026, 10, 5)
GOA = "hotel-goa-001"
BLR = "hotel-blr-001"


def text_response(payload: dict | str, stop_reason: str = "end_turn") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
    )


def answer_response(payload: dict) -> SimpleNamespace:
    """The normal way the model answers: an `answer_guest` tool call."""
    return tool_response("answer_guest", payload)


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


def make_container(messages_client: Any | None = None, **settings_overrides) -> Container:
    provider = AnthropicProvider(messages_client) if messages_client is not None else None
    if provider is not None:  # the fake client speaks the Anthropic protocol
        settings_overrides = {"llm_provider": "anthropic", "model_primary": "claude-opus-5", "model_fast": "claude-opus-5", **settings_overrides}
    return build_container(Settings.for_tests(**settings_overrides), clock=FixedClock(TODAY), llm_provider=provider)


def turn_request(container: Container, message: str, hotel_id: str = GOA, booking_context: BookingContext | None = None, **kwargs) -> TurnRequest:
    ctx = container.tenants.resolve(hotel_id, Channel.WEB, "test-request", "0" * 32)
    return TurnRequest(tenant=ctx, message=message, booking_context=booking_context, **kwargs)


def ai_reply(container: Container, message: str, **kwargs):
    """Call the AI assistant directly (no offline fallback) so model-path errors surface."""
    turn = container.assistant.resolve_turn(turn_request(container, message, **kwargs))
    trace = AITrace(trace_id="t", request_id="r", tenant_id=turn.tenant.tenant_id, hotel_id=turn.tenant.hotel_id, channel="web")
    return container.assistant.ai.reply(turn, trace)


@pytest.fixture
def fake_messages():
    return FakeMessages()


@pytest.fixture
def ai_container(fake_messages):
    return make_container(fake_messages)


@pytest.fixture
def offline_container():
    return make_container()


@pytest.fixture
def kb(offline_container):
    return offline_container.knowledge.snapshot(GOA, TODAY)


@pytest.fixture
def inventory(offline_container):
    return load_inventory(offline_container.settings.data_dir / "hotels" / GOA / "inventory.json")


def _client(container: Container):
    with TestClient(create_app(container=container)) as client:
        yield client


@pytest.fixture
def ai_client(ai_container):
    yield from _client(ai_container)


@pytest.fixture
def offline_client(offline_container):
    yield from _client(offline_container)
