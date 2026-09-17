# Readiness matrix

**State: hardened modular monolith with local evidence. Not deployed to production, not production-ready.**

Evidence below comes from automated tests, one local run of the optional Redis and PostgreSQL adapter tests against real services, a local load test, and live evaluations against a **GLM 5.2 development gateway**, which is the default runtime provider. The Anthropic adapter is tested only against a mocked HTTP transport. **Anthropic live API: NOT VERIFIED — no Anthropic credential.** The GitHub Actions workflows have **not been run on GitHub**.

**Docker/containerization: NOT REQUIRED FOR CURRENT PROJECT — removed intentionally.** The app runs locally with a Python virtual environment (FastAPI) and npm (React + Vite); see [DEPLOYMENT.md](DEPLOYMENT.md).

## Status legend

| Status | Meaning |
|---|---|
| **IMPLEMENTED + TESTED** | Code on the running path, with automated tests or a recorded local run that exercises it |
| **IMPLEMENTED + NOT VERIFIED** | Code exists but has not been exercised in the environment where it matters (e.g. CI on GitHub) |
| **DESIGNED** | An interface, schema or written design exists; no working implementation on the running path |
| **NOT IMPLEMENTED** | Nothing beyond, at most, a placeholder |

## Capabilities

| Capability | Status | Evidence | Remaining gap |
|---|---|---|---|
| GLM runtime provider (default) | IMPLEMENTED + TESTED | `app/llm/glm_provider.py`: forced tool call, typed errors, retry policy, HTTPS required in production. Contract tests with mocked transport; real slow-server timeout test. Live GLM 5.2: development suite 34/34 (`glm-5.2-final`, also adapter runs 1–2: 34/34), holdout 12/12 | Production GLM endpoint not chosen; provider SLA, quota and data terms not assessed |
| Anthropic provider (alternative) | IMPLEMENTED + NOT VERIFIED | Adapter tested with the real SDK against a mocked HTTP transport; provider-neutral contract and failure tests | **Live verification: NOT VERIFIED — no Anthropic credential** |
| Provider-neutral contract | IMPLEMENTED + TESTED | `tests/test_contracts.py`: tool calls, no tools on `generate`, and timeout / 503 / plain-text output → identical fallback and public code across anthropic, glm and scripted providers (9 cases) | — |
| Multi-tenancy (application) | IMPLEMENTED + TESTED | Tenant-scoped repositories and keys; cross-hotel 404s (`test_tenancy.py`); cross-tenant read on another replica → 404; holdout `holdout-cross-tenant-facts` (offline and GLM) | Persistent tenant registry; tenant admin API |
| Tenant isolation (database, optional PostgreSQL) | IMPLEMENTED + TESTED (locally once; not run in CI) | `migrations/0001`: tenant_id everywhere, composite FKs, RLS forced on 11 tables. Against PostgreSQL 17 with a non-superuser role: RLS hides and blocks cross-tenant reads and writes; FKs block cross-tenant references even for a superuser | Only the audit sink writes to PostgreSQL; other repositories DESIGNED |
| Shared state for multiple processes (Redis, optional) | IMPLEMENTED + TESTED (locally once) | `app/state/redis_backend.py`. Against Redis 7.4: CAS + TTL, shared limits, locks, idempotency once across stores; three in-process app instances sharing Redis: no lost turns, one booking, shared limits. Tests skip without `TEST_REDIS_URL` and are **not run in CI** | Redis TLS/auth not exercised; not needed for the current project (in-memory is the default) |
| Concurrency safety | IMPLEMENTED + TESTED | Lease locks + compare-and-set versions; with locks disabled CAS alone rejected 5 of 6 concurrent writers (409), no lost update; 409 `CONVERSATION_BUSY` + client auto-retry | — |
| Idempotent bookings | IMPLEMENTED + TESTED | Same key → one booking across concurrent calls in memory and across 3 in-process instances sharing Redis; conflict on reuse; one durable `BookingConfirmed` per booking; unique DB constraint tested | Bookings not persisted to PostgreSQL; key not forwarded to a real PMS (none exists) |
| Cloudbeds PMS adapter (live availability) | IMPLEMENTED + NOT VERIFIED | `app/reservations/cloudbeds_provider.py` against the documented v1.3 `getAvailableRoomTypes`; 17 adapter tests plus the shared provider contract, all over stub transports | **Not yet run against a real Cloudbeds account**; booking writes deliberately NOT IMPLEMENTED (guest details not collected) |
| Reservation provider contract | IMPLEMENTED + TESTED | Typed results and errors, tenant context, timeout, read-only retries with overall deadline, breaker, audit events ([RESERVATION_INTEGRATION.md](RESERVATION_INTEGRATION.md)) | Real PMS/CRS adapter NOT IMPLEMENTED |
| Circuit breaker | IMPLEMENTED + TESTED | Single half-open trial (5 concurrent callers → 1 trial), failed trial reopens, business errors never count, retries count once | Per-process state (by design) |
| Timeouts | IMPLEMENTED + TESTED | LLM HTTP timeouts close the connection (real slow server); tool and integration timeouts; retry deadline below tool timeout | Timed-out Python threads are not cancelled (documented; mutations rely on idempotency) |
| Error model | IMPLEMENTED + TESTED | Stable codes and envelope, `meta.degradation`, no stack traces (`tests/test_errors.py`: 422, 404, 405, 413, 503 knowledge, 500 hidden detail, degradation per failure kind) | — |
| Rate limiting | IMPLEMENTED + TESTED | ip_burst and ip in the HTTP middleware for every `/api/` request (unknown routes and invalid bodies count), then tenant, hotel, conversation; 429 + Retry-After; shared across instances when Redis is used (tested in-process) | Fails open when Redis is down (deliberate); no API-key dimension |
| Conversational routing | IMPLEMENTED + TESTED | Greetings, capability questions, everyday small talk, thanks and goodbye are answered deterministically before retrieval or any model call, in both AI and FAQ mode; the booking form opens only for availability intent (`app/assistant/conversation.py`, `tests/test_conversation.py`, 6 eval scenarios). Off-topic requests (coding, weather) are declined briefly **without** front-desk escalation in AI mode | Small talk outside these five intents, and misspelled small talk, still goes to the model or the FAQ engine; the FAQ engine cannot tell off-topic from an unknown hotel fact, so it still escalates |
| Security test suite | IMPLEMENTED + TESTED | Guardrail, injection, exfiltration, tool-authorization, cross-tenant, error and privacy tests; holdout adversarial evals (offline 12/12, GLM 12/12, critical 10/10) | Penetration test; live-model red teaming beyond 12 holdout scenarios |
| Secret hygiene | IMPLEMENTED + TESTED | `scripts/scan_secrets.py`: tracked files 0 findings, frontend bundle 0, eval results 0; settings `repr` and log redaction tests | Scanner not yet run in GitHub CI |
| Security headers | IMPLEMENTED + TESTED (API) / NOT IMPLEMENTED (static hosting) | Backend sets nosniff, DENY, no-referrer, `Cache-Control: no-store` on `/api`, HSTS in production (`app/api/middleware.py`). `test_security_headers_request_ids_and_trace_propagation` asserts `X-Content-Type-Options`, `X-Frame-Options` and `Cache-Control`; `Referrer-Policy` and HSTS are set in code but not asserted by a test | CSP, Permissions-Policy and HSTS for the built SPA are a hosting requirement ([DEPLOYMENT.md](DEPLOYMENT.md)) |
| Privacy (minimisation) | IMPLEMENTED + TESTED | Card/email/phone masking before model, storage and traces; false-positive tests; `pii_masked_total` ([PRIVACY.md](PRIVACY.md)) | Names/addresses not detected |
| Retention and deletion | IMPLEMENTED + TESTED | Conversation TTL (memory and Redis), DELETE → 404 thereafter, DB retention job under RLS | Retention job not scheduled; log retention not configured |
| Observability | IMPLEMENTED + TESTED | Access logs and traces carry request, trace, tenant, hotel and conversation ids; latency breakdown; metrics asserted to move (`tests/test_observability.py`) | Log shipping, dashboards, alerting, trace export not deployed |
| Performance evidence | IMPLEMENTED + TESTED | `perf/load_test.py` at 10/25/50/100 users, 0% errors; thread-pool bottleneck found and relieved ([PERFORMANCE.md](PERFORMANCE.md)) | Local benchmark only; no multi-worker, Redis-backed or real-model load test |
| Evaluation gates | IMPLEMENTED + TESTED | 41-scenario development suite, 12-scenario holdout, critical flag, `--fail-on-critical` (exit 4), `--baseline` (exit 3); offline 28/28 (critical 14/14) | Larger dataset from real traffic; LLM-judge grading |
| CI (standard) | IMPLEMENTED + NOT VERIFIED | `ci.yml`: backend (ruff, pytest, offline eval gate, pip-audit), security (secret scan, no committed `.env`), frontend (oxlint, tsc + build, Vitest, bundle secret scan, npm audit), e2e (Playwright). No Docker needed. Every step was run locally, not as a workflow | First run on GitHub |
| CI (live AI eval) | IMPLEMENTED + NOT VERIFIED | `live-ai-eval.yml`: manual, provider choice, protected secrets, sanitised inputs | First run; environment secrets |
| Docker / containerization | NOT REQUIRED FOR CURRENT PROJECT | Removed intentionally (Dockerfiles, compose, nginx edge, container-only scripts and CI jobs) | — (not a gap) |
| Graceful shutdown | IMPLEMENTED + TESTED (lifespan) | FastAPI lifespan flushes audit events and closes clients (`Container.close`, exercised whenever a test client exits; audit flush asserted in the PostgreSQL tests) | SIGTERM draining under uvicorn not measured in the current setup; readiness does not flip to draining |
| Migrations | IMPLEMENTED + TESTED | Ordered, transactional, checksummed, advisory-locked; run with `python -m app.db.migrate` (optional PostgreSQL only; tested locally once, not in CI) | Rollback strategy (forward-only by design) |
| Configuration and flags | IMPLEMENTED + TESTED | Startup validation incl. HTTPS, mock and lease rules; unknown flags fail startup ([CONFIGURATION.md](CONFIGURATION.md)) | Runtime flag changes; secret manager |
| Frontend resilience | IMPLEMENTED + TESTED | 24 Vitest tests incl. landing quick actions, busy retry, 503, unexpected bodies, abort timeout, 413, long content; Playwright 10/10 (desktop + mobile) | Full screen-reader audit |
| Admin authentication | DESIGNED | `AuthProvider` boundary; default refuses with 401 `UNAUTHORIZED` | OIDC/JWT NOT IMPLEMENTED |
| Guest authentication | NOT IMPLEMENTED | Booking tools require a principal that nothing issues | Identity provider integration |
| Semantic retrieval | NOT IMPLEMENTED | Flag fails startup if enabled | Only if knowledge outgrows the prompt |
| WhatsApp / voice channels | DESIGNED | Render adapters with tests; flags reserved | Webhooks, telephony |
| Deployment infrastructure | NOT REQUIRED FOR CURRENT PROJECT | Local run and hosting requirements in [DEPLOYMENT.md](DEPLOYMENT.md) | Chosen when the product actually needs a deployment |
| SLOs / on-call | DESIGNED | [SRE.md](SRE.md) proposals | Measured SLOs, alerting, on-call |

