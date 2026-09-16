# Readiness matrix

**State: hardened modular monolith with local evidence. Not deployed to production, not production-ready.**

Evidence below comes from automated tests, local runs against real Redis and PostgreSQL containers, a local three-replica Docker stack, a local load test, and live evaluations against a **GLM 5.2 development gateway**, which is the default runtime provider. The Anthropic adapter is tested only against a mocked HTTP transport. **Anthropic live API: NOT VERIFIED — no Anthropic credential.** The GitHub Actions workflows have **not been run on GitHub**.

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
| Tenant isolation (database) | IMPLEMENTED + TESTED | `migrations/0001`: tenant_id everywhere, composite FKs, RLS forced on 11 tables. Against PostgreSQL 17 with a non-superuser role: RLS hides and blocks cross-tenant reads and writes; FKs block cross-tenant references even for a superuser | Only the audit sink writes to PostgreSQL; other repositories DESIGNED |
| Shared state for replicas (Redis) | IMPLEMENTED + TESTED | `app/state/redis_backend.py`. Real Redis 7.4: CAS + TTL, shared limits, locks, idempotency once across stores. Three in-process replicas: no lost turns, one booking, shared limits. Three containers: 33/33 edge checks, one booking across simultaneous replicas | Redis TLS/auth not exercised; Redis high availability not tested |
| Concurrency safety | IMPLEMENTED + TESTED | Lease locks + compare-and-set versions; with locks disabled CAS alone rejected 5 of 6 concurrent writers (409), no lost update; 409 `CONVERSATION_BUSY` + client auto-retry | — |
| Idempotent bookings | IMPLEMENTED + TESTED | Same key → one booking across 9 concurrent calls on 3 replicas and 3 containers; conflict on reuse; one durable `BookingConfirmed` per booking; unique DB constraint tested | Bookings not persisted to PostgreSQL; key not forwarded to a real PMS (none exists) |
| Reservation provider contract | IMPLEMENTED + TESTED | Typed results and errors, tenant context, timeout, read-only retries with overall deadline, breaker, audit events ([RESERVATION_INTEGRATION.md](RESERVATION_INTEGRATION.md)) | Real PMS/CRS adapter NOT IMPLEMENTED |
| Circuit breaker | IMPLEMENTED + TESTED | Single half-open trial (5 concurrent callers → 1 trial), failed trial reopens, business errors never count, retries count once | Per-process state (by design) |
| Timeouts | IMPLEMENTED + TESTED | LLM HTTP timeouts close the connection (real slow server); tool and integration timeouts; retry deadline below tool timeout | Timed-out Python threads are not cancelled (documented; mutations rely on idempotency) |
| Error model | IMPLEMENTED + TESTED | Stable codes and envelope, `meta.degradation`, no stack traces (`tests/test_errors.py`: 422, 404, 405, 413, 503 knowledge, 500 hidden detail, degradation per failure kind) | — |
| Rate limiting | IMPLEMENTED + TESTED | ip_burst, ip (before hotel resolution), tenant, hotel, conversation; 429 + Retry-After; shared across replicas in Redis (exactly 30 of 60 through the 3-replica edge) | Fails open when Redis is down (deliberate); no API-key dimension; no WAF |
| Security test suite | IMPLEMENTED + TESTED | Guardrail, injection, exfiltration, tool-authorization, cross-tenant, error and privacy tests; holdout adversarial evals (offline 12/12, GLM 12/12, critical 10/10) | Penetration test; live-model red teaming beyond 12 holdout scenarios |
| Secret hygiene | IMPLEMENTED + TESTED | `scripts/scan_secrets.py`: tracked files 0 findings, frontend bundle 0, backend image filesystem 0, eval results 0; settings `repr` and log redaction tests | Scanner not yet run in GitHub CI; no history rewrite scan tool (gitleaks) |
| Security headers | IMPLEMENTED + TESTED | CSP, nosniff, DENY, no-referrer, Permissions-Policy on every nginx location; checked automatically by `verify_stack.py`; HSTS snippet for the TLS edge; backend sends HSTS in production | TLS edge not built, so HSTS delivery is untested |
| Privacy (minimisation) | IMPLEMENTED + TESTED | Card/email/phone masking before model, storage and traces; false-positive tests; `pii_masked_total` ([PRIVACY.md](PRIVACY.md)) | Names/addresses not detected |
| Retention and deletion | IMPLEMENTED + TESTED | Conversation TTL (memory and Redis), DELETE → 404 thereafter, DB retention job under RLS | Retention job not scheduled; log retention not configured |
| Observability | IMPLEMENTED + TESTED | Access logs and traces carry request, trace, tenant, hotel and conversation ids; latency breakdown; metrics asserted to move (`tests/test_observability.py`) | Log shipping, dashboards, alerting, trace export not deployed |
| Performance evidence | IMPLEMENTED + TESTED | `perf/load_test.py` at 10/25/50/100 users, 0% errors; thread-pool bottleneck found and relieved ([PERFORMANCE.md](PERFORMANCE.md)) | Local benchmark only; no multi-worker, Redis-backed or real-model load test |
| Evaluation gates | IMPLEMENTED + TESTED | 34-scenario development suite, 12-scenario holdout, critical flag, `--fail-on-critical` (exit 4), `--baseline` (exit 3); offline 28/28 (critical 14/14) | Larger dataset from real traffic; LLM-judge grading |
| CI (standard) | IMPLEMENTED + NOT VERIFIED | `ci.yml`: backend, integration with Redis/PostgreSQL services, security scan, frontend with bundle scan, e2e, docker incl. 3-replica verification. Every step was run locally, not as a workflow | First run on GitHub |
| CI (live AI eval) | IMPLEMENTED + NOT VERIFIED | `live-ai-eval.yml`: manual, provider choice, protected secrets, sanitised inputs | First run; environment secrets |
| Docker images | IMPLEMENTED + TESTED | Non-root (10001 / 101), digest-pinned, health checks, no secrets, backend 71.2 MB compressed | Registry, signing, SBOM, vulnerability scan |
| Graceful shutdown | IMPLEMENTED + TESTED | SIGTERM → exit 0 in ~2.1 s, audit flush and client close, edge kept serving 12/12 | Readiness does not flip to draining on SIGTERM |
| Migrations | IMPLEMENTED + TESTED | Ordered, transactional, checksummed, advisory-locked; migration job in compose | Rollback strategy (forward-only by design) |
| Configuration and flags | IMPLEMENTED + TESTED | Startup validation incl. HTTPS, mock and lease rules; unknown flags fail startup ([CONFIGURATION.md](CONFIGURATION.md)) | Runtime flag changes; secret manager |
| Frontend resilience | IMPLEMENTED + TESTED | 19 Vitest tests incl. busy retry, 503, unexpected bodies, abort timeout, 413, long content; Playwright 6/6 (desktop + mobile) | Full screen-reader audit |
| Admin authentication | DESIGNED | `AuthProvider` boundary; default refuses with 401 `UNAUTHORIZED` | OIDC/JWT NOT IMPLEMENTED |
| Guest authentication | NOT IMPLEMENTED | Booking tools require a principal that nothing issues | Identity provider integration |
| Semantic retrieval | NOT IMPLEMENTED | Flag fails startup if enabled | Only if knowledge outgrows the prompt |
| WhatsApp / voice channels | DESIGNED | Render adapters with tests; flags reserved | Webhooks, telephony |
| Production deployment | DESIGNED | [DEPLOYMENT.md](DEPLOYMENT.md) proposed topology | Everything cloud-side |
| SLOs / on-call | DESIGNED | [SRE.md](SRE.md) proposals | Measured SLOs, alerting, on-call |

