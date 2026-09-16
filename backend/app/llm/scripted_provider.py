"""Deterministic provider for tests and contract checks: replays scripted responses."""

from dataclasses import replace

from .provider import LLMRequest, LLMResponse


class ScriptedLLMProvider:
    name = "scripted"

    def __init__(self, responses: list[LLMResponse | Exception] | None = None):
        self.responses = list(responses or [])
        self.requests: list[LLMRequest] = []

    def generate_with_tools(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("ScriptedLLMProvider has no scripted response left")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def generate(self, request: LLMRequest) -> LLMResponse:
        return self.generate_with_tools(replace(request, tools=[]))
