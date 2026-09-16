"""Prometheus metrics.

Each container gets its own registry, so tests are isolated. Labels are deliberately
low-cardinality: no tenant_id/hotel_id/conversation_id labels (thousands of hotels would
explode series counts). Per-tenant analytics come from structured events and logs instead.
"""

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

LATENCY_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000, 45000)
FINE_BUCKETS_MS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100, 250)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.assistant_requests_total = Counter("assistant_requests_total", "Guest turns received", ["channel"], registry=r)
        self.assistant_success_total = Counter("assistant_success_total", "Guest turns answered (AI or offline)", ["mode"], registry=r)
        self.assistant_failures_total = Counter("assistant_failures_total", "Guest turns that errored", ["error_type"], registry=r)
        self.assistant_fallback_total = Counter("assistant_fallback_total", "Turns served by the offline engine", ["reason"], registry=r)
        self.assistant_replies_total = Counter("assistant_replies_total", "Replies by type", ["reply_type", "mode"], registry=r)
        self.unsupported_question_total = Counter("unsupported_question_total", "Replies of type fallback", registry=r)
        self.availability_search_total = Counter("availability_search_total", "Availability searches", ["source", "available"], registry=r)
        self.tool_calls_total = Counter("tool_calls_total", "Tool executions", ["tool", "status"], registry=r)
        self.tool_failures_total = Counter("tool_failures_total", "Failed tool executions", ["tool", "error_code"], registry=r)
        self.guardrail_interventions_total = Counter("guardrail_interventions_total", "Guardrail triggers", ["guardrail"], registry=r)
        self.prompt_injection_signals_total = Counter("prompt_injection_signals_total", "Input guardrail flags (not necessarily blocked)", ["flag"], registry=r)
        self.pii_masked_total = Counter("pii_masked_total", "Guest messages with personal data masked, by kind", ["kind"], registry=r)
        self.rate_limited_total = Counter("rate_limited_total", "Requests rejected by rate limits", ["dimension"], registry=r)
        self.state_backend_errors_total = Counter("state_backend_errors_total", "Shared-state (Redis) errors handled by failing open", ["component"], registry=r)
        self.conversation_conflicts_total = Counter("conversation_conflicts_total", "Conversation saves rejected by the version check", registry=r)
        self.audit_events_total = Counter("audit_events_total", "Audit events by outcome (written, dropped, failed)", ["outcome"], registry=r)
        self.llm_tokens_total = Counter("llm_tokens_total", "LLM tokens by kind and model (price per model)", ["kind", "model"], registry=r)
        self.llm_latency_ms = Histogram("llm_latency_ms", "LLM call latency", ["provider"], buckets=LATENCY_BUCKETS_MS, registry=r)
        self.tool_latency_ms = Histogram("tool_latency_ms", "Tool latency", ["tool"], buckets=LATENCY_BUCKETS_MS, registry=r)
        self.turn_latency_ms = Histogram("turn_latency_ms", "Assistant turn latency (total)", ["mode"], buckets=LATENCY_BUCKETS_MS, registry=r)
        self.app_latency_ms = Histogram("app_latency_ms", "Turn latency excluding LLM and tool time", ["mode"], buckets=FINE_BUCKETS_MS, registry=r)
        self.retrieval_latency_ms = Histogram("retrieval_latency_ms", "Knowledge retrieval latency", buckets=FINE_BUCKETS_MS, registry=r)
        self.request_latency_ms = Histogram("request_latency_ms", "HTTP request latency", ["route", "status_class"], buckets=LATENCY_BUCKETS_MS, registry=r)

    def render(self) -> bytes:
        return generate_latest(self.registry)