## Verification evidence (run 2026-09-16, final code)

| Check | Result |
|---|---|
| Backend pytest with Redis 7.4 + PostgreSQL 17 containers | **292 passed** |
| Backend pytest without services (CI `backend` job equivalent) | **270 passed, 22 skipped** (integration) |
| Lint / types | ruff clean; oxlint clean; `tsc -b` clean |
| Frontend Vitest | **19 passed** |
| Playwright E2E (desktop + mobile, AI disabled) | **6 passed** |
| Offline eval, development suite | **28/28** (6 AI-only skipped), critical 14/14, no regressions vs baseline |
| Offline eval, holdout suite | **12/12**, critical 10/10 |
| GLM 5.2 live, development suite (runtime evidence, not Claude) | **34/34**, critical 14/14, decision accuracy 18/18, groundedness 14/14, p50 3.7 s / p95 10.1 s, no regressions vs adapter run 2 (`evals/results/glm-5.2-final`) |
| GLM 5.2 live, holdout suite | **12/12**, critical 10/10 (`evals/results/glm-5.2-holdout-run1`) |
| Three-replica Docker stack (AI disabled) | `verify_stack.py --expect-shared-state` **33/33**; simultaneous booking in 3 containers → one booking id, created on one replica |
| Load test (local, not capacity) | 0% errors at 10–100 users in all scenarios; see [PERFORMANCE.md](PERFORMANCE.md) |
| Secret scans | 0 findings: tracked files, new files, frontend bundle, backend image filesystem |
| Anthropic live API | **NOT VERIFIED — no Anthropic credential** |
| GitHub Actions | **Not run** (workflows exist; no push was made) |

## Not claimed

- Production readiness or production capacity
- Live Anthropic Claude verification
- That CI passes on GitHub
- A real PMS/CRS integration
- High availability of Redis or PostgreSQL
- Compliance certification, a penetration test, achieved SLOs
