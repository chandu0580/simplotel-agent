# Performance

> **Local benchmark, not production capacity.** Every number here was measured on one developer laptop, with the load generator on the same machine. They show where this code's bottlenecks are and how it behaves as load grows. They say nothing about how many hotels or guests a production deployment can serve.

## How it was measured

```bash
cd backend
python -m perf.load_test                                   # 3 scenarios × 10/25/50/100 users, 20 s each → perf/load_results.md
LOAD_TEST_WORKER_THREADS=150 python -m perf.load_test --levels 50,100 --scenarios mock_ai_turn
python -m perf.benchmark                                   # in-process profile of each layer → perf/results.md
```

`perf/load_test.py` starts the real API as a subprocess using the container's command (`uvicorn app.main:app`, one worker). It then drives the API over real HTTP with closed-loop virtual users: each user sends its next request as soon as the previous one returns.

| Setting | Value |
|---|---|
| Machine | Windows 11, 4 cores / 8 threads, 15.3 GiB RAM |
| Runtime | Python 3.13.3, uvicorn 1 worker, in-memory state backend |
| Rate limits | Off (otherwise they are what gets measured) |
| Duration | 3 s warm-up (not recorded), then 20 s per level; a fresh server process for every level |
| Server CPU / memory | psutil, sampled every 0.5 s over the server's process tree (100% = one core) |
| Client | Same machine (`httpx.AsyncClient`), so it competes with the server for CPU |

**No paid model traffic.** The load test builds the server environment explicitly: every provider key and base URL is blanked, and the only LLM provider it ever selects is `mock`. At startup it checks `/ready`: the `llm` check must read `not_configured` for offline scenarios and `configured` (mock) for the AI scenario.

### Scenarios

| Scenario | Request | What it exercises |
|---|---|---|
| `offline_turn` | `POST /conversations/{id}/messages` ("What time is check-in?"), AI disabled | Conversation lock, knowledge snapshot, input guardrails, keyword retrieval, offline engine, tracing, events, state write |
| `availability` | `POST /availability` | Validation, resilience wrapper, inventory search and pricing |
| `mock_ai_turn` | Same as `offline_turn` with `LLM_PROVIDER=mock`, 1,500 ms model latency | The full AI path: prompt build, tool parsing, output guardrails, tracing. The model wait is simulated |

Each virtual user starts a new conversation every 15 turns, outside the timed section, so the stored-message cap is never what gets measured.

## Results (40 worker threads, the framework default at the time)

Source: `backend/perf/load_results.md` and `.json`. Error rate was **0.00%** in every row.

| Scenario | Users | RPS | p50 ms | p95 ms | p99 ms | Server CPU avg / max % | RSS max MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| offline_turn | 10 | 167.4 | 50.3 | 92.7 | 109.6 | 95.7 / 116.3 | 96.2 |
| offline_turn | 25 | 184.6 | 117.4 | 205.9 | 284.4 | 97.1 / 115.4 | 100.4 |
| offline_turn | 50 | 148.5 | 212.9 | 975.4 | 1,692.2 | 74.7 / 117.0 | 107.1 |
| offline_turn | 100 | 76.6 | 830.6 | 3,475.3 | 5,404.9 | 48.0 / 98.5 | 108.1 |
| availability | 10 | 310.0 | 28.6 | 55.4 | 72.3 | 101.4 / 122.2 | 90.3 |
| availability | 25 | 187.5 | 67.9 | 458.3 | 753.5 | 66.6 / 103.1 | 94.7 |
| availability | 50 | 149.6 | 200.0 | 1,081.6 | 1,732.2 | 56.3 / 99.4 | 97.5 |
| availability | 100 | 138.1 | 457.2 | 2,170.7 | 4,616.7 | 54.3 / 95.9 | 100.0 |
| mock_ai_turn | 10 | 6.6 | 1,513.8 | 1,564.2 | 1,578.3 | 5.2 / 21.8 | 90.4 |
| mock_ai_turn | 25 | 16.0 | 1,521.5 | 1,580.7 | 1,627.3 | 12.3 / 46.7 | 94.4 |
| mock_ai_turn | 50 | 25.3 | 1,673.9 | 2,877.4 | 3,120.4 | 19.2 / 79.7 | 98.9 |
| mock_ai_turn | 100 | 24.9 | 3,396.5 | 6,063.6 | 8,467.6 | 19.7 / 66.9 | 105.3 |

