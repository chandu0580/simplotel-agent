# Assignment scope vs later additions

This repository started as a take-home assignment: build a full-stack AI hotel guest assistant in roughly 6–8 hours. It was later evolved into an enterprise architecture foundation. This page shows what was required and what came afterwards, so a reviewer can evaluate each on its own terms.

## 1. What the assignment required

| Requirement (from the brief) | Where it lives today |
|---|---|
| Guest-facing chat UI (React preferred) with clear messages, a loading state, error handling and follow-ups | `frontend/src/components/*`, `frontend/src/hooks/useChat.ts` |
| A usable way to collect check-in, check-out and guest count | `frontend/src/components/AvailabilityForm.tsx` |
| Clear availability results, responsive on desktop and mobile | `frontend/src/components/AvailabilityResults.tsx`, `index.css`; E2E runs on desktop and Pixel 7 |
| Browser calls the backend, never the LLM; no keys in the frontend | `frontend/src/api/client.ts`; provider code only in `backend/app/llm/anthropic_provider.py` |
| API accepting guest questions and conversation context | `POST /api/chat` (original, still supported) and `POST /api/v1/hotels/{hotel_id}/conversations/{id}/messages` |
| Answers from a hotel knowledge base (JSON or database) | `backend/app/data/hotels/hotel-goa-001/hotel.json`, `app/knowledge/` |
| Detect availability requests and call a mock `checkAvailability` tool | `check_availability` tool (`app/tools/builtin.py`) → `app/reservations/` |
| Use an LLM where appropriate; keep deterministic logic outside it | `app/assistant/agent.py`; dates, capacity, inventory and pricing in `app/reservations/availability.py` |
| Clear fallback when an answer can't be determined | Output guardrails and fallback replies; offline engine `app/assistant/offline.py` |
| Structured responses, validation, error handling, logging | `app/schemas.py`, `app/api/errors.py`, `app/core/observability.py` |
| Meaningful automated tests | `backend/tests/`, `frontend/src/App.test.tsx`, `frontend/e2e/` |
| 8–10+ evaluation scenarios, including frontend loading/error and an E2E flow | `backend/evals/scenarios.json` (34 scenarios), `docs/EVALUATION.md` |
| README, architecture, curl examples, decisions note, AI tools used | `README.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` |

The assignment-era answers to the brief's product questions (customer problem, guest journey, UX rationale, AI vs deterministic responsibilities, hallucination prevention, failure handling, usefulness metrics, production improvements) are in [DECISIONS.md](DECISIONS.md) and still apply.

**Assignment-era state** (git history up to commit `b251802`):
- Tests: 87 backend, 9 frontend, 6 E2E.
- Offline eval: 21/21.
- Development-provider (GLM, not Claude) AI eval: 26/27 and 27/27.
- Live Anthropic API not verified.

## 2. What was added afterwards (enterprise evolution)

None of this was required by the assignment. It is a foundation, not a production deployment.

| Area | Added |
|---|---|
| Architecture | Modular monolith with interfaces and a composition root (`app/container.py`); the old single-module layout was split into `core/`, `tenancy`, `knowledge/`, `llm/`, `tools/`, `assistant/`, `conversations/`, `reservations/`, `auth/`, `channels/`, `api/` |
| Multi-tenancy | Tenant registry, `TenantContext`, a second demo tenant and hotel, tenant-scoped repositories, per-tenant feature flags, hotel-local time zones, isolation tests |
| Knowledge | `KnowledgeProvider`, content lifecycle (draft/published/archived, versions, effective dates), `Retriever`/`Evidence`, knowledge versioning |
| AI | `LLMProvider` abstraction (Anthropic adapter plus scripted provider), `ModelRouter`, prompt and tool-schema versioning, AI traces |
| Tools | `ToolRegistry` with read-only/mutating policies, exposure control, roles, guest confirmation, idempotency keys, timeouts, audit; a mock `create_booking` for the mutation path |
| Reservations | `ReservationProvider` boundary; resilience wrapper (timeout, read retries, circuit breaker, short-TTL cache); idempotency store |
| Conversations | Server-side conversation service: context window, message cap, sliding expiry, deletion, concurrency lock |
| Guardrails | Input (exfiltration block, injection signals, prompt-tag neutralisation) and output (leaks, citations, fabricated prices, inventory claims) |
| API | `/api/v1` hotel-scoped guest API, admin API behind an authorization boundary, stable error codes, `/health`, `/ready`, `/metrics`, OpenAPI snapshot contract test; legacy endpoints kept with deprecation headers |
| Platform | Environment-aware config validation, feature flags, structured JSON logs with redaction, Prometheus metrics, domain events, rate limiting, caching |
| Frontend | v1 conversations, i18n (English plus a draft Hindi), hotel branding, connection status, accessibility improvements |
| Delivery | Dockerfiles (non-root, pinned digests, health checks), docker-compose, GitHub Actions CI plus a manual live-AI eval workflow (neither has been run on GitHub yet) |
| Evaluation | Structured assertions (decision, tool arguments, guardrails, no-model-call), tags, quality metrics, baseline regression gate, prompt-injection and multi-tenant scenarios |
| Documentation | ENTERPRISE_ARCHITECTURE, SYSTEM_DESIGN, THREAT_MODEL, SRE, OBSERVABILITY, COST_MODEL, ENTERPRISE_READINESS, this page |

## 3. Backward compatibility

- **Original endpoints still work:** `/api/chat`, `/api/availability`, `/api/hotel` and `/api/health` behave as before for the default hotel. They now carry `Deprecation` and `Link: successor-version` headers.
- **Original tests carried over:** every assignment-era backend test still passes. Only fixtures and import paths changed with the new module layout.
- **Two frontend tests were replaced by design:** their behaviour moved to the server, which no longer takes client-sent history in v1. Server-side context is covered by backend tests.

## 4. Still not done, in either phase

- The live Anthropic API has not been called; there was no credential. The development-provider runs used GLM and are not Claude verification.
- There is no real PMS or booking integration, no persistent database, and no production authentication.
- Nothing has been deployed, load tested or measured against SLOs.
