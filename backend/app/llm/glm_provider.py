"""GLM adapter over the OpenAI-compatible Chat Completions protocol (the default runtime provider).

Why this protocol for GLM: it lets us *force* a tool call (`tool_choice: "required"`). With the
Anthropic-format endpoint the model occasionally answered in plain text instead of calling a tool
(an offline fallback for the guest); forcing removes that failure mode at the source.

Timeouts are enforced by the HTTP client (connect/read/write/pool), so an abandoned request is
actually closed rather than left running. Retries: 429/5xx/connection errors only, with jittered
exponential backoff, bounded by `max_retries`.
"""

from collections.abc import Callable
import json
import random
import time
from typing import Any

import httpx

from .provider import LLMProviderError, LLMRequest, LLMResponse, StopReason, TokenUsage, ToolCall

_FINISH_REASONS: dict[str, StopReason] = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class GLMProvider:
    name = "glm"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not base_url:
            raise ValueError("LLM_BASE_URL is required for the GLM provider")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.max_retries = max_retries
        self._sleep = sleep
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=min(5.0, timeout_seconds), read=timeout_seconds, write=10.0, pool=5.0),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    def _body(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "system", "content": request.system}] + [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}} for t in request.tools
            ]
            body["tool_choice"] = "required" if request.require_tool else "auto"
            body["parallel_tool_calls"] = not request.single_tool_call
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = self._client.post(self.url, json=body)
            except httpx.TimeoutException as exc:
                error = LLMProviderError("timeout", f"GLM request timed out: {type(exc).__name__}")
            except httpx.TransportError as exc:
                error = LLMProviderError("connection", f"GLM endpoint unreachable: {type(exc).__name__}")
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise LLMProviderError("protocol", "GLM returned a non-JSON response") from exc
                error = LLMProviderError("status", f"GLM API error {response.status_code}", response.status_code)
                if response.status_code not in _RETRYABLE_STATUS:
                    raise error
            if attempt >= self.max_retries:
                raise error
            self._sleep(min(4.0, 0.25 * (2**attempt)) * (0.5 + random.random() / 2))  # noqa: S311 - jitter
            attempt += 1

    def generate_with_tools(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        data = self._post(self._body(request))
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("protocol", "GLM response has no choices") from exc

        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            raw = function.get("arguments")
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                arguments = raw  # malformed: the assistant rejects it as invalid output
            calls.append(ToolCall(call.get("id") or f"call_{index}", function.get("name", ""), arguments))

        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        return LLMResponse(
            stop_reason=_FINISH_REASONS.get(choice.get("finish_reason") or "", "tool_use" if calls else "other"),
            text=message.get("content") or None,
            tool_calls=calls,
            model=data.get("model") or request.model,
            provider=self.name,
            usage=TokenUsage(usage.get("prompt_tokens"), usage.get("completion_tokens"), cached, None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        from dataclasses import replace

        return self.generate_with_tools(replace(request, tools=[]))

    def close(self) -> None:
        self._client.close()
