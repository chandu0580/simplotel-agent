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


def _glm_provider(status=200, body=None):
    import httpx

    from app.llm.glm_provider import GLMProvider

    body = body or {
        "id": "chatcmpl-1", "model": "glm-5.2",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "check_availability", "arguments": "{\"adults\": 2}"}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 0}},
    }
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GLMProvider("https://gateway.example/v1", "test-key", timeout_seconds=5, max_retries=0, client=client, sleep=lambda _s: None), sent


@pytest.fixture(params=["anthropic", "glm", "scripted"])
def llm(request):
    return {"anthropic": _anthropic_provider, "glm": _glm_provider, "scripted": _scripted_provider}[request.param]()


def test_llm_provider_contract_tool_calls(llm):
    provider, _ = llm
    response = provider.generate_with_tools(REQUEST)
    assert response.stop_reason == "tool_use"
    assert response.tool_calls == [ToolCall("toolu_1", "check_availability", {"adults": 2})]
    assert response.usage.input_tokens == 5 and response.model and response.provider == provider.name


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
    from app.core.config import Settings

    glm = ModelRouter.from_settings(Settings.for_tests(llm_provider="glm"))
    anthropic_router = ModelRouter.from_settings(Settings.for_tests(llm_provider="anthropic", model_primary="claude-opus-5", model_fast="claude-opus-5"))
    assert glm.route(ModelTask.GUEST_TURN).model == "glm-5.2" and glm.route(ModelTask.GUEST_TURN).effort is None  # effort is Anthropic-only
    assert anthropic_router.route(ModelTask.GUEST_TURN).model == "claude-opus-5" and anthropic_router.route(ModelTask.GUEST_TURN).effort == "low"
    assert set(glm.describe()) == {t.value for t in ModelTask}
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


# ---------- GLM adapter specifics ----------


def test_glm_request_forces_a_single_tool_call_and_omits_anthropic_parameters():
    from dataclasses import replace

    provider, sent = _glm_provider()
    provider.generate_with_tools(replace(REQUEST, require_tool=True))
    body = sent[0]
    assert body["tool_choice"] == "required" and body["parallel_tool_calls"] is False
    assert body["messages"][0] == {"role": "system", "content": "You are a test."}
    assert body["tools"][0]["function"]["name"] == "check_availability"
    assert "effort" not in json.dumps(body) and "fallbacks" not in body and "output_config" not in body


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [(500, {"error": "boom"}, "status"), (401, {"error": "bad key"}, "status"), (200, {"no": "choices"}, "protocol")],
)
def test_glm_errors_map_to_typed_provider_errors(status, body, kind):
    provider, _ = _glm_provider(status=status, body=body)
    with pytest.raises(LLMProviderError) as exc:
        provider.generate_with_tools(REQUEST)
    assert exc.value.kind == kind


def test_glm_retries_transient_errors_but_not_client_errors():
    import httpx

    from app.llm.glm_provider import GLMProvider

    ok = {"model": "glm-5.2", "choices": [{"finish_reason": "stop", "message": {"content": "hi"}}], "usage": {}}
    for status, expected_calls in ((503, 3), (400, 1)):
        calls = []

        def handler(request, status=status, calls=calls):
            calls.append(1)
            return httpx.Response(status if len(calls) < 3 else 200, json=ok if len(calls) >= 3 else {"error": "x"})

        provider = GLMProvider("https://g.example/v1", "k", timeout_seconds=5, max_retries=2, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None)
        try:
            provider.generate(REQUEST)
        except LLMProviderError:
            pass
        assert len(calls) == expected_calls


