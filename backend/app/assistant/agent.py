"""AI assistant: one model call per guest turn.

The model replies by calling exactly one tool:
  * `answer_guest`: an answer from the knowledge base, with cited source ids;
  * `check_availability`: dates and guests are known;
  * `request_booking_details`: availability was asked for but details are missing.

The final answer is a strict tool rather than a JSON output format combined with tools: that keeps
one decision point per turn and doesn't depend on a provider supporting structured output and tool
calls in the same request (a real model tested during development stopped calling tools when both
were enabled). Tool results go straight to the reply; availability numbers never pass back through
the model, so it can't misquote prices or inventory.
"""

import json
import logging
import time

from pydantic import BaseModel, Field, ValidationError

from ..core.metrics import Metrics
from ..core.tracing import AITrace
from ..core.versioning import content_hash
from ..knowledge.retrieval import Retriever
from ..llm.provider import LLMMessage, LLMProvider, LLMProviderError, LLMRequest, LLMResponse, LLMToolSpec
from ..llm.router import ModelRouter, ModelTask
from ..schemas import AvailabilityResult, ChatReply
from ..tools.base import ToolContext, ToolErrorCode, ToolRegistry
from ..tools.builtin import NeedsDetails
from .guardrails import OutputGuardrails, neutralise_prompt_tags
from .prompts import ANSWER_TOOL, LANGUAGE_NAMES, PROMPT_VERSION, render_system_prompt, tool_schema_version
from .turn import DependencyUnavailable, LLMError, Turn

logger = logging.getLogger("hotel_assistant.agent")

AVAILABLE_SUGGESTIONS = ["What is the cancellation policy?", "Is breakfast included?"]
UNAVAILABLE_SUGGESTIONS = ["Try different dates", "How can I contact the hotel?"]


class ModelReply(BaseModel):
    type: str = Field(pattern="^(answer|clarification|fallback)$")
    text: str = Field(min_length=1)
    source_ids: list[str]
    suggestions: list[str]


def build_messages(turn: Turn) -> list[LLMMessage]:
    request = turn.request
    # Replayed history is guest-controlled too: neutralise prompt tags exactly like the current message.
    history = [LLMMessage(m.role, neutralise_prompt_tags(m.content)) for m in request.history]
    # The API requires the first message to be from the user; a UI welcome message is not.
    while history and history[0].role != "user":
        history.pop(0)

    ctx = request.booking_context
    lines = [f"Today's date at the hotel: {turn.today.isoformat()} ({turn.today:%A})."]
    if ctx and any(v is not None for v in ctx.model_dump().values()):
        lines.append(
            "Booking details the guest last entered: "
            f"check_in={ctx.check_in or 'unknown'}, check_out={ctx.check_out or 'unknown'}, "
            f"adults={ctx.adults if ctx.adults is not None else 'unknown'}, "
            f"children={ctx.children if ctx.children is not None else 'unknown'}."
        )
    if request.locale and request.locale != "en":
        lines.append(f"Reply language: {LANGUAGE_NAMES.get(request.locale, request.locale)} (locale {request.locale}).")
    content = "<context>\n" + "\n".join(lines) + "\n</context>\n\n<guest_message>\n" + turn.message + "\n</guest_message>"
    return history + [LLMMessage("user", content)]


