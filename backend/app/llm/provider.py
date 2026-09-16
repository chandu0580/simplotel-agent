"""Provider-neutral LLM interface.

The assistant builds an `LLMRequest` and receives an `LLMResponse`. Nothing outside
`app/llm/*_provider.py` imports a vendor SDK, so adding OpenAI or Gemini means adding one adapter
that maps these types, not changing the assistant.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class LLMToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: list[LLMMessage]
    model: str
    max_tokens: int
    effort: str | None = None
    tools: list[LLMToolSpec] = field(default_factory=list)
    single_tool_call: bool = True
    # Ask the provider to force a tool call. GLM honours it; Anthropic can't while thinking is on,
    # so that adapter relies on the prompt instruction instead.
    require_tool: bool = False
    cache_system_prompt: bool = True


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    stop_reason: StopReason
    text: str | None
    tool_calls: list[ToolCall]
    model: str
    provider: str
    usage: TokenUsage = TokenUsage()
    latency_ms: int = 0


class LLMProviderError(Exception):
    """Transport/API/SDK failure. `kind` is a stable category for logs and metrics."""

    def __init__(self, kind: Literal["status", "connection", "timeout", "sdk", "protocol"], message: str, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class LLMProvider(Protocol):
    name: str

    def generate_with_tools(self, request: LLMRequest) -> LLMResponse: ...

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Plain generation: same call with no tools."""
