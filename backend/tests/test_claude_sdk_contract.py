"""Runs ClaudeAssistant through the real Anthropic SDK with a mocked HTTP transport.

The SimpleNamespace fakes in conftest check our logic; these tests check what the fakes
can't: the exact request the SDK puts on the wire (headers, beta flags, tool schemas,
output format) and that real SDK response objects parse correctly. They do not prove the
live API accepts the request — only a run with a real key (evals --mode ai) does that.
"""

import json

import anthropic
import httpx2
import pytest

from app.claude_assistant import FALLBACK_BETA, ClaudeAssistant, LLMError
from app.schemas import BookingContext, ChatRequest
from tests.conftest import TODAY


def _message(content: list[dict], stop_reason: str) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 3000, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }


def _assistant(kb, handler, refusal_fallback="default"):
    captured: list[httpx2.Request] = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        status, body = handler(request)
        return httpx2.Response(status, json=body)

    client = anthropic.Anthropic(
        api_key="sk-ant-test-not-a-real-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(transport)),
    )
    return ClaudeAssistant(kb, client.beta.messages, model="claude-opus-5", effort="low", refusal_fallback=refusal_fallback), captured


def _answer_block(answer: dict) -> dict:
    return {"type": "tool_use", "id": "toolu_answer", "name": "answer_guest", "input": answer}


def test_request_on_the_wire_matches_the_messages_api_contract(kb):
    answer = {"type": "answer", "text": "Check-in is from 2:00 PM.", "source_ids": ["timings.check_in_out"], "suggestions": []}
    assistant, captured = _assistant(kb, lambda _r: (200, _message([_answer_block(answer)], "tool_use")))

    reply = assistant.reply(ChatRequest(message="What time is check-in?"), TODAY)

    assert reply.type == "answer" and reply.sources[0].id == "timings.check_in_out"
    request = captured[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test-not-a-real-key"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"}
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in body["tools"]] == ["answer_guest", "check_availability", "request_booking_details"]
    assert all(t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in body["tools"])
    assert body["messages"][-1]["role"] == "user"
    assert "thinking" not in body  # Opus 5 runs adaptive thinking by default


def test_refusal_fallback_can_be_disabled(kb):
    answer = {"type": "clarification", "text": "Hello!", "source_ids": [], "suggestions": []}
    assistant, captured = _assistant(kb, lambda _r: (200, _message([_answer_block(answer)], "tool_use")), refusal_fallback="none")

    assistant.reply(ChatRequest(message="hi"), TODAY)

    body = json.loads(captured[0].content)
    assert "fallbacks" not in body
    assert FALLBACK_BETA not in captured[0].headers.get("anthropic-beta", "")


def test_real_sdk_tool_use_response_runs_availability(kb):
    tool_use = {
        "type": "tool_use",
        "id": "toolu_01",
        "name": "check_availability",
        "input": {"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 3, "children": 0},
    }
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    assistant, _ = _assistant(kb, lambda _r: (200, _message([thinking, tool_use], "tool_use")))

    reply = assistant.reply(ChatRequest(message="Rooms for 3 adults 7-9 Oct?"), TODAY)

    assert reply.type == "availability"
    assert {r.room_id for r in reply.availability.rooms} == {"deluxe-pool-view", "family-suite"}


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
def test_http_errors_from_the_api_become_llm_errors(kb, status):
    error = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    assistant, _ = _assistant(kb, lambda _r: (status, error))

    with pytest.raises(LLMError):
        assistant.reply(ChatRequest(message="hi"), TODAY)


def test_refusal_stop_reason_becomes_llm_error(kb):
    assistant, _ = _assistant(kb, lambda _r: (200, _message([], "refusal")))

    with pytest.raises(LLMError, match="declined"):
        assistant.reply(ChatRequest(message="hi"), TODAY)


def test_booking_context_is_sent_to_the_model(kb):
    answer = {"type": "clarification", "text": "Sure.", "source_ids": [], "suggestions": []}
    assistant, captured = _assistant(kb, lambda _r: (200, _message([_answer_block(answer)], "tool_use")))

    assistant.reply(
        ChatRequest(message="Same dates, but for 3 adults.", booking_context=BookingContext(check_in="2026-10-07", check_out="2026-10-09", adults=2)),
        TODAY,
    )

    last = json.loads(captured[0].content)["messages"][-1]["content"]
    assert "check_in=2026-10-07, check_out=2026-10-09, adults=2" in last