class AIAssistant:
    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        router: ModelRouter,
        retriever: Retriever,
        guardrails: OutputGuardrails,
        metrics: Metrics,
    ):
        self.provider = provider
        self.tools = tools
        self.router = router
        self.retriever = retriever
        self.guardrails = guardrails
        self.metrics = metrics
        self._system_prompts: dict[tuple[str, str, str], str] = {}

    def reply(self, turn: Turn, trace: AITrace) -> ChatReply:
        kb = turn.kb
        retrieval_started = time.perf_counter()
        evidence = self.retriever.retrieve(kb, turn.message)
        trace.retrieval_latency_ms = round((time.perf_counter() - retrieval_started) * 1000, 3)
        trace.evidence_ids = evidence.ids

        key = (kb.hotel.id, kb.knowledge_version, content_hash(evidence.ids))
        if key not in self._system_prompts:  # byte-identical per knowledge version, so prompt caching can hit
            self._system_prompts[key] = render_system_prompt(kb, evidence)
        model_tools = self.tools.model_tools(turn.tenant_flags)
        specs = [ANSWER_TOOL] + [LLMToolSpec(d.name, d.description, d.input_schema) for d in model_tools]
        route = self.router.route(ModelTask.GUEST_TURN)
        trace.provider, trace.model = self.provider.name, route.model
        trace.prompt_version, trace.tool_schema_version = PROMPT_VERSION, tool_schema_version(specs)

        try:
            response = self.provider.generate_with_tools(
                LLMRequest(
                    system=self._system_prompts[key],
                    messages=build_messages(turn),
                    model=route.model,
                    max_tokens=route.max_tokens,
                    effort=route.effort,
                    tools=specs,
                    require_tool=True,
                )
            )
        except LLMProviderError as exc:
            raise LLMError(f"provider_{exc.kind}", str(exc)) from exc
        self._record_response(trace, response)

        if response.stop_reason == "refusal":
            raise LLMError("refusal", "Model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("truncated", "Model output was truncated")

        calls = response.tool_calls
        # If a model ignores the single-tool instruction, an action beats a text answer.
        call = next((c for c in calls if c.name != ANSWER_TOOL.name), calls[0] if calls else None)
        if call is not None and call.name == ANSWER_TOOL.name:
            return self._answer(turn, trace, call.arguments)
        if call is not None:
            return self._action(turn, trace, call.name, call.arguments)

        if response.text is None:
            raise LLMError("invalid_output", "Model returned neither a tool call nor text")
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise LLMError("invalid_output", "Model replied with plain text instead of a tool call") from exc
        return self._answer(turn, trace, payload)

    def _record_response(self, trace: AITrace, response: LLMResponse) -> None:
        trace.model = response.model
        trace.stop_reason = response.stop_reason
        trace.llm_latency_ms = response.latency_ms
        trace.input_tokens = response.usage.input_tokens
        trace.output_tokens = response.usage.output_tokens
        trace.cache_read_tokens = response.usage.cache_read_tokens
        trace.cache_write_tokens = response.usage.cache_write_tokens
        self.metrics.llm_latency_ms.labels(response.provider).observe(response.latency_ms)
        usage = response.usage
        for kind, value in (("input", usage.input_tokens), ("output", usage.output_tokens), ("cache_read", usage.cache_read_tokens), ("cache_write", usage.cache_write_tokens)):
            if value:
                self.metrics.llm_tokens_total.labels(kind, response.model).inc(value)

    def _answer(self, turn: Turn, trace: AITrace, payload: object) -> ChatReply:
        try:
            parsed = ModelReply.model_validate(payload)
        except ValidationError as exc:
            raise LLMError("invalid_output", f"Model sent an invalid answer: {exc.error_count()} errors") from exc
        price_check = self.tools.flags.is_enabled("guardrail_price_check_enabled", turn.tenant_flags)
        outcome = self.guardrails.check_answer(turn.kb, parsed.type, parsed.text, parsed.source_ids, parsed.suggestions, price_check)
        trace.guardrails += outcome.triggered
        trace.cited_ids = [s.id for s in outcome.reply.sources]
        return outcome.reply

    def _action(self, turn: Turn, trace: AITrace, name: str, arguments: object) -> ChatReply:
        ctx = ToolContext(tenant=turn.tenant, kb=turn.kb, today=turn.today, booking_context=turn.request.booking_context, tenant_flags=turn.tenant_flags)
        result = self.tools.execute(name, arguments, ctx, invoked_by="model")
        trace.tool_calls.append(result.record("model"))

        if not result.ok:
            if reason := dependency_reason(result.error_code):
                raise DependencyUnavailable(availability_unavailable_reply(turn), reason)
            # Unknown, unexposed, unauthorized or malformed tool calls are model errors.
            raise LLMError("invalid_tool_call", f"Model tool call {name!r} rejected: {result.error_code}")

        if isinstance(result.data, NeedsDetails):
            message, leaks = self.guardrails.check_model_text(turn.kb, result.data.message)
            trace.guardrails += leaks
            return ChatReply(
                type="collect_booking_details",
                text=message or "Please choose your dates and number of guests.",
                booking_prefill=result.data.prefill,
                form_error=result.data.form_error,
            )
        if isinstance(result.data, AvailabilityResult):
            suggestions = AVAILABLE_SUGGESTIONS if result.data.available else UNAVAILABLE_SUGGESTIONS
            return ChatReply(type="availability", text=result.data.message, availability=result.data, suggestions=suggestions)
        raise LLMError("invalid_tool_call", f"Tool {name!r} returned an unexpected result")


def dependency_reason(code: ToolErrorCode | None) -> str | None:
    """Tool failures that are the dependency's fault (not the model's) → degradation reason."""
    return {
        ToolErrorCode.DEPENDENCY_UNAVAILABLE: "reservation_unavailable",
        ToolErrorCode.TIMEOUT: "tool_timeout",
        ToolErrorCode.EXECUTION_FAILED: "tool_unavailable",
    }.get(code)


def availability_unavailable_reply(turn: Turn) -> ChatReply:
    return ChatReply(
        type="fallback",
        text=f"I can't check live availability right now. Please try again in a few minutes, or contact us: {turn.kb.contact_line()}.",
        suggestions=["Try again", "How can I contact the hotel?"],
    )