## Verification evidence (run 2026-09-16, final code)

| Check | Result |
|---|---|
| Backend pytest with the optional Redis 7.4 + PostgreSQL 17 services (run locally once, before Docker removal) | **292 passed** |
| Backend pytest without services (CI `backend` job equivalent) | **390 passed, 22 skipped** (optional Redis/PostgreSQL integration tests) |
| Lint / types | ruff clean; oxlint clean; `tsc -b` clean |
| Frontend Vitest | **24 passed** |
| Playwright E2E (desktop + mobile, AI disabled) | **10 passed** |
| Offline eval, development suite | **34/34** (7 AI-only skipped; 41 scenarios incl. 6 conversational), critical 14/14, no regressions vs baseline |
| Offline eval, holdout suite | **12/12**, critical 10/10 |
| GLM 5.2 live, development suite (runtime evidence, not Claude) | **41/41**, critical 15/15, decision accuracy 18/18, groundedness 14/14, p50 1.7 s / p95 3.4 s after the brevity rules (was 3.7 s / 10.1 s), no regressions (`evals/results/glm-ux-fix`) |
| GLM 5.2 live, holdout suite | **12/12**, critical 10/10 (`evals/results/glm-5.2-holdout-run1`) |
| Load test (local, not capacity) | 0% errors at 10–100 users in all scenarios; see [PERFORMANCE.md](PERFORMANCE.md) |
| Secret scans | 0 findings: tracked files, frontend bundle |
| Anthropic live API | **NOT VERIFIED — no Anthropic credential** |
| GitHub Actions | **Not run** (workflows exist; no push was made) |

## Not claimed

- Production readiness or production capacity
- Live Anthropic Claude verification
- That CI passes on GitHub
- A real PMS/CRS integration
- High availability of Redis or PostgreSQL
- Compliance certification, a penetration test, achieved SLOs
