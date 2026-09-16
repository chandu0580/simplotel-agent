# Load test results

**LOCAL BENCHMARK, NOT PRODUCTION CAPACITY.** Windows 11, 4 cores / 8 threads, 15.3 GiB RAM; Python 3.13.3; uvicorn, 1 worker, in-memory state, rate limits off, client on the same machine.
20.0 s per level after a 3 s warm-up; fresh server process per level. Mock model latency 1500 ms. server_cpu_* is psutil process CPU; 100% = one full core.

| Scenario | Users | Requests | RPS | p50 ms | p95 ms | p99 ms | Error rate | Server CPU avg / max % | Server RSS max MB | Statuses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| offline_turn | 10 | 3353 | 167.4 | 50.3 | 92.7 | 109.6 | 0.00% | 95.7 / 116.3 | 96.2 | {'200': 3353} |
| offline_turn | 25 | 3719 | 184.6 | 117.4 | 205.9 | 284.4 | 0.00% | 97.1 / 115.4 | 100.4 | {'200': 3719} |
| offline_turn | 50 | 3033 | 148.5 | 212.9 | 975.4 | 1692.2 | 0.00% | 74.7 / 117.0 | 107.1 | {'200': 3033} |
| offline_turn | 100 | 1693 | 76.6 | 830.6 | 3475.3 | 5404.9 | 0.00% | 48.0 / 98.5 | 108.1 | {'200': 1693} |
| availability | 10 | 6206 | 310.0 | 28.6 | 55.4 | 72.3 | 0.00% | 101.4 / 122.2 | 90.3 | {'200': 6206} |
| availability | 25 | 3766 | 187.5 | 67.9 | 458.3 | 753.5 | 0.00% | 66.6 / 103.1 | 94.7 | {'200': 3766} |
| availability | 50 | 3052 | 149.6 | 200.0 | 1081.6 | 1732.2 | 0.00% | 56.3 / 99.4 | 97.5 | {'200': 3052} |
| availability | 100 | 2858 | 138.1 | 457.2 | 2170.7 | 4616.7 | 0.00% | 54.3 / 95.9 | 100.0 | {'200': 2858} |
| mock_ai_turn | 10 | 140 | 6.6 | 1513.8 | 1564.2 | 1578.3 | 0.00% | 5.2 / 21.8 | 90.4 | {'200': 140} |
| mock_ai_turn | 25 | 343 | 16.0 | 1521.5 | 1580.7 | 1627.3 | 0.00% | 12.3 / 46.7 | 94.4 | {'200': 343} |
| mock_ai_turn | 50 | 550 | 25.3 | 1673.9 | 2877.4 | 3120.4 | 0.00% | 19.2 / 79.7 | 98.9 | {'200': 550} |
| mock_ai_turn | 100 | 587 | 24.9 | 3396.5 | 6063.6 | 8467.6 | 0.00% | 19.7 / 66.9 | 105.3 | {'200': 587} |
