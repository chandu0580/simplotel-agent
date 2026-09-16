# Enterprise readiness matrix

**State: enterprise architecture foundation. Not a production deployment.**

Nothing here has run in production, handled real guest traffic, been load tested, or been verified against the live Anthropic API. There is no Anthropic credential, so all model-behaviour evidence comes from offline tests, scripted providers, and a GLM development provider that is not Claude.

## Status legend

| Status | Meaning |
|---|---|
| **IMPLEMENTED** | Working code on the request path, covered by automated tests |
| **IMPLEMENTED (prototype)** | Working and tested, but single-process, in-memory or mock. Needs a production backing service |
| **DESIGNED** | The interface or boundary exists in code; there is no production implementation |
| **DOCUMENTED** | Design written down; no code |

## Capabilities

| Capability | Status | Evidence | What production still needs |
|---|---|---|---|
| Multi-tenancy boundary | IMPLEMENTED | `app/tenancy.py`, tenant-scoped repositories, 2 demo tenants; `tests/test_tenancy.py` (cross-hotel 404s, per-hotel knowledge and inventory, per-tenant flags) | Persistent tenant registry; row-level security or schema isolation in the database |
| Hotel configuration | IMPLEMENTED | `HotelProfile` (brand, languages, time zone, contact, check-in/out), rooms, per-hotel JSON; `/api/v1/hotels/{id}` | Admin write API with approval and audit |
| Knowledge abstraction | IMPLEMENTED | `KnowledgeProvider` + `JsonKnowledgeProvider`; contract test | Database/CMS-backed provider |
| Knowledge lifecycle | IMPLEMENTED | draft/published/archived, `version`, `effective_from/until`, `updated_by`; drafts and expired entries never served or citable; `knowledge_version`; tests in `test_knowledge.py` | Publishing workflow, approvals, history |
| RAG abstraction | DESIGNED | `Retriever`, `Evidence`, `RetrievalResult`; full-context and keyword retrievers implemented; enabling semantic retrieval fails startup instead of silently degrading | Embeddings, vector index, hybrid ranking; only when content outgrows the prompt |
| LLM provider abstraction | IMPLEMENTED | `LLMProvider`; Anthropic adapter (tested against the real SDK with a mocked HTTP transport) and a scripted provider; contract tests | Live Anthropic verification; OpenAI/Gemini adapters if needed (not built) |
| Model routing | IMPLEMENTED (prototype) | `ModelRouter` with per-task routes from config; only `GUEST_TURN` has a caller | Routed tasks (classification, summarisation) validated by evals |
| Tool framework | IMPLEMENTED | `ToolRegistry`: lookup, exposure, flags, argument validation, timeout, audit log, metrics, events; `test_tools_and_resilience.py` | — |
| Tool authorization | IMPLEMENTED | READ_ONLY vs MUTATING policies, roles, tenant/hotel scope, guest confirmation, idempotency key; model can't call unexposed tools (tested with a compliant, "attacking" model) | Real guest authentication to supply principals |
| Idempotency | IMPLEMENTED (prototype) | `InMemoryIdempotencyStore`: same key + same request returns the same booking; different request conflicts; 8 concurrent duplicates create 1 booking | Unique constraint in the booking database, in the same transaction |
| Reservation provider | IMPLEMENTED (prototype) | `ReservationProvider`, mock provider, resilience wrapper (timeout, read retries, circuit breaker ignoring business errors, TTL cache, no mutation retries) | Real PMS/CRS/channel-manager adapter |
| Conversation service | IMPLEMENTED (prototype) | Server-side context, window, cap, sliding TTL, deletion, per-conversation lock; `test_conversations_v1.py` | Shared store (Redis/Postgres) with optimistic concurrency for multiple replicas |
| Guardrails | IMPLEMENTED | Input: exfiltration block, injection flags, prompt-tag neutralisation (current and replayed history). Output: secret/prompt leakage, citation validation, unsupported prices, inventory claims, suggestion and form-message checks | Semantic groundedness checking (LLM judge on sampled traffic); PII redaction before model calls |
| Prompt injection tests | IMPLEMENTED | `tests/test_guardrails.py` (model scripted to comply); eval scenarios `injection-*` (offline and GLM development provider) | Live-model red-teaming |
| Observability (logs) | IMPLEMENTED | Structured JSON logs, request/trace/tenant/hotel/conversation context, secret redaction; tested | Log shipping, retention |
| AI tracing | IMPLEMENTED | `AITrace` per turn: provider, model, prompt/tool/knowledge versions, evidence, citations, tool calls, guardrails, tokens (including cache reads/writes), latency, fallback reason; pluggable `TraceSink` | OpenTelemetry/LangSmith exporter |
| Metrics | IMPLEMENTED | Prometheus `/metrics`, low-cardinality labels; tested | Prometheus/Grafana deployment, alerting |
| Product metrics | DOCUMENTED | Definitions and proxies in `OBSERVABILITY.md` | Analytics pipeline; booking-engine events for conversion |
| Domain events | IMPLEMENTED (prototype) | Typed events without message text; logged plus in-memory publisher | Outbox table plus broker once consumers exist |
| Rate limiting | IMPLEMENTED (prototype) | Per IP (before hotel resolution), hotel and conversation; admin endpoints; 429 with Retry-After; tested | Shared limiter (Redis) or gateway/WAF; tenant and API-key dimensions |
| Caching | IMPLEMENTED (prototype) | `Cache` interface, TTL cache for knowledge and short-TTL availability; TTL 0 disables | Shared cache for multiple replicas |
| API versioning | IMPLEMENTED | `/api/v1`; legacy endpoints kept with Deprecation/Link headers; OpenAPI snapshot contract test (`docs/openapi.json`) | — |
| Error model | IMPLEMENTED | Stable UPPER_SNAKE codes with request id; no stack traces; legacy format preserved | — |
| Authentication boundary | DESIGNED | `AuthProvider`; default provider refuses (401 `AUTH_NOT_CONFIGURED`); development static tokens rejected in production config | OIDC/JWT validation at gateway or in `AuthProvider` |
| RBAC boundary | IMPLEMENTED | Roles guest → platform_admin with hierarchy, tenant and hotel scoping; admin API tests | Real identity provider supplying roles |
| Admin / hotel operations API | IMPLEMENTED (prototype) | Read-only: list hotels, knowledge with lifecycle, AI config and versions | Write operations with audit and approvals |
| AI evaluation framework | IMPLEMENTED | 34 scenarios with tags; structured checks (decision, tool arguments, guardrails, no-model-call, sources, availability fields); quality metrics; baseline regression gate in CI | Larger dataset from real (anonymised) traffic; LLM-judge grading; live Claude runs |
| Prompt/model versioning | IMPLEMENTED | `prompt_version`, `tool_schema_version`, `knowledge_version`, model in traces, API `meta`, admin config, eval results | Prompt registry with staged rollout |
| Feature flags | IMPLEMENTED | Safe defaults, env and per-tenant overrides, unknown flags fail fast | Runtime updates without restart |
| Configuration | IMPLEMENTED | Environment-aware settings with production validation; `.env.example` parse-tested | Secret manager integration |
| CI/CD | DESIGNED | `.github/workflows/ci.yml` (lint, tests, eval gate, dependency audit, E2E, docker smoke), `ai-eval.yml` (manual, secret-gated). **Not yet run on GitHub** | First run on GitHub; deployment pipeline; image signing |
| Docker | IMPLEMENTED | Multi-stage, digest-pinned, non-root, health checks; compose with read-only FS and dropped capabilities. Built and run locally: both healthy, no secrets or `.env` inside images, security headers verified | Registry, image scanning, SBOM |
| Health / readiness | IMPLEMENTED | `/health` (event loop), `/ready` (knowledge required; reservations degraded ≠ not ready); tested | Orchestrator probes |
| Accessibility | IMPLEMENTED | Live region with `aria-busy`, alerts, labels, focus into new forms, `lang` attribute, keyboard-operable controls, reduced motion; frontend tests | Full screen-reader and contrast audit |
| Internationalisation | IMPLEMENTED (prototype) | UI catalogues (English complete, Hindi draft), locale sent to backend, AI told the reply language; tested | Native review of Hindi; localised offline answers and backend strings |
| Voice readiness | DESIGNED | Channel-independent core; voice render adapter (speakable text, capped options); tested | Telephony, STT/TTS, latency budget, barge-in |
| WhatsApp readiness | DESIGNED | WhatsApp render adapter (text, button limits); tested | Webhook with signature verification, session window, templates |
| Threat model | DOCUMENTED | [THREAT_MODEL.md](THREAT_MODEL.md) | Penetration test |
| SRE design | DOCUMENTED | [SRE.md](SRE.md): proposed SLOs, degradation matrix, runbooks | On-call, alerting, measured SLOs |
| Observability design | DOCUMENTED | [OBSERVABILITY.md](OBSERVABILITY.md): dashboards and proposed alerts | Deployed dashboards |
| Cost model | DOCUMENTED | [COST_MODEL.md](COST_MODEL.md): illustrative arithmetic at list prices | Real usage data |
| Scalability design | DOCUMENTED | [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md): 1 → 10,000+ hotels path | Load testing |
| Disaster recovery | DOCUMENTED | [SRE.md](SRE.md): proposed RPO/RTO, provider outage behaviour | Backups, restore drills |

## Verification evidence (run 2026-09-16)

| Check | Result |
|---|---|
| Backend pytest | **203 passed** |
| Frontend Vitest | **13 passed** |
| Playwright E2E (desktop + Pixel 7, AI disabled) | **6 passed** |
| Lint and type checks | ruff, oxlint and tsc clean |
| Offline eval | **28/28** (34 scenarios, 6 AI-only skipped); groundedness 15/15; baseline gate: no regressions |
| GLM development-provider eval (not Claude) | 33/34 and 34/34 on this architecture; final run after review fixes 32/34 (1 plain-text-instead-of-tool fallback; 1 false-negative check, since corrected). Decision accuracy 18/18 in every run |
| Local performance (in-process, no network or LLM) | HTTP conversation turn p50 8.6 ms / p95 11.0 ms; AI turn app overhead p50 2.1 ms (`backend/perf/results.md`) |
| Docker | Both images built and healthy; no secrets or `.env` in image filesystems; headers, non-root user, read-only FS, unexposed `/metrics` verified manually |
| Anthropic live API | **Not verified** (no credential) |

## Not claimed

- Production readiness
- Proven high availability
- Support for 10,000 hotels
- Compliance certification
- A real booking integration
- Live Anthropic verification
- Achieved SLOs
