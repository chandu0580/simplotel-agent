# Load test results

**LOCAL BENCHMARK, NOT PRODUCTION CAPACITY.** Windows 11, 4 cores / 8 threads, 15.3 GiB RAM; Python 3.13.3; uvicorn, 1 worker, 150 worker threads, in-memory state, rate limits off, client on the same machine.
20.0 s per level after a 3 s warm-up; fresh server process per level. Mock model latency 1500 ms. server_cpu_* is psutil process CPU; 100% = one full core.

| Scenario | Users | Requests | RPS | p50 ms | p95 ms | p99 ms | Error rate | Server CPU avg / max % | Server RSS max MB | Statuses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| mock_ai_turn | 50 | 657 | 30.8 | 1534.4 | 1679.4 | 1768.2 | 0.00% | 23.0 / 79.3 | 100.7 | {'200': 657} |
| mock_ai_turn | 100 | 1070 | 49.8 | 1522.1 | 5299.8 | 7040.8 | 0.00% | 32.4 / 81.2 | 112.8 | {'200': 1070} |
