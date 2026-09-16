"""Contract tests: every implementation of a boundary must behave the same way.

When a real PMS provider, database knowledge provider or another LLM adapter is added, add it to
the parametrised fixtures here and it must pass the same assertions.
"""

from datetime import date
import json
from pathlib import Path

import anthropic
import httpx2
import pytest

from app.core.cache import TTLCache
from app.core.errors import AppError
from app.core.resilience import CircuitBreaker
from app.knowledge.provider import JsonKnowledgeProvider
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.provider import LLMMessage, LLMProviderError, LLMRequest, LLMResponse, LLMToolSpec, TokenUsage, ToolCall
from app.llm.router import ModelRoute, ModelRouter, ModelTask
from app.llm.scripted_provider import ScriptedLLMProvider
from app.main import create_app
from app.reservations.availability import AvailabilityValidationError
from app.reservations.idempotency import InMemoryIdempotencyStore
from app.reservations.models import AvailabilityQuery, ReservationError, ReservationErrorCode
from app.reservations.provider import MockReservationProvider, ResilientReservationProvider
from app.schemas import AvailabilityResult
from tests.conftest import GOA, TODAY, make_container

TOOL = LLMToolSpec("check_availability", "Check rooms", {"type": "object", "properties": {"adults": {"type": "integer"}}, "required": ["adults"], "additionalProperties": False})
REQUEST = LLMRequest(system="You are a test.", messages=[LLMMessage("user", "Rooms for 2?")], model="claude-opus-5", max_tokens=100, effort="low", tools=[TOOL])


# ---------- LLM providers ----------


def _anthropic_provider(status=200, body=None):
    body = body or {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use", "stop_sequence": None,
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "check_availability", "input": {"adults": 2}}],
        "usage": {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(status, json=body)

    client = anthropic.Anthropic(api_key="sk-ant-test-not-a-real-key", max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return AnthropicProvider(client.beta.messages), sent


def _scripted_provider():
    response = LLMResponse("tool_use", None, [ToolCall("toolu_1", "check_availability", {"adults": 2})], "claude-opus-5", "scripted", TokenUsage(5, 3, 0))
    provider = ScriptedLLMProvider([response, response])
    return provider, provider.requests


@pytest.fixture(params=["anthropic", "scripted"])
def llm(request):
    return _anthropic_provider() if request.param == "anthropic" else _scripted_provider()


def test_llm_provider_contract_tool_calls(llm):
    provider, _ = llm
    response = provider.generate_with_tools(REQUEST)
    assert response.stop_reason == "tool_use"
    assert response.tool_calls == [ToolCall("toolu_1", "check_availability", {"adults": 2})]
    assert response.usage.input_tokens == 5 and response.model == "claude-opus-5"


def test_llm_provider_contract_generate_sends_no_tools(llm):
    provider, sent = llm
    provider.generate(REQUEST)
    last = sent[-1]
    tools = last.get("tools") if isinstance(last, dict) else last.tools
    assert not tools


def test_anthropic_errors_map_to_provider_errors():
    provider, _ = _anthropic_provider(status=529, body={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    with pytest.raises(LLMProviderError) as exc:
        provider.generate_with_tools(REQUEST)
    assert exc.value.kind == "status" and exc.value.status_code == 529


def test_model_router_routes_every_task():
    router = ModelRouter.from_settings(make_container().settings)
    assert router.route(ModelTask.GUEST_TURN).model == "claude-opus-5"
    assert set(router.describe()) == {t.value for t in ModelTask}
    with pytest.raises(ValueError):
        ModelRouter({ModelTask.GUEST_TURN: ModelRoute("m", None, 1)})


# ---------- Reservation providers ----------


@pytest.fixture(params=["mock", "resilient"])
def reservations(request, offline_container):
    mock = MockReservationProvider(offline_container.settings.data_dir, InMemoryIdempotencyStore())
    if request.param == "mock":
        return mock
    return ResilientReservationProvider(mock, CircuitBreaker("c", 5, 30), TTLCache(), timeout_seconds=5, read_retries=1, availability_ttl_seconds=10)


def test_reservation_provider_contract(reservations, offline_container):
    ctx, kb = offline_container.tenants.resolve(GOA), offline_container.knowledge.snapshot(GOA, TODAY)

    result = reservations.check_availability(ctx, kb, AvailabilityQuery(check_in=date(2026, 10, 7), check_out=date(2026, 10, 8), adults=2), TODAY)
    assert isinstance(result, AvailabilityResult) and result.available
    assert reservations.get_room(ctx, kb, "ocean-villa").name == "Ocean Villa"
    with pytest.raises(AvailabilityValidationError):
        reservations.check_availability(ctx, kb, AvailabilityQuery(check_in=date(2026, 10, 8), check_out=date(2026, 10, 7), adults=2), TODAY)
    for call in (lambda: reservations.modify_booking(ctx, "BK-1", {}, "idem-key-0001"), lambda: reservations.cancel_booking(ctx, "BK-1", "idem-key-0001")):
        with pytest.raises(ReservationError) as exc:
            call()
        assert exc.value.code == ReservationErrorCode.NOT_SUPPORTED
    assert reservations.is_healthy()


# ---------- Knowledge provider ----------


def test_knowledge_provider_contract(offline_container):
    provider = JsonKnowledgeProvider(offline_container.settings.data_dir, TTLCache(), 60)
    snapshot = provider.snapshot(GOA, TODAY)
    assert snapshot.hotel.id == GOA and snapshot.entry("policies.cancellation")
    assert len(provider.list_entries(GOA, include_unpublished=True)) > len(provider.list_entries(GOA))
    assert provider.profile(GOA).timezone == "Asia/Kolkata"
    assert provider.is_healthy([GOA]) and not provider.is_healthy(["hotel-nope"])
    with pytest.raises(AppError):
        provider.snapshot("hotel-nope", TODAY)


# ---------- Frontend/backend API contract ----------

OPENAPI_SNAPSHOT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def test_openapi_matches_committed_snapshot():
    """The frontend and external integrators rely on docs/openapi.json. Regenerate it deliberately:
    python -m scripts.export_openapi"""
    app = create_app(container=make_container())
    current = json.loads(json.dumps(app.openapi()))
    assert OPENAPI_SNAPSHOT.exists(), "docs/openapi.json missing; run: python -m scripts.export_openapi"
    assert current == json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8")), "API contract changed; review and regenerate docs/openapi.json"


def test_fields_the_frontend_depends_on_are_in_the_contract():
    schemas = create_app(container=make_container()).openapi()["components"]["schemas"]
    assert {"conversation_id", "mode", "reply", "notice", "meta"} <= set(schemas["ConversationTurnResponse"]["properties"])
    assert {"type", "text", "sources", "suggestions", "availability", "booking_prefill", "form_error"} <= set(schemas["ChatReply"]["properties"])
    assert {"room_id", "name", "max_occupancy", "breakfast_included", "rooms_left", "nightly_rate", "total_price", "currency"} <= set(schemas["RoomOffer"]["properties"])


def test_token_usage_includes_cache_reads_and_writes():
    provider, _ = _anthropic_provider(
        body={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 2800, "cache_creation_input_tokens": 120},
        }
    )
    usage = provider.generate(REQUEST).usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (50, 10, 2800, 120)