def test_glm_malformed_tool_arguments_are_rejected_by_the_assistant():
    """Malformed JSON arguments reach the assistant as a raw string and are treated as invalid output → offline fallback."""
    import httpx

    from app.container import build_container
    from app.core.clock import FixedClock
    from app.core.config import Settings
    from app.llm.glm_provider import GLMProvider
    from tests.conftest import TODAY, turn_request

    body = {"model": "glm-5.2", "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "answer_guest", "arguments": "{not json"}}]}}]}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    provider = GLMProvider("https://g.example/v1", "k", timeout_seconds=5, max_retries=0, client=client)
    container = build_container(Settings.for_tests(llm_provider="glm"), clock=FixedClock(TODAY), llm_provider=provider)

    outcome = container.assistant.handle(turn_request(container, "What time is check-in?"))

    assert outcome.mode == "offline" and outcome.trace.fallback_reason == "invalid_output"
    assert outcome.reply.sources[0].id == "timings.check_in_out"


def test_glm_timeout_abandons_the_request_within_the_budget():
    """Against a real slow HTTP server: the client read timeout closes the connection, so no work is left hanging."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time

    from app.llm.glm_provider import GLMProvider

    finished = []

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(2)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            except OSError:
                pass
            finished.append(1)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = GLMProvider(f"http://127.0.0.1:{server.server_address[1]}/v1", "k", timeout_seconds=0.3, max_retries=0)
        started = time.perf_counter()
        with pytest.raises(LLMProviderError) as exc:
            provider.generate(REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        server.shutdown()
    assert exc.value.kind == "timeout"
    assert elapsed < 1.5


def test_production_requires_https_llm_endpoints():
    from app.core.config import ConfigError, Settings

    base = {"app_env": "production", "cors_origins": ("https://hotel.example",), "log_format": "json"}
    with pytest.raises(ConfigError, match="LLM_BASE_URL must use https"):
        Settings(**base, llm_base_url="http://10.0.0.1:4000/v1").validate()
    with pytest.raises(ConfigError, match="mock"):
        Settings(**base, llm_provider="mock").validate()
    Settings(**base, llm_base_url="https://llm.internal.example/v1").validate()
    Settings(app_env="development", llm_base_url="http://127.0.0.1:4000/v1").validate()  # allowed outside production


def test_default_provider_is_glm_and_reads_llm_variables():
    from app.core.config import Settings

    settings = Settings.from_env({"LLM_API_KEY": "k-123456789", "LLM_BASE_URL": "https://g.example/v1", "LLM_MODEL": "glm-5.2"})
    assert settings.llm_provider == "glm" and settings.llm_configured and settings.model_primary == "glm-5.2"
    assert "k-123456789" in settings.secret_values() and "k-123456789" not in repr(settings)


# ---------- Provider-neutral failure contract ----------

PLAIN_TEXT = "Check-in is at 2 PM."  # a text reply instead of the required tool call


def _failing(provider_name: str, failure: str):
    import httpx

    from app.llm.glm_provider import GLMProvider

    if provider_name == "scripted":
        item = {
            "timeout": LLMProviderError("timeout", "timed out"),
            "unavailable": LLMProviderError("status", "503", status_code=503),
            "malformed": LLMResponse("end_turn", PLAIN_TEXT, [], "scripted", "scripted", TokenUsage(5, 3, 0)),
        }[failure]
        return ScriptedLLMProvider([item])

    if provider_name == "anthropic":
        def handler(request):
            if failure == "timeout":
                raise httpx2.ReadTimeout("slow", request=request)
            if failure == "unavailable":
                return httpx2.Response(503, json={"type": "error", "error": {"type": "api_error", "message": "down"}})
            return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "stop_sequence": None,
                                              "content": [{"type": "text", "text": PLAIN_TEXT}], "usage": {"input_tokens": 5, "output_tokens": 3}})

        client = anthropic.Anthropic(api_key="test-key-placeholder", max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        return AnthropicProvider(client.beta.messages)

    def glm_handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "unavailable":
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={"model": "glm-5.2", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": PLAIN_TEXT}}]})

    return GLMProvider("https://g.example/v1", "k", timeout_seconds=5, max_retries=0, client=httpx.Client(transport=httpx.MockTransport(glm_handler)))


@pytest.mark.parametrize("provider_name", ["anthropic", "glm", "scripted"])
@pytest.mark.parametrize(
    ("failure", "reason", "public_code"),
    [("timeout", "provider_timeout", "LLM_TIMEOUT"), ("unavailable", "provider_status", "LLM_UNAVAILABLE"), ("malformed", "invalid_output", "LLM_UNAVAILABLE")],
)
def test_every_provider_fails_the_same_way(provider_name, failure, reason, public_code):
    """Timeouts, outages and malformed output degrade identically whatever the provider: grounded offline answer + stable code."""
    from app.container import build_container
    from app.core.clock import FixedClock
    from app.core.config import Settings
    from app.core.errors import DEGRADATION_CODES
    from tests.conftest import turn_request

    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY), llm_provider=_failing(provider_name, failure))
    outcome = container.assistant.handle(turn_request(container, "What time is check-in?"))

    assert outcome.mode == "offline" and outcome.trace.fallback_used
    assert outcome.trace.fallback_reason == reason and DEGRADATION_CODES[reason] == public_code
    assert outcome.reply.sources and outcome.reply.sources[0].id == "timings.check_in_out"
    if failure == "malformed":
        assert outcome.reply.text != PLAIN_TEXT  # unvalidated model text is never shown
