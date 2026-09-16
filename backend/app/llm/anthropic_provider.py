"""Anthropic Messages API adapter: the only module that knows Anthropic SDK specifics."""

from dataclasses import replace
import time
from typing import Any, Protocol

import anthropic

from .provider import LLMProviderError, LLMRequest, LLMResponse, TokenUsage, ToolCall

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_STOP_REASONS = {"end_turn", "tool_use", "max_tokens", "refusal"}


class MessagesClient(Protocol):
    """The slice of `anthropic.Anthropic().beta.messages` we use; tests inject a fake."""

    def create(self, **kwargs: Any) -> Any: ...


def refusal_fallback_kwargs(mode: str) -> dict:
    """Server-side refusal fallback: on a policy decline the API re-runs the request once on a
    substitute model chosen by Anthropic. `none` disables it for endpoints that don't support it."""
    if mode == "none":
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, messages_client: MessagesClient, refusal_fallback: str = "default"):
        self.messages = messages_client
        self.refusal_fallback = refusal_fallback

    @classmethod
    def from_settings(cls, api_key: str, timeout: float, max_retries: int, refusal_fallback: str) -> "AnthropicProvider":
        # ANTHROPIC_BASE_URL is honoured by the SDK, so an Anthropic-compatible gateway works without code changes.
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        return cls(client.beta.messages, refusal_fallback)

    def _kwargs(self, request: LLMRequest) -> dict[str, Any]:
        system_block: dict[str, Any] = {"type": "text", "text": request.system}
        if request.cache_system_prompt:
            system_block["cache_control"] = {"type": "ephemeral"}
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [system_block],
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            **refusal_fallback_kwargs(self.refusal_fallback),
        }
        if request.effort:
            kwargs["output_config"] = {"effort": request.effort}
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "strict": True, "input_schema": t.input_schema} for t in request.tools
            ]
            # Forced tool_choice ("any") is rejected while thinking is on, so the prompt asks for exactly one tool.
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": request.single_tool_call}
        return kwargs

    def generate_with_tools(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = self.messages.create(**self._kwargs(request))
        except anthropic.APIStatusError as exc:
            raise LLMProviderError("status", f"Anthropic API error {exc.status_code}: {exc.message}", exc.status_code) from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMProviderError("connection", f"Anthropic API unreachable: {type(exc).__name__}") from exc
        except anthropic.AnthropicError as exc:  # e.g. unparseable response
            raise LLMProviderError("sdk", f"Anthropic SDK error: {type(exc).__name__}") from exc

        blocks = list(getattr(response, "content", []) or [])
        usage = getattr(response, "usage", None)
        stop = response.stop_reason if response.stop_reason in _STOP_REASONS else "other"
        return LLMResponse(
            stop_reason=stop,
            text=next((b.text for b in blocks if b.type == "text"), None),
            tool_calls=[ToolCall(b.id, b.name, b.input) for b in blocks if b.type == "tool_use"],
            model=getattr(response, "model", request.model),
            provider=self.name,
            usage=TokenUsage(
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        return self.generate_with_tools(replace(request, tools=[]))
