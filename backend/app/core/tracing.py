"""AI execution traces.

One `AITrace` per guest turn records what the assistant did and with which versions, so a
production answer can be explained later ("which prompt, model, evidence and tools produced
this?"). Sinks are pluggable: logs today; OpenTelemetry, LangSmith or Sentry can be added as
another `TraceSink` without touching the assistant.
"""

from collections import deque
from dataclasses import asdict, dataclass, field
import logging
from typing import Protocol

from .observability import log_event

logger = logging.getLogger("hotel_assistant.trace")


@dataclass
class ToolCallRecord:
    name: str
    status: str  # ok | error
    latency_ms: int
    error_code: str | None = None
    invoked_by: str = "model"
    # Validated arguments, recorded only for read-only tools (dates/guest counts); never for mutating tools.
    arguments: dict | None = None


@dataclass
class AITrace:
    trace_id: str
    request_id: str
    tenant_id: str
    hotel_id: str
    channel: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    knowledge_version: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    cited_ids: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    input_flags: list[str] = field(default_factory=list)
    pii_masked: list[str] = field(default_factory=list)  # kinds masked in the guest message (card, email, phone)
    stop_reason: str | None = None
    llm_latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    mode: str | None = None  # ai | offline | guardrail | conversational
    intent: str | None = None  # conversational intents only (greeting, capability, thanks, goodbye)
    fallback_used: bool = False
    fallback_reason: str | None = None
    reply_type: str | None = None
    success: bool = False
    error_type: str | None = None
    total_latency_ms: int | None = None
    # Latency breakdown (ms, sub-millisecond precision). app = total - llm - tools: prompt build,
    # guardrails, validation, knowledge snapshot and retrieval, i.e. everything this service controls.
    knowledge_latency_ms: float | None = None
    retrieval_latency_ms: float | None = None
    tool_latency_ms: float | None = None
    app_latency_ms: float | None = None


class TraceSink(Protocol):
    def record(self, trace: AITrace) -> None: ...


class LoggingTraceSink:
    def record(self, trace: AITrace) -> None:
        log_event(logger, "ai_trace", **asdict(trace))


class InMemoryTraceSink:
    def __init__(self, maxlen: int = 1000):
        self.traces: deque[AITrace] = deque(maxlen=maxlen)

    def record(self, trace: AITrace) -> None:
        self.traces.append(trace)

    @property
    def last(self) -> AITrace | None:
        return self.traces[-1] if self.traces else None


class CompositeTraceSink:
    def __init__(self, *sinks: TraceSink):
        self.sinks = sinks

    def record(self, trace: AITrace) -> None:
        for sink in self.sinks:
            try:
                sink.record(trace)
            except Exception:  # a broken exporter must never break a guest reply
                logger.exception("trace_sink_failed sink=%s", type(sink).__name__)
