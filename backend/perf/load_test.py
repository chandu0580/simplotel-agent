"""Reproducible local load test. LOCAL BENCHMARK, NOT PRODUCTION CAPACITY.

    python -m perf.load_test [--levels 10,25,50,100] [--seconds 20] [--scenarios offline_turn,availability,mock_ai_turn]

Starts the real API (uvicorn, one worker, the same command the container runs) as a subprocess and drives it
over real HTTP with closed-loop virtual users (each user sends its next request as soon as the previous one
completes). Scenarios:

* offline_turn   - POST a guest question to a conversation; AI disabled, so no model is involved.
* availability   - POST /availability (deterministic inventory search).
* mock_ai_turn   - POST a guest question with LLM_PROVIDER=mock: the full AI path (prompt build, tool
                   parsing, guardrails, tracing) with a fixed simulated model latency (default 1500 ms).

Safety: the server environment is built explicitly. Every provider credential and base URL is blanked, and
the only LLM provider ever selected is `mock`, so the test cannot send traffic to a paid model. Rate limits
are disabled for the run (they would otherwise be the thing being measured).

Caveats (also printed in the report): client and server share one machine, so client CPU competes with the
server; one uvicorn worker; in-memory state backend; no network latency.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time

import httpx
import psutil

BACKEND = Path(__file__).resolve().parents[1]
HOTEL = "hotel-goa-001"
BLANKED = ["LLM_API_KEY", "LLM_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "REDIS_URL", "DATABASE_URL"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(scenario: str, mock_latency_ms: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "ANTHROPIC_", "OPENAI_", "FEATURE_"))}
    env.update({name: "" for name in BLANKED})  # present-but-empty: python-dotenv never overrides existing variables
    env.update(
        {
            "APP_ENV": "development",
            "LOG_LEVEL": "WARNING",
            "LOG_FORMAT": "json",
            "RATE_LIMIT_ENABLED": "false",
            "STATE_BACKEND": "memory",
            "AI_ENABLED": "true" if scenario == "mock_ai_turn" else "false",
            "LLM_PROVIDER": "mock" if scenario == "mock_ai_turn" else "none",
            "MOCK_LLM_LATENCY_MS": str(mock_latency_ms),
            "CONVERSATION_MAX_MESSAGES": "40",
            "WORKER_THREADS": os.environ.get("LOAD_TEST_WORKER_THREADS", "40"),
        }
    )
    return env


class Server:
    def __init__(self, scenario: str, mock_latency_ms: int):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log", "--log-level", "warning"]
        self.proc = subprocess.Popen(cmd, cwd=BACKEND, env=server_env(scenario, mock_latency_ms), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603 - fixed argv: this interpreter + uvicorn
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                ready = httpx.get(f"{self.base}/ready", timeout=1)
                if ready.status_code == 200:
                    llm = ready.json()["checks"]["llm"]
                    expected = "configured" if scenario == "mock_ai_turn" else "not_configured"
                    if llm != expected:
                        raise RuntimeError(f"unexpected llm check {llm!r} for {scenario}")
                    self.ps = psutil.Process(self.proc.pid)
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("server did not become ready")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@dataclass
class Sampler:
    ps: psutil.Process
    cpu: list[float] = field(default_factory=list)
    rss_mb: list[float] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)

    def run(self) -> None:
        # On Windows a venv python.exe is a launcher that runs the interpreter as a child: sample the whole tree.
        procs = [self.ps, *self.ps.children(recursive=True)]
        for proc in procs:
            proc.cpu_percent(None)
        while not self.stop.wait(0.5):
            cpu = rss = 0.0
            for proc in procs:
                try:
                    cpu += proc.cpu_percent(None)
                    rss += proc.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            self.cpu.append(cpu)
            self.rss_mb.append(rss / 2**20)


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, round(p / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[index]


async def run_level(base: str, scenario: str, users: int, seconds: float) -> dict:
    api = f"{base}/api/v1/hotels/{HOTEL}"
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        conversations = []
        if scenario != "availability":
            for _ in range(users):
                conversations.append((await client.post(f"{api}/conversations", json={})).json()["conversation_id"])
        deadline = time.perf_counter() + seconds

        async def user(index: int) -> None:
            sent = 0
            while time.perf_counter() < deadline:
                if scenario != "availability" and sent and sent % 15 == 0:  # stay under the stored-message cap; not timed
                    conversations[index] = (await client.post(f"{api}/conversations", json={})).json()["conversation_id"]
                started = time.perf_counter()
                try:
                    if scenario == "availability":
                        response = await client.post(f"{api}/availability", json={"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2})
                    else:
                        response = await client.post(f"{api}/conversations/{conversations[index]}/messages", json={"message": "What time is check-in?"})
                    key = str(response.status_code)
                except httpx.HTTPError as exc:
                    key = type(exc).__name__
                latencies.append((time.perf_counter() - started) * 1000)
                statuses[key] = statuses.get(key, 0) + 1
                sent += 1

        started = time.perf_counter()
        await asyncio.gather(*(user(i) for i in range(users)))
        elapsed = time.perf_counter() - started
    latencies.sort()
    total = len(latencies)
    ok = sum(v for k, v in statuses.items() if k.startswith("2"))
    return {
        "users": users,
        "requests": total,
        "duration_s": round(elapsed, 1),
        "rps": round(total / elapsed, 1),
        "p50_ms": round(percentile(latencies, 50), 1),
        "p95_ms": round(percentile(latencies, 95), 1),
        "p99_ms": round(percentile(latencies, 99), 1),
        "max_ms": round(latencies[-1], 1) if latencies else 0,
        "error_rate": round(1 - ok / total, 4) if total else 0,
        "statuses": statuses,
    }


def run_scenario(scenario: str, levels: list[int], seconds: float, mock_latency_ms: int) -> list[dict]:
    rows = []
    for users in levels:
        server = Server(scenario, mock_latency_ms)  # fresh process per level: no warm state carried over
        sampler = Sampler(server.ps)
        thread = threading.Thread(target=sampler.run, daemon=True)
        try:
            asyncio.run(run_level(server.base, scenario, min(users, 5), 3))  # warm-up, not recorded
            thread.start()
            row = asyncio.run(run_level(server.base, scenario, users, seconds))
        finally:
            sampler.stop.set()
            thread.join()
            server.stop()
        row["server_cpu_avg_pct"] = round(statistics.mean(sampler.cpu), 1) if sampler.cpu else None
        row["server_cpu_max_pct"] = round(max(sampler.cpu), 1) if sampler.cpu else None
        row["server_rss_max_mb"] = round(max(sampler.rss_mb), 1) if sampler.rss_mb else None
        row["scenario"] = scenario
        rows.append(row)
        print(json.dumps(row))
    return rows


def write_report(rows: list[dict], args) -> None:
    meta = {
        "label": "LOCAL BENCHMARK, NOT PRODUCTION CAPACITY",
        "machine": f"{platform.system()} {platform.release()}, {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads, {round(psutil.virtual_memory().total / 2**30, 1)} GiB RAM",
        "python": platform.python_version(),
        "server": f"uvicorn, 1 worker, {os.environ.get('LOAD_TEST_WORKER_THREADS', '40')} worker threads, in-memory state, rate limits off, client on the same machine",
        "seconds_per_level": args.seconds,
        "mock_llm_latency_ms": args.mock_latency_ms,
        "cpu_note": "server_cpu_* is psutil process CPU; 100% = one full core",
    }
    out = BACKEND / "perf"
    (out / "load_results.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=2), encoding="utf-8")
    lines = [
        "# Load test results",
        "",
        f"**{meta['label']}.** {meta['machine']}; Python {meta['python']}; {meta['server']}.",
        f"{args.seconds} s per level after a 3 s warm-up; fresh server process per level. Mock model latency {args.mock_latency_ms} ms. {meta['cpu_note']}.",
        "",
        "| Scenario | Users | Requests | RPS | p50 ms | p95 ms | p99 ms | Error rate | Server CPU avg / max % | Server RSS max MB | Statuses |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['users']} | {r['requests']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | {r['error_rate']:.2%} | "
            f"{r['server_cpu_avg_pct']} / {r['server_cpu_max_pct']} | {r['server_rss_max_mb']} | {r['statuses']} |"
        )
    (out / "load_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="10,25,50,100")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--scenarios", default="offline_turn,availability,mock_ai_turn")
    parser.add_argument("--mock-latency-ms", type=int, default=1500)
    args = parser.parse_args()
    levels = [int(x) for x in args.levels.split(",")]
    rows = []
    for scenario in args.scenarios.split(","):
        rows += run_scenario(scenario, levels, args.seconds, args.mock_latency_ms)
    write_report(rows, args)


if __name__ == "__main__":
    main()
