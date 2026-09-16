"""Assistant core: resolves a guest turn for any channel and applies graceful degradation.

    TurnRequest → tenant flags + hotel-local date + knowledge snapshot
      → input guardrails → AI assistant (or offline engine on failure / when disabled)
      → trace + metrics + events → TurnOutcome
"""

from dataclasses import dataclass
import logging
import time
import uuid

from ..core.clock import Clock, local_today
from ..core.events import DomainEvent, EventPublisher
from ..core.flags import FeatureFlags
from ..core.metrics import Metrics
from ..core.observability import log_event
from ..core.privacy import Minimised, minimise
from ..core.tracing import AITrace, TraceSink
from ..knowledge.provider import KnowledgeProvider
from ..schemas import ChatReply
from ..tenancy import TenantRegistry
from .agent import AIAssistant
from .guardrails import InputGuardrails
from .offline import OfflineAssistant
from .turn import DependencyUnavailable, LLMError, Turn, TurnRequest

logger = logging.getLogger("hotel_assistant.service")

AI_UNAVAILABLE_NOTICE = "Our AI assistant is temporarily unavailable, so answers are coming from our standard hotel FAQ."
AI_DISABLED_NOTICE = "AI answers are turned off; answers are coming from our standard hotel FAQ."


@dataclass
class TurnOutcome:
    reply: ChatReply
    mode: str  # ai | offline
    notice: str | None
    trace: AITrace


