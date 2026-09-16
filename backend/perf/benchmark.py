"""Local performance profile of the application's own overhead.

    python -m perf.benchmark [--iterations 300]

Measures each layer separately so optimisation decisions rest on numbers:
  * retrieval (full-context, keyword)
  * availability tool (deterministic inventory + pricing, through the resilience wrapper)
  * assistant turn with a zero-latency scripted model (prompt build + guardrails + tracing = app overhead)
  * offline assistant turn
  * HTTP round trip through FastAPI middleware (in-process client, no network)

LLM latency is not measured here; it depends on the provider (see docs/EVALUATION.md for measured
development-provider latency). Results go to perf/results.md.
"""

import argparse
from datetime import date
import json
from pathlib import Path
import platform
import statistics
import time

from fastapi.testclient import TestClient

from app.assistant.turn import TurnRequest
from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.knowledge.retrieval import FullContextRetriever, KeywordRetriever
from app.llm.provider import LLMResponse, TokenUsage, ToolCall
from app.llm.scripted_provider import ScriptedLLMProvider
from app.main import create_app
from app.tenancy import Channel
from app.tools.base import ToolContext

TODAY = date(2026, 10, 5)
HOTEL = "hotel-goa-001"


def measure(fn, iterations: int) -> dict:
    for _ in range(min(20, iterations)):  # warm-up
        fn()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return {
        "p50_ms": round(samples[len(samples) // 2], 3),
        "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 3),
        "max_ms": round(samples[-1], 3),
        "mean_ms": round(statistics.mean(samples), 3),
        "iterations": iterations,
    }


class EndlessScripted(ScriptedLLMProvider):
    """Always answers instantly, so the measurement isolates application overhead."""

    def generate_with_tools(self, request):
        self.requests.clear()
        return LLMResponse(
            "tool_use",
            None,
            [ToolCall("t", "answer_guest", {"type": "answer", "text": "Check-in is from 2:00 PM.", "source_ids": ["timings.check_in_out"], "suggestions": []})],
            "scripted",
            "scripted",
            TokenUsage(3000, 50, 0),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=300)
    n = parser.parse_args().iterations

    settings = Settings.for_tests(log_level="CRITICAL", availability_cache_ttl_seconds=0)
    offline = build_container(settings, clock=FixedClock(TODAY))
    ai = build_container(settings, clock=FixedClock(TODAY), llm_provider=EndlessScripted())
    kb = offline.knowledge.snapshot(HOTEL, TODAY)
    ctx = offline.tenants.resolve(HOTEL, Channel.WEB)
    tool_ctx = ToolContext(tenant=ctx, kb=kb, today=TODAY)
    availability_args = {"check_in": "2026-10-07", "check_out": "2026-10-10", "adults": 2, "children": 0}

    results = {
        "retrieval_full_context": measure(lambda: FullContextRetriever().retrieve(kb, "What time is check-in?"), n),
        "retrieval_keyword": measure(lambda: KeywordRetriever().retrieve(kb, "Can I cancel my booking and get a refund?"), n),
        "tool_check_availability_3_nights": measure(lambda: offline.tools.execute("check_availability", availability_args, tool_ctx, invoked_by="system"), n),
        "assistant_turn_ai_scripted_model": measure(lambda: ai.assistant.handle(TurnRequest(tenant=ctx, message="What time is check-in?")), n),
        "assistant_turn_offline": measure(lambda: offline.assistant.handle(TurnRequest(tenant=ctx, message="What is the cancellation policy?")), n),
    }

    with TestClient(create_app(container=offline)) as client:
        cid = client.post(f"/api/v1/hotels/{HOTEL}/conversations", json={}).json()["conversation_id"]
        results["http_conversation_message_offline"] = measure(
            lambda: client.post(f"/api/v1/hotels/{HOTEL}/conversations/{cid}/messages", json={"message": "Is breakfast included?"}), n
        )
        results["http_availability"] = measure(lambda: client.post(f"/api/v1/hotels/{HOTEL}/availability", json={**availability_args}), n)
        results["http_health"] = measure(lambda: client.get("/health"), n)

    env = {"python": platform.python_version(), "machine": platform.machine(), "system": f"{platform.system()} {platform.release()}", "date": time.strftime("%Y-%m-%d")}
    out = Path(__file__).parent
    (out / "results.json").write_text(json.dumps({"environment": env, "results": results}, indent=2), encoding="utf-8")
    lines = [
        "# Local performance profile",
        "",
        f"Measured {env['date']} on {env['system']} ({env['machine']}), Python {env['python']}; {n} iterations each after warm-up.",
        "In-process measurements: no network, no real LLM. They show application overhead only.",
        "",
        "| Operation | p50 (ms) | p95 (ms) | max (ms) |",
        "|---|---|---|---|",
    ]
    lines += [f"| {name} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} |" for name, r in results.items()]
    (out / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
