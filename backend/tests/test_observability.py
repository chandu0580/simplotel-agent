"""Observability evidence: request context on every log line, AI trace completeness, latency breakdown, metrics that move."""

import io
import json
import logging

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.observability import ContextFilter, JsonFormatter, RedactingFilter, Redactor
from app.llm.mock_provider import LatencyMockProvider
from app.main import create_app
from tests.conftest import GOA, TODAY, make_container
from tests.test_tools_and_resilience import FlakyProvider

BASE = f"/api/v1/hotels/{GOA}"


def _capture(*logger_names):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter(Redactor(["glm-secret-key-value-123"])))
    loggers = [logging.getLogger(n) for n in logger_names]
    previous = [(lg.level, lg.propagate) for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
    return stream, handler, loggers, previous


def _release(handler, loggers, previous):
    for lg, (level, _propagate) in zip(loggers, previous, strict=True):
        lg.removeHandler(handler)
        lg.setLevel(level)


def _records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def samples(container) -> dict[tuple[str, tuple], float]:
    out = {}
    for family in text_string_to_metric_families(container.metrics.render().decode()):
        for s in family.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def value(container, name, **labels) -> float:
    return samples(container).get((name, tuple(sorted(labels.items()))), 0.0)


def test_access_log_and_trace_carry_full_request_context():
    container = build_container(Settings.for_tests(llm_api_key="glm-secret-key-value-123"), clock=FixedClock(TODAY), llm_provider=LatencyMockProvider(latency_ms=5))  # scan-secrets: allow (fake key)
    stream, handler, loggers, previous = _capture("hotel_assistant.api", "hotel_assistant.trace")
    try:
        with TestClient(create_app(container=container)) as client:
            cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
            response = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in? key glm-secret-key-value-123"}, headers={"X-Request-ID": "req-obs-1"})
    finally:
        _release(handler, loggers, previous)
    records = _records(stream)
    access = next(r for r in records if r.get("message") == "http_request" and r.get("request_id") == "req-obs-1")
    assert access["tenant_id"] == "tenant-demo" and access["hotel_id"] == GOA and access["conversation_id"] == cid and access["trace_id"]
    assert access["route"] == "/api/v1/hotels/{hotel_id}/conversations/{conversation_id}/messages" and access["status"] == 200

    trace = next(r for r in records if r.get("message") == "ai_trace")
    assert trace["request_id"] == "req-obs-1" and trace["trace_id"] == access["trace_id"] == response.json()["meta"]["trace_id"]
    assert trace["conversation_id"] == cid and trace["tenant_id"] == "tenant-demo"
    for field in ("provider", "model", "prompt_version", "tool_schema_version", "knowledge_version", "mode", "reply_type", "llm_latency_ms", "total_latency_ms"):
        assert trace[field] not in (None, ""), field
    assert trace["provider"] == "mock" and trace["fallback_used"] is False and trace["success"] is True
    assert "glm-secret-key-value-123" not in stream.getvalue()


def test_latency_breakdown_separates_llm_tool_retrieval_and_app_time():
    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY), llm_provider=LatencyMockProvider(latency_ms=40))
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"})
    trace = container.recent_traces.traces[-1]
    assert trace.llm_latency_ms >= 40
    assert trace.retrieval_latency_ms is not None and trace.knowledge_latency_ms is not None and trace.tool_latency_ms == 0
    assert 0 <= trace.app_latency_ms < trace.total_latency_ms
    assert abs(trace.total_latency_ms - (trace.llm_latency_ms + trace.tool_latency_ms + trace.app_latency_ms)) <= 1
    assert value(container, "turn_latency_ms_count", mode="ai") == 1 and value(container, "app_latency_ms_count", mode="ai") == 1
    assert value(container, "retrieval_latency_ms_count") == 1 and value(container, "llm_latency_ms_count", provider="mock") == 1


def test_counters_move_for_each_documented_signal():
    container = make_container(rate_limit_enabled=True, rate_limit_ip_per_minute=100, rate_limit_ip_burst=100, rate_limit_conversation_per_minute=3)
    container.reservations.inner = FlakyProvider(container.reservations.inner, failures=1000)
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Is there a pool?"})  # offline turn (AI disabled)
        client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "Is anything available from 2026-10-07 to 2026-10-09 for 2 adults?"})  # tool fails: outage
        client.post(f"{BASE}/availability", json={"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2})  # 503
        for _ in range(3):
            client.get(f"{BASE}/conversations/{cid}")  # 3rd+ over the conversation limit
    assert value(container, "assistant_requests_total", channel="web") == 2
    assert value(container, "assistant_fallback_total", reason="ai_disabled") >= 1
    assert value(container, "assistant_fallback_total", reason="reservation_unavailable") == 1
    assert value(container, "tool_calls_total", tool="check_availability", status="error") == 1
    assert value(container, "tool_failures_total", tool="check_availability", error_code="DEPENDENCY_UNAVAILABLE") == 1
    assert value(container, "rate_limited_total", dimension="conversation") >= 1
    assert value(container, "request_latency_ms_count", route="/api/v1/hotels/{hotel_id}/availability", status_class="5xx") == 1


def test_successful_availability_and_errors_are_counted():
    container = make_container()
    with TestClient(create_app(container=container), raise_server_exceptions=False) as client:
        client.post(f"{BASE}/availability", json={"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2})
        container.assistant.knowledge = None  # force an unexpected failure inside the turn
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        failed = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "hello"})
    assert value(container, "availability_search_total", source="form", available="true") == 1
    assert failed.status_code == 500
    assert value(container, "assistant_failures_total", error_type="AttributeError") == 1
