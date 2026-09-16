# Local performance profile

Measured 2026-09-16 on Windows 11 (AMD64), Python 3.13.3; 300 iterations each after warm-up.
In-process measurements: no network, no real LLM. They show application overhead only.

| Operation | p50 (ms) | p95 (ms) | max (ms) |
|---|---|---|---|
| retrieval_full_context | 0.029 | 0.036 | 0.09 |
| retrieval_keyword | 0.554 | 1.168 | 2.361 |
| tool_check_availability_3_nights | 1.95 | 5.203 | 12.639 |
| assistant_turn_ai_scripted_model | 2.149 | 4.049 | 6.98 |
| assistant_turn_offline | 2.568 | 5.846 | 13.37 |
| http_conversation_message_offline | 8.563 | 10.975 | 14.92 |
| http_availability | 7.197 | 10.298 | 14.044 |
| http_health | 4.385 | 7.81 | 12.468 |