## What the numbers show

**1. The AI path was capped by the thread pool, not by CPU.** AI turns plateaued at about 25 RPS from 50 users upward while the server used under 20% of one core. Sync request handlers run in AnyIO's worker thread pool, which defaulted to 40 threads, and each AI turn holds a thread for the model call: 40 threads ÷ 1.5 s ≈ 26.7 turns per second. Above that, requests queue, and at 100 users p50 doubled to 3.4 s.

The thread pool size is now configurable (`WORKER_THREADS`). Re-measured with 150 threads (`backend/perf/load_results_mock_ai_threads150.md`):

| Scenario | Users | Threads | RPS | p50 ms | p95 ms | p99 ms | RSS max MB | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| mock_ai_turn | 50 | 40 | 25.3 | 1,673.9 | 2,877.4 | 3,120.4 | 98.9 | 0% |
| mock_ai_turn | 50 | **150** | **30.8** | **1,534.4** | **1,679.4** | **1,768.2** | 100.7 | 0% |
| mock_ai_turn | 100 | 40 | 24.9 | 3,396.5 | 6,063.6 | 8,467.6 | 105.3 | 0% |
| mock_ai_turn | 100 | **150** | **49.8** | **1,522.1** | 5,299.8 | 7,040.8 | 112.8 | 0% |

With 150 threads, 100-user AI throughput doubled and median latency returned to the model's own latency, for about 8 MB more memory. The p95 at 100 users is still 5.3 s, so part of the tail remains. Its cause wasn't isolated; candidates are the single event loop, the client sharing the machine, and GIL contention on the non-model part of each turn. The default is now `WORKER_THREADS=150`. A real model with its own latency and rate limits will behave differently, and the right value per replica should be set from production measurements.

**2. CPU-bound paths saturate one core at about 25 users.** Offline turns and availability searches use about 100% of one core by 10–25 users. After that, throughput falls and tail latency grows (offline p95 3.5 s at 100 users). This is expected for one Python worker. The remedies are more workers per container or more replicas; the Redis state backend (see [DEPLOYMENT.md](DEPLOYMENT.md)) exists so replicas share conversations, limits and idempotency. Adding workers was **not** measured here.

**3. Memory is flat.** Server RSS stayed between 90 and 113 MB in every run.

**4. The client competes with the server.** Server CPU drops below 100% at 50–100 users while latency climbs, which suggests part of the degradation is the load generator on the same 4-core machine. A separate client machine was not used.

## Where time goes inside one request

The in-process profile (`backend/perf/results.md`, `python -m perf.benchmark`) measures each layer without network or model. An HTTP conversation turn took p50 8.6 ms / p95 11.0 ms, and an AI turn's application overhead with an instant scripted model was p50 2.1 ms.

At runtime every AI trace separates the latency sources (`AITrace`, see [OBSERVABILITY.md](OBSERVABILITY.md)):

| Field | Meaning |
|---|---|
| `knowledge_latency_ms` | Loading the tenant's knowledge snapshot for today (cached) |
| `retrieval_latency_ms` | Selecting evidence for the question |
| `llm_latency_ms` | The provider call |
| `tool_latency_ms` | Sum of tool executions (e.g. availability) |
| `app_latency_ms` | `total − llm − tools`: everything this service controls (knowledge, retrieval, prompt build, guardrails, validation, tracing) |
| `total_latency_ms` | The whole turn |

These fields also feed the histograms `turn_latency_ms{mode}`, `app_latency_ms{mode}` and `retrieval_latency_ms`. A test checks that total ≈ llm + tools + app to within 1 ms.

For comparison, measured live-model latency (GLM 5.2, the development suite through the GLM adapter) was p50 about 5.5 s and p95 12.6–14.3 s per scenario ([EVALUATION.md](EVALUATION.md)). The model dominates end-to-end latency; application overhead is milliseconds.

## Not measured

- Multiple uvicorn workers, or multiple replicas under load (replicas were verified functionally, not for throughput)
- The Redis state backend under load (every run used in-memory state)
- A real model under concurrent load (no paid traffic was generated)
- Network latency between client, edge, API, Redis and PostgreSQL
- Long soak tests, memory growth over hours, garbage-collection pauses
- A dedicated load-generator machine
