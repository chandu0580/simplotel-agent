"""Latency-only mock provider for load tests and demos (never allowed in production).

It answers every turn with a grounded `answer_guest` call after a fixed delay, so load tests can
exercise the full AI code path with realistic model latency without sending traffic to a paid model.
"""

from dataclasses import replace
import time

from .provider import LLMRequest, LLMResponse, TokenUsage, ToolCall


class LatencyMockProvider:
    name = "mock"

    def __init__(self, latency_ms: int):
        self.latency_ms = latency_ms

    def generate_with_tools(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        time.sleep(self.latency_ms / 1000)
        answer = {"type": "answer", "text": "Check-in is from 2:00 PM and check-out is by 11:00 AM.", "source_ids": ["timings.check_in_out"], "suggestions": []}
        return LLMResponse(
            "tool_use",
            None,
            [ToolCall("mock_1", "answer_guest", answer)],
            "mock-latency",
            self.name,
            TokenUsage(2800, 60, 0, 0),
            int((time.perf_counter() - started) * 1000),
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        return self.generate_with_tools(replace(request, tools=[]))
