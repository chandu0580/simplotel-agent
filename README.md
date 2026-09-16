# Hotel Guest Assistant

A multi-tenant AI guest assistant for hotel websites. Guests ask about the property, rooms, amenities and policies, and check live availability, all in one conversation. Answers are grounded in each hotel's own knowledge base. Dates, capacity, inventory and prices are always computed by deterministic code, never by the model.

The project began as a take-home assignment for Simplotel and has since been evolved into an **enterprise architecture foundation**: a tenant-aware modular monolith with clear integration boundaries, guardrails, observability, evaluation and container packaging. It is **not a production deployment**. [docs/ASSIGNMENT_SCOPE.md](docs/ASSIGNMENT_SCOPE.md) separates what the assignment required from what was added later.

## Status

| | |
|---|---|
| **Implemented and tested** | Guest chat UI, v1 conversation API, multi-tenancy, knowledge lifecycle, deterministic availability, tool framework with authorization, guardrails, offline fallback, AI traces, metrics, structured logs, rate limiting, admin RBAC boundary, i18n, Docker |
| **Prototype** (in-memory, single process, or mock) | Conversation store, idempotency store, rate limiter, cache, reservation provider (mock inventory), bookings, dev-only static-token admin auth |
| **Designed / documented only** | OIDC authentication, semantic retrieval (RAG), real PMS/booking integration, WhatsApp and voice ingress, persistent database, dashboards and alerting |
| **Verification (2026-09-16)** | Backend **203** tests · Frontend **13** · E2E **6** (desktop + mobile) · Offline eval **28/28** · Docker images built and run healthy |
| **Live Anthropic API** | **Not verified.** No Anthropic credential was available. The Claude integration is tested with a fake client and with the real SDK against a mocked HTTP transport. AI-mode evals ran against a **GLM development provider** (33/34, 34/34, 32/34), which is **not** Claude verification. |

Details: [docs/ENTERPRISE_READINESS.md](docs/ENTERPRISE_READINESS.md).

## Features

- **Grounded Q&A.** Each answer cites knowledge-base entries. Uncited answers, fabricated prices, inventory claims and prompt or secret leakage are blocked before reaching the guest.
- **Availability.** The model decides when to search or ask for details; code validates dates and computes capacity, inventory and price. Results render as room cards.
- **Conversations.** History and booking context are kept on the server, so follow-ups like "what about 3 adults?" work. Conversations expire and guests can delete them.
- **Graceful degradation.**
  - If the model fails, a deterministic FAQ engine answers.
  - If reservations are down, the guest gets a safe reply, and availability endpoints return 503.
  - Admin requests without configured auth get an honest 401.
- **Multi-tenant.** Every request is scoped to a tenant and hotel. Two demo tenants (a Goa resort and a Bengaluru business hotel) prove isolation.
- **Guest UI.** English plus a draft Hindi translation, hotel branding, loading/error/retry/offline states, keyboard and screen-reader support.

## Architecture

```
Browser (React + Vite)
   │  /api/v1/hotels/{hotel_id}/...
   ▼
FastAPI  ── middleware: request/trace ids · security headers · rate limits · tenant resolution
   │
ConversationService ── server-side context, expiry, locking
   │
AssistantService ── input guardrails ─► AIAssistant (1 LLM call, 3 strict tools, output guardrails)
   │                                   └► OfflineAssistant (deterministic fallback)
   ├─► ToolRegistry ─► ReservationProvider (resilient wrapper → mock inventory)
   ├─► KnowledgeProvider + Retriever (per-hotel JSON, content lifecycle)
   └─► LLMProvider (Anthropic adapter) · ModelRouter
Cross-cutting: config & flags · structured logs · AI traces · Prometheus metrics · domain events
```

Overview: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Deep dive: [ENTERPRISE_ARCHITECTURE](docs/ENTERPRISE_ARCHITECTURE.md), [SYSTEM_DESIGN](docs/SYSTEM_DESIGN.md)

## Quick start

### Option A: Docker (one command)

```bash
docker compose up --build        # http://localhost:8080
```

Without an Anthropic key the assistant runs in offline FAQ mode. To enable AI, put `ANTHROPIC_API_KEY=...` in `backend/.env`; it is read at runtime and never baked into the image.

### Option B: Local development

Requirements: Python 3.11+ (developed on 3.13) and Node.js 20+ (developed on 22).