class AssistantService:
    def __init__(
        self,
        tenants: TenantRegistry,
        knowledge: KnowledgeProvider,
        ai: AIAssistant | None,
        offline: OfflineAssistant,
        input_guardrails: InputGuardrails,
        flags: FeatureFlags,
        metrics: Metrics,
        events: EventPublisher,
        traces: TraceSink,
        clock: Clock,
        mask_contact_details: bool = True,
    ):
        self.mask_contact_details = mask_contact_details
        self.tenants = tenants
        self.knowledge = knowledge
        self.ai = ai
        self.offline = offline
        self.input_guardrails = input_guardrails
        self.flags = flags
        self.metrics = metrics
        self.events = events
        self.traces = traces
        self.clock = clock

    def resolve_turn(self, request: TurnRequest) -> Turn:
        tenant = self.tenants.tenant(request.tenant.tenant_id)
        profile = self.knowledge.profile(request.tenant.hotel_id)
        today = local_today(self.clock, profile.timezone)
        kb = self.knowledge.snapshot(request.tenant.hotel_id, today)
        return Turn(request=request, kb=kb, today=today, tenant_flags=tenant.feature_flags, message=request.message)

    def ai_available_for(self, tenant_flags: dict[str, bool]) -> bool:
        return self.ai is not None and self.flags.is_enabled("ai_assistant_enabled", tenant_flags)

    def minimise(self, text: str) -> Minimised:
        return minimise(text, mask_contact_details=self.mask_contact_details)

    def handle(self, request: TurnRequest) -> TurnOutcome:
        started = time.perf_counter()
        # Personal data never reaches the model, traces or storage (see app.core.privacy). Idempotent, so
        # text already minimised by ConversationService passes through unchanged.
        masked = self.minimise(request.message)
        request.message = masked.text
        request.history = [item.model_copy(update={"content": self.minimise(item.content).text}) for item in request.history]
        ctx = request.tenant
        trace = AITrace(
            trace_id=ctx.trace_id if ctx.trace_id != "-" else uuid.uuid4().hex,
            request_id=ctx.request_id,
            tenant_id=ctx.tenant_id,
            hotel_id=ctx.hotel_id,
            channel=ctx.channel,
            conversation_id=ctx.conversation_id,
        )
        self.metrics.assistant_requests_total.labels(ctx.channel).inc()
        try:
            knowledge_started = time.perf_counter()
            turn = self.resolve_turn(request)
            trace.knowledge_latency_ms = round((time.perf_counter() - knowledge_started) * 1000, 3)
            trace.knowledge_version = turn.kb.knowledge_version
            trace.pii_masked = sorted(set(masked.masked))
            for kind in trace.pii_masked:
                self.metrics.pii_masked_total.labels(kind).inc()
            self._event("GuestQuestionAsked", ctx, {"message_length": len(request.message), "locale": request.locale})
            outcome = self._respond(turn, trace)
        except Exception as exc:
            trace.success, trace.error_type = False, type(exc).__name__
            trace.total_latency_ms = int((time.perf_counter() - started) * 1000)
            self.metrics.assistant_failures_total.labels(type(exc).__name__).inc()
            self.traces.record(trace)
            raise

        trace.reply_type, trace.success = outcome.reply.type, True
        total_ms = (time.perf_counter() - started) * 1000
        trace.total_latency_ms = int(total_ms)
        trace.tool_latency_ms = float(sum(c.latency_ms for c in trace.tool_calls))
        trace.app_latency_ms = round(max(total_ms - (trace.llm_latency_ms or 0) - trace.tool_latency_ms, 0.0), 3)
        self._observe(ctx, outcome, trace)
        self.traces.record(trace)
        return outcome

    def _respond(self, turn: Turn, trace: AITrace) -> TurnOutcome:
        verdict = self.input_guardrails.check(turn.request.message)
        trace.input_flags = verdict.flags
        turn.message = verdict.text
        ai_available = self.ai_available_for(turn.tenant_flags)
        display_mode = "ai" if ai_available else "offline"
        if verdict.blocked and verdict.reply is not None:
            trace.mode = "guardrail"
            trace.guardrails.append("input_blocked")
            return TurnOutcome(verdict.reply, display_mode, None, trace)

        try:
            if not ai_available:
                trace.mode, trace.fallback_used, trace.fallback_reason = "offline", True, "ai_disabled"
                return TurnOutcome(self.offline.reply(turn, trace), "offline", AI_DISABLED_NOTICE, trace)
            try:
                trace.mode = "ai"
                return TurnOutcome(self.ai.reply(turn, trace), "ai", None, trace)
            except LLMError as exc:
                log_event(logger, "llm_failure", level=logging.ERROR, reason=exc.reason, error=str(exc))
                trace.mode, trace.fallback_used, trace.fallback_reason, trace.error_type = "offline", True, exc.reason, exc.reason
                return TurnOutcome(self.offline.reply(turn, trace), "offline", AI_UNAVAILABLE_NOTICE, trace)
        except DependencyUnavailable as exc:
            trace.fallback_used, trace.fallback_reason, trace.error_type = True, exc.reason, exc.reason
            return TurnOutcome(exc.reply, display_mode, None, trace)

    def _observe(self, ctx, outcome: TurnOutcome, trace: AITrace) -> None:
        self.metrics.assistant_success_total.labels(outcome.mode).inc()
        mode = trace.mode or outcome.mode
        self.metrics.turn_latency_ms.labels(mode).observe(trace.total_latency_ms or 0)
        self.metrics.app_latency_ms.labels(mode).observe(trace.app_latency_ms or 0)
        if trace.retrieval_latency_ms is not None:
            self.metrics.retrieval_latency_ms.observe(trace.retrieval_latency_ms)
        self.metrics.assistant_replies_total.labels(outcome.reply.type, outcome.mode).inc()
        if outcome.reply.type == "fallback":
            self.metrics.unsupported_question_total.inc()
        if trace.fallback_used:
            self.metrics.assistant_fallback_total.labels(trace.fallback_reason or "unknown").inc()
            self._event("FallbackTriggered", ctx, {"reason": trace.fallback_reason})
        for name in trace.guardrails:
            self.metrics.guardrail_interventions_total.labels(name).inc()
        for flag in trace.input_flags:
            self.metrics.prompt_injection_signals_total.labels(flag).inc()
        if trace.guardrails or trace.input_flags:
            self._event("GuardrailTriggered", ctx, {"guardrails": trace.guardrails, "input_flags": trace.input_flags})
        self._event(
            "AssistantResponseGenerated",
            ctx,
            {
                "reply_type": outcome.reply.type,
                "mode": outcome.mode,
                "sources": [s.id for s in outcome.reply.sources],
                "prompt_version": trace.prompt_version,
                "knowledge_version": trace.knowledge_version,
                "latency_ms": trace.total_latency_ms,
            },
        )

    def _event(self, name: str, ctx, data: dict) -> None:
        self.events.publish(DomainEvent(name=name, tenant_id=ctx.tenant_id, hotel_id=ctx.hotel_id, conversation_id=ctx.conversation_id, channel=ctx.channel, data=data))