On Windows, clone into a short path (e.g. `C:\dev\simplotel-agent`) or [enable long paths](https://pip.pypa.io/warnings/enable-long-paths). Some Anthropic SDK file names are long enough that `pip install` fails in deeply nested folders.

```bash
# Backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # optional: add ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000     # API docs: http://localhost:8000/docs

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173 (proxies /api to :8000)
```

To point the UI at the second demo hotel, run `VITE_HOTEL_ID=hotel-blr-001 npm run dev`.

## Environment

All backend settings are documented in [backend/.env.example](backend/.env.example) and validated in `app/core/config.py`. The main ones:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables AI mode. Server-side only |
| `APP_ENV` | `development` | `production` rejects dev auth, localhost/wildcard CORS, text logs and disabled rate limits |
| `ANTHROPIC_MODEL` / `ANTHROPIC_EFFORT` | `claude-opus-5` / `low` | Model routing for guest turns |
| `AI_ENABLED` | `true` | Global kill switch (tenants can also disable AI via `ai_assistant_enabled`) |
| `AUTH_MODE` | `disabled` | Admin API auth; `static_token` is for development only |
| `RATE_LIMIT_*` | 60/min IP · 20/min conversation · 1200/min hotel | In-process limiter |
| `FEATURE_*` | see `app/core/flags.py` | Global feature flags; per-tenant overrides live in `app/data/tenants.json` |

Frontend: `VITE_HOTEL_ID` (default `hotel-goa-001`), `VITE_API_BASE_URL` (for builds served from another origin). There are no provider credentials in the frontend.

## API

The full contract is in OpenAPI at `/docs` (outside production) and in the committed snapshot [docs/openapi.json](docs/openapi.json).

### Guest API (v1)

The responses below were captured from the running backend in offline mode.

```bash
# Start a conversation
curl -s -X POST http://localhost:8000/api/v1/hotels/hotel-goa-001/conversations \
  -H "Content-Type: application/json" -d '{"locale": "en"}'
```
```json
{"conversation_id": "conv_37ef7f7dcfb54a88b102a355edc7f759", "hotel_id": "hotel-goa-001", "channel": "web", "locale": "en", "expires_at": "2026-10-06T12:00:00Z"}
```

```bash
# Send a message (history and booking context are kept on the server)
curl -s -X POST http://localhost:8000/api/v1/hotels/hotel-goa-001/conversations/$CID/messages \
  -H "Content-Type: application/json" -d '{"message": "What is the cancellation policy?"}'
```
```json
{
  "request_id": "851f69cec16b43e0",
  "conversation_id": "conv_37ef7f7dcfb54a88b102a355edc7f759",
  "mode": "offline",
  "reply": {
    "type": "answer",
    "text": "Standard (flexible) rates can be cancelled free of charge up to 48 hours before the check-in date. …",
    "sources": [{"id": "policies.cancellation", "title": "Cancellation policy"}],
    "suggestions": ["What time is check-in?", "Is breakfast included?", "Check room availability"],
    "availability": null, "booking_prefill": null, "form_error": null
  },
  "notice": "AI answers are turned off; answers are coming from our standard hotel FAQ.",
  "meta": {"trace_id": "8fe4f8701638408e897df99d44ac5f3f", "prompt_version": null, "tool_schema_version": null, "knowledge_version": "000669a8d553"}
}
```

In AI mode, `mode` is `"ai"` and `meta` also carries `prompt_version` (e.g. `guest-assistant@4+…`) and `tool_schema_version`. `reply.type` is one of `answer`, `clarification`, `fallback`, `availability` or `collect_booking_details`.

```bash
# Booking-form search (deterministic, no LLM), recorded in the conversation's context
curl -s -X POST http://localhost:8000/api/v1/hotels/hotel-goa-001/conversations/$CID/availability \
  -H "Content-Type: application/json" -d '{"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 3}'
```
```json
{
  "check_in": "2026-10-07", "check_out": "2026-10-09", "nights": 2, "adults": 3, "children": 0, "available": true,
  "rooms": [{"room_id": "deluxe-pool-view", "name": "Deluxe Pool View Room", "max_occupancy": 3, "breakfast_included": true,
             "rooms_left": 8, "nightly_rate": 7800, "total_price": 15600, "currency": "INR", "...": "..."}],
  "sold_out_room_names": [],
  "message": "2 room types available for 3 adults, 2 nights from Wed 07 Oct 2026 to Fri 09 Oct 2026.",
  "season_label": null
}
```

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/hotels/{hotel_id}` | Public profile: branding, languages, today's date at the hotel, form limits |
| `POST /api/v1/hotels/{hotel_id}/conversations` | Start a conversation |
| `GET` / `DELETE /api/v1/hotels/{hotel_id}/conversations/{id}` | View or delete (data minimisation) |
| `POST .../conversations/{id}/messages` | Guest turn |
| `POST .../conversations/{id}/availability` | Form search recorded in the conversation |
| `POST /api/v1/hotels/{hotel_id}/availability` | Stateless availability search |
| `GET /health` · `GET /ready` · `GET /metrics` | Liveness · readiness · Prometheus (internal only) |
| `GET /api/v1/admin/tenants/{tenant_id}/hotels[/{hotel_id}/knowledge \| /ai-config]` | Admin, read-only, role- and tenant-scoped |
| `/api/chat`, `/api/availability`, `/api/hotel`, `/api/health` | Original assignment endpoints for the default hotel; deprecated, still supported |

### Errors

```json
{"error": {"code": "INVALID_BOOKING_DETAILS", "message": "Check-out date must be after the check-in date.", "request_id": "1b312934a28b407a", "details": null}}
```

| Codes | Status |
|---|---|
| `VALIDATION_ERROR`, `INVALID_BOOKING_DETAILS` | 422 |
| `HOTEL_NOT_FOUND`, `CONVERSATION_NOT_FOUND` | 404 |
| `RATE_LIMITED` | 429, with `Retry-After` |
| `AVAILABILITY_UNAVAILABLE` | 503, with `Retry-After` |
| `AUTH_NOT_CONFIGURED`, `AUTHENTICATION_REQUIRED` | 401 |
| `FORBIDDEN` | 403 |
| `INTERNAL_ERROR` | 500; never includes stack traces |

Legacy endpoints keep their original lowercase codes.

## Testing

```bash
cd backend
python -m pytest                                  # 203 tests: unit, integration, contract, security
ruff check app tests evals scripts perf
python -m evals.run_evals --mode offline --baseline evals/results/offline.json   # eval + regression gate
python -m perf.benchmark                          # local overhead profile

cd ../frontend
npm test                                          # 13 component tests
npm run build                                     # type check + build
npx playwright install chromium && npm run test:e2e   # 6 E2E runs (real backend + frontend, desktop + mobile)
```

CI (`.github/workflows/ci.yml`) runs all of the above plus dependency audits and a Docker smoke test, with no secrets needed. Live AI evaluation is a separate manual workflow (`ai-eval.yml`) gated on an `ANTHROPIC_API_KEY` secret. **Neither workflow has been run on GitHub yet.**

## Evaluation

34 scenarios covering functional, grounding, tool-calling, conversation, safety, prompt-injection and multi-tenant cases. Checks are structured wherever possible: which decision the model made, tool arguments, cited evidence, guardrails triggered, and whether the model was called at all.

| Run | Result |
|---|---|
| Offline | 28/28 (6 AI-only skipped); groundedness 15/15 |
| GLM development provider (**not Claude**) | 33/34, 34/34, and 32/34 after review fixes (one plain-text-instead-of-tool fallback; one false-negative check since corrected); decision accuracy 18/18 in every run |
| Anthropic live | **Not executed**, no credential |

Details and history: [docs/EVALUATION.md](docs/EVALUATION.md).

## AI architecture in brief

- **One model call per turn.** The model must reply through exactly one strict tool: `answer_guest`, `check_availability` or `request_booking_details`. Tool results go straight to the UI, so the model never restates prices or inventory. The answer is a tool, not a JSON output format, because a real development model stopped calling tools when both were enabled.
- **Grounding.** The system prompt holds the hotel's published knowledge (about 2.8k tokens for the demo hotel). Every factual answer must cite entry ids, which code validates.
- **Versioned.** Prompt, tool-schema and knowledge versions are recorded in traces, API responses and eval results.
- **Model-agnostic core.** The `LLMProvider` interface isolates SDK details, and `ModelRouter` selects a model per task.
- **Why no vector database:** the content fits in the prompt, and retrieval would only add a way to miss evidence. The retrieval interface is ready for when content grows. See [docs/DECISIONS.md](docs/DECISIONS.md).

## Security

- **Credentials:** held only by the backend, read from the environment, and redacted in logs. None are in the frontend or container images (verified by scanning the exported image filesystems).
- **Guardrails:**
  - Input: exfiltration attempts blocked before the model; injection attempts flagged and counted; prompt tags neutralised.
  - Output: secret/prompt leakage, uncited answers, unsupported prices and inventory claims.
  - Tools: the model can only request exposed read-only tools.
- **Isolation:** tenant-scoped data access, with isolation tests.
- **Admin API:** refuses until real auth is configured; RBAC with tenant and hotel scoping.
- **HTTP hygiene:** rate limits, CORS allow-list, security headers (API and nginx), production configuration validation.
- **Containers:** non-root, read-only filesystem, capabilities dropped, digest-pinned base images.

Threats, residual risks and roadmap: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Observability

Structured JSON logs carry request, trace, tenant, hotel, conversation and channel context. There is an `AITrace` per turn (model, versions, evidence, tool calls, guardrails, tokens, latency, fallback reason), Prometheus metrics with low-cardinality labels, and domain events that never include message text. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) and [docs/SRE.md](docs/SRE.md).

## Multi-tenancy

`app/data/tenants.json` maps tenants to hotels. Each hotel has its own `hotel.json` (profile, brand, languages, rooms, knowledge with lifecycle) and `inventory.json`. Requests resolve `hotel_id` to a `TenantContext`. Conversations, bookings and caches are keyed by tenant and hotel, so a conversation from one hotel is a 404 through another. Feature flags can be overridden per tenant.

## Limitations

- **Unverified:** the live Anthropic API has not been called, and the CI workflows have never run on GitHub.
- **Mocked:** availability and bookings use a mock provider. There is no PMS integration or payment flow.
- **Single process:** conversations, rate limits, idempotency, cache and locks are in memory. Multiple replicas need Redis or Postgres first.
- **Authentication:** admin authentication is not production-grade (development static tokens only); guest chat is unauthenticated by design.
- **Offline mode is literal:** it matches keywords, answers in English only, and recognises ISO dates only.
- **Hindi UI strings are a draft** that needs native review.
- **Not wired up yet:** WhatsApp and voice have render adapters but no inbound channels; semantic retrieval isn't implemented.
- **Unmeasured:** nothing has been load tested or deployed, and every SLO in the docs is a proposal.

## Production roadmap

1. **Persistent stores:** Postgres for tenants, knowledge, bookings and audit; Redis for conversations, rate limits and idempotency.
2. **Authentication:** OIDC for admin and staff, and guest authentication for booking management.
3. **Real reservation integration:** a PMS or channel-manager adapter behind `ReservationProvider`.
4. **Live evaluation:** run the eval suite against Claude, and gate prompt and model changes on it.
5. **Observability stack:** OpenTelemetry export, dashboards and alerts; an LLM-judge groundedness check on sampled traffic.
6. **Edge protection:** WAF and bot protection, per-tenant LLM budgets, PII redaction before model calls.
7. **Knowledge authoring:** an admin publishing workflow for knowledge content.
8. **Channels:** WhatsApp and voice ingress.
9. **Semantic retrieval**, once content outgrows the prompt.

Migration path and scaling stages: [docs/ENTERPRISE_ARCHITECTURE.md](docs/ENTERPRISE_ARCHITECTURE.md).

## Documentation

| Doc | Contents |
|---|---|
| [ASSIGNMENT_SCOPE](docs/ASSIGNMENT_SCOPE.md) | What the assignment required vs what was added |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Codebase map and one guest turn |
| [DECISIONS](docs/DECISIONS.md) | Product, UX, AI and engineering decisions (incl. the assignment's questions) |
| [ENTERPRISE_ARCHITECTURE](docs/ENTERPRISE_ARCHITECTURE.md) | Target architecture, tenancy, data model, scalability, migration |
| [SYSTEM_DESIGN](docs/SYSTEM_DESIGN.md) | Request lifecycles and failure paths (sequence diagrams) |
| [THREAT_MODEL](docs/THREAT_MODEL.md) | Threats, mitigations, residual risk |
| [SRE](docs/SRE.md) · [OBSERVABILITY](docs/OBSERVABILITY.md) | Proposed SLOs, runbooks, DR, dashboards, metric definitions |
| [COST_MODEL](docs/COST_MODEL.md) | LLM cost drivers and levers |
| [ENTERPRISE_READINESS](docs/ENTERPRISE_READINESS.md) | Capability-by-capability status |
| [EVALUATION](docs/EVALUATION.md) | Test and eval results, current and historical |

## AI tools used

- **Claude Code** (Anthropic's coding agent, running Claude Opus 5): design, implementation, tests, reviews and documentation, including parallel sub-agents that drafted the design documents from the code. Every result quoted in this repository comes from commands that were actually run.
- **GLM (`glm-5.2`, via an Anthropic-compatible gateway):** a development provider for running the AI-mode eval suite through the app's real model code path. It is not the production model, and those runs are not Claude verification.
- **Claude API (`claude-opus-5`):** the runtime model the backend is built for. Not yet exercised live.
