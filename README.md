# Hotel Guest Assistant

A multi-tenant AI guest assistant for hotel websites. Guests ask about the property, rooms, amenities and policies, and check live availability, all in one conversation. Answers are grounded in each hotel's own knowledge base. Dates, capacity, inventory and prices are always computed by deterministic code, never by the model.

The project began as a take-home assignment for Simplotel and has since been evolved into an **enterprise architecture foundation**: a tenant-aware modular monolith with clear integration boundaries, guardrails, observability, evaluation and optional shared-state adapters (Redis, PostgreSQL) behind interfaces. It is **not a production deployment**. [docs/ASSIGNMENT_SCOPE.md](docs/ASSIGNMENT_SCOPE.md) separates what the assignment required from what was added later.

## Status

| | |
|---|---|
| **Implemented and tested** | Guest chat UI, v1 conversation API, multi-tenancy, knowledge lifecycle, deterministic availability, tool framework with authorization, guardrails, PII masking before the model, offline fallback, GLM and Anthropic provider adapters, AI traces, metrics, structured logs, rate limiting, idempotency and conversation locks (in-memory by default), admin RBAC boundary, i18n |
| **Optional adapters** (behind interfaces) | Redis state adapter (conversations, rate limits, idempotency, locks) and PostgreSQL adapter (migrations, row-level security, audit sink, retention). Integration tests in `backend/tests/integration` were verified locally once against Redis 7.4 and PostgreSQL 17; they skip unless `TEST_REDIS_URL` / `TEST_DATABASE_URL` are set and are not run in CI. |
| **Prototype** (single process, per-process or mock) | In-memory state backend (the default), per-process knowledge and availability cache, reservation provider (mock inventory), bookings, dev-only static-token admin auth |
| **Designed / documented only** | PostgreSQL repositories other than audit (conversations, messages, tool calls, bookings, knowledge, evaluations: schema only), OIDC authentication, semantic retrieval (RAG), real PMS/booking integration, WhatsApp and voice ingress, dashboards and alerting |
| **Docker/containerization** | NOT REQUIRED FOR CURRENT PROJECT — removed intentionally. The app runs locally with a Python virtual environment and the Vite dev server. |
| **Anthropic live API** | **NOT VERIFIED — no Anthropic credential.** The Anthropic adapter is tested with the real SDK against a mocked HTTP transport. |
| **GLM (default provider)** | Development suite 34/34 in three runs (two adapter runs and a final run on the final code) and holdout suite 12/12 with the GLM-native adapter. This is evidence for the GLM runtime only, **not** Claude verification. |
| **CI** | Workflows are defined; **neither has been run on GitHub**. |

Test totals and the full verification record: [docs/ENTERPRISE_READINESS.md](docs/ENTERPRISE_READINESS.md).

## Features

- **Grounded Q&A.** Each answer cites knowledge-base entries. Uncited answers, fabricated prices, inventory claims and prompt or secret leakage are blocked before reaching the guest.
- **Availability.** The model decides when to search or ask for details; code validates dates and computes capacity, inventory and price. Results render as room cards.
- **Conversations.** History and booking context are kept on the server, so follow-ups like "what about 3 adults?" work. Conversations expire and guests can delete them.
- **Graceful degradation.**
  - If the model fails or times out, a deterministic FAQ engine answers and the turn reports `meta.degradation`.
  - If reservations are down, the guest gets a safe reply, and availability endpoints return 503.
  - Admin requests without configured auth get an honest 401.
- **Multi-tenant.** Every request is scoped to a tenant and hotel. Two demo tenants (a Goa resort and a Bengaluru business hotel) prove isolation.
- **Guest UI.** English plus a draft Hindi translation, hotel branding, loading/error/retry/offline states, keyboard and screen-reader support.

## Architecture

```
Guest
   │
React web app (Vite)
   │  /api/v1/hotels/{hotel_id}/...
   ▼
FastAPI API ── middleware: request/trace ids · security headers · 64 KB body limit · rate limits
   │
TenantContext ── hotel_id → tenant, per-tenant flags and limits
   │
ConversationService ── server-side context, expiry, locks + compare-and-set versions
   │
AssistantService ── input guardrails + PII masking ─► AIAssistant (1 LLM call, 3 strict tools, output guardrails)
   │                                                └► OfflineAssistant (deterministic fallback)
   ├─► ToolRegistry ─► ReservationProvider (resilient wrapper → mock inventory)
   ├─► KnowledgeProvider + Retriever (per-hotel JSON, content lifecycle)
   └─► LLMProvider (GLM adapter, default · Anthropic adapter · mock for load tests) · ModelRouter
State (behind interfaces): in-memory by default · optional Redis adapter (conversations, rate limits, idempotency, locks) · optional PostgreSQL audit trail
Cross-cutting: config & flags · structured logs · AI traces · Prometheus metrics · domain events
```

Overview: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Deep dive: [ENTERPRISE_ARCHITECTURE](docs/ENTERPRISE_ARCHITECTURE.md), [SYSTEM_DESIGN](docs/SYSTEM_DESIGN.md) · Running and hosting notes: [DEPLOYMENT](docs/DEPLOYMENT.md)

## Quick start

Requirements: Python 3.11+ (developed on 3.13) and Node.js 22.12+ (developed on 22; the installed Vitest requires `^22.12.0 || ^24.0.0 || >=26.0.0`). No Docker is needed.

On Windows, clone into a short path (e.g. `C:\dev\simplotel-agent`) or [enable long paths](https://pip.pypa.io/warnings/enable-long-paths). Some Anthropic SDK file names are long enough that `pip install` fails in deeply nested folders.

**Backend**

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
# If activation is blocked by the execution policy, run this first (current window only):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# In cmd.exe activate with: .venv\Scripts\activate.bat
pip install -r requirements.txt    # for tests and linting: pip install -r requirements-dev.txt
Copy-Item .env.example .env        # optional: add LLM_API_KEY and LLM_BASE_URL
uvicorn app.main:app --reload --port 8000     # API docs: http://localhost:8000/docs
```

macOS/Linux:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt    # for tests and linting: pip install -r requirements-dev.txt
cp .env.example .env               # optional: add LLM_API_KEY and LLM_BASE_URL
uvicorn app.main:app --reload --port 8000     # API docs: http://localhost:8000/docs
```

**Frontend** (second terminal; the same commands work in PowerShell and bash)

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173 (Vite proxies /api to :8000)
```

Without `LLM_API_KEY` and `LLM_BASE_URL` the assistant runs in offline FAQ mode. To enable AI, put the provider settings (see [Environment](#environment)) in `backend/.env`.

To point the UI at the second demo hotel, in PowerShell:

```powershell
$env:VITE_HOTEL_ID="hotel-blr-001"; npm run dev
```

On macOS/Linux: `VITE_HOTEL_ID=hotel-blr-001 npm run dev`. In PowerShell the variable stays set for that window; clear it with `Remove-Item Env:VITE_HOTEL_ID`.

The default configuration keeps all state in memory in one process. The optional adapters are enabled with `STATE_BACKEND=redis` + `REDIS_URL` and `DATABASE_URL` (PostgreSQL, run migrations first); they require Redis and PostgreSQL services you provide. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Environment

All backend settings are listed in [backend/.env.example](backend/.env.example), validated in `app/core/config.py` and documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). The main ones:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` rejects dev auth, localhost/wildcard CORS, text logs, disabled rate limits, `LLM_PROVIDER=mock` and non-HTTPS model endpoints |
| `LLM_PROVIDER` | `glm` | `glm` (OpenAI-compatible Chat Completions, forced tool call) · `anthropic` (alternative adapter) · `mock` (fixed latency for load tests; rejected in production) · `none` |
| `LLM_API_KEY` / `LLM_BASE_URL` | — | GLM credential and endpoint. Server-side only. `LLM_BASE_URL` must be `https://` in production |
| `LLM_MODEL` / `LLM_MODEL_FAST` | `glm-5.2` / empty (= `LLM_MODEL`) | GLM model routing |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT` | —, —, `claude-opus-5`, `low` | Used only when `LLM_PROVIDER=anthropic`; `ANTHROPIC_BASE_URL` must be `https://` in production |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `20` / `1` | Model call budget |
| `AI_ENABLED` | `true` | Global kill switch (tenants can also disable AI via `ai_assistant_enabled`) |
| `STATE_BACKEND` | `memory` | `memory` (one process) or `redis` (several replicas: conversations, rate limits, idempotency, locks) |
| `REDIS_URL` / `REDIS_KEY_PREFIX` | — / `sa` | Required when `STATE_BACKEND=redis` |
| `DATABASE_URL` | — | Optional PostgreSQL audit trail; connect as a non-superuser role so row-level security applies |
| `WORKER_THREADS` | `150` | Request-handler threads; caps concurrent AI turns per process |
| `AUTH_MODE` | `disabled` | Admin API auth; `static_token` is for development only |
| `RATE_LIMIT_*` | IP burst 30 per 10 s · IP 60/min · tenant 3000/min · hotel 1200/min · conversation 20/min | Memory or Redis |
| `PII_MASK_CONTACT_DETAILS` | `true` | Mask emails and phone numbers before the model; card numbers are always masked ([PRIVACY](docs/PRIVACY.md)) |
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
  "meta": {"trace_id": "8fe4f8701638408e897df99d44ac5f3f", "degradation": null, "prompt_version": null, "tool_schema_version": null, "knowledge_version": "000669a8d553"}
}
```

In AI mode, `mode` is `"ai"` and `meta` also carries `prompt_version` (e.g. `guest-assistant@4+…`) and `tool_schema_version`. When a turn is answered in degraded mode, `meta.degradation` is `{code, message}` (for example `LLM_TIMEOUT`); otherwise it is `null`. `reply.type` is one of `answer`, `clarification`, `fallback`, `availability` or `collect_booking_details`.

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
| `GET /health` · `GET /ready` · `GET /metrics` | Liveness · readiness · Prometheus (`/metrics` is meant for internal scraping; the hosting edge must not expose it publicly) |
| `GET /api/v1/admin/tenants/{tenant_id}/hotels[/{hotel_id}/knowledge \| /ai-config]` | Admin, read-only, role- and tenant-scoped |
| `/api/chat`, `/api/availability`, `/api/hotel`, `/api/health` | Original assignment endpoints for the default hotel; deprecated, still supported |

### Errors

Every v1 error uses one envelope and never includes a stack trace:

```json
{"error": {"code": "INVALID_BOOKING_DETAILS", "message": "Check-out date must be after the check-in date.", "request_id": "1b312934a28b407a", "details": null}}
```

| Code | Status |
|---|---|
| `VALIDATION_ERROR`, `INVALID_BOOKING_DETAILS` | 422 |
| `PAYLOAD_TOO_LARGE` | 413 (body over 64 KB) |
| `METHOD_NOT_ALLOWED` | 405 |
| `NOT_FOUND`, `HOTEL_NOT_FOUND`, `CONVERSATION_NOT_FOUND` | 404 |
| `UNAUTHORIZED` | 401 (`details: [{"reason": "auth_not_configured"}]` when admin auth is disabled) |
| `FORBIDDEN` | 403 |
| `CONVERSATION_BUSY` | 409, `Retry-After: 2` |
| `RATE_LIMITED` | 429, with `Retry-After` and `details[].dimension` |
| `RESERVATION_UNAVAILABLE`, `KNOWLEDGE_UNAVAILABLE` | 503, `Retry-After: 30` |
| `STATE_UNAVAILABLE` | 503, `Retry-After: 5` (Redis unreachable) |
| `INTERNAL_ERROR` | 500 |
| `LLM_TIMEOUT`, `LLM_UNAVAILABLE`, `TOOL_TIMEOUT`, `TOOL_UNAVAILABLE` | Not HTTP errors: reported in `meta.degradation` of a 200 turn |
| `FEATURE_DISABLED`, `NOT_SUPPORTED`, `IDEMPOTENCY_CONFLICT` | Tool and reservation outcome codes |

The list is defined in `backend/app/core/errors.py`. Legacy endpoints keep their original lowercase codes.

## Testing

Run with the backend virtual environment activated. The block is written for Windows PowerShell; apart from the `$env:` lines the commands are the same in bash (macOS/Linux variant below).

```powershell
cd backend
python -m pytest                                  # unit, contract, security and API tests
ruff check app tests evals scripts perf

# Optional-adapter integration tests against Redis and PostgreSQL you run yourself
# (skipped when the variables are unset; not run in CI)
$env:TEST_REDIS_URL="redis://127.0.0.1:6379/15"
$env:TEST_DATABASE_URL="postgresql://<superuser>:<password>@127.0.0.1:5432/postgres"
python -m pytest tests/integration -rs

# Evaluation (offline needs no model)
python -m evals.run_evals --mode offline --baseline evals/results/offline.json   # regression gate (exit 3)
python -m evals.run_evals --suite holdout --fail-on-critical                     # holdout suite; exit 4 on a critical failure
python -m evals.run_evals --mode ai --label <label> --provider-note "..."        # live model from LLM_PROVIDER; costs tokens

# Performance (local benchmark, not production capacity)
python -m perf.benchmark                          # in-process overhead profile
python -m perf.load_test                          # real HTTP load test; only ever uses LLM_PROVIDER=mock

# Secret scan (reports file and pattern names only)
python -m scripts.scan_secrets --git              # tracked files; also: PATH... (e.g. the frontend dist/)

cd ../frontend
npm test                                          # component tests
npm run build                                     # type check + build
npx playwright install chromium                   # once
npm run test:e2e                                  # E2E (real backend + frontend, desktop + mobile, AI disabled)
```

macOS/Linux variant of the integration-test command:

```bash
TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
TEST_DATABASE_URL=postgresql://<superuser>:<password>@127.0.0.1:5432/postgres \
python -m pytest tests/integration -rs
```

Totals from the latest run: [docs/ENTERPRISE_READINESS.md](docs/ENTERPRISE_READINESS.md). Load-test method and results: [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

### CI

- **`.github/workflows/ci.yml`** needs no LLM secret and no Docker. Four jobs: backend (ruff, pytest, offline eval gate, pip-audit); security (secret scan of tracked files, no committed `.env`); frontend (oxlint, type check and build, Vitest, bundle secret scan, npm audit); e2e (Playwright against the real backend and frontend, AI disabled). The optional Redis/PostgreSQL integration tests skip in CI.
- **`.github/workflows/live-ai-eval.yml`** is manual (`workflow_dispatch`): provider `glm` or `anthropic`, secrets from the protected `ai-evaluation` environment, inputs sanitised, optional baseline gate, results uploaded as artifacts.

**Neither workflow has been run on GitHub.**

## Evaluation

Two suites. The **development** suite (34 scenarios: functional, grounding, tool-calling, conversation, safety, prompt-injection and multi-tenant) was used while writing prompts and guardrails. The **holdout** suite (12 adversarial scenarios, 10 critical) was written afterwards and is never used for tuning. Checks are structured wherever possible: the decision the model made, tool arguments, cited evidence, guardrails triggered, and whether the model was called at all.

| Run | Result |
|---|---|
| Offline, development suite | 28/28 (6 AI-only skipped); critical 14/14; no regressions vs baseline |
| Offline, holdout suite | 12/12; critical 10/10 |
| GLM `glm-5.2`, GLM-native adapter, development suite (**GLM runtime only, not Claude**) | 34/34 in three runs (two adapter runs, then a final run on the final code: critical 14/14, p50 3718 ms, p95 10062 ms); decision accuracy 18/18 in all three |
| GLM `glm-5.2`, GLM-native adapter, holdout suite (**GLM runtime only, not Claude**) | 12/12; critical 10/10; served by AI 12/12 |
| Anthropic live | **NOT VERIFIED — no Anthropic credential** |

Details, root-cause analysis and history: [docs/EVALUATION.md](docs/EVALUATION.md).

## AI architecture in brief

- **One model call per turn.** The model must reply through exactly one strict tool: `answer_guest`, `check_availability` or `request_booking_details`. Tool results go straight to the UI, so the model never restates prices or inventory. The answer is a tool, not a JSON output format, because a real development model stopped calling tools when both were enabled.
- **Forced tool call on the default provider.** The GLM adapter uses OpenAI-compatible Chat Completions with `tool_choice="required"` and parallel tool calls disabled. Over the earlier Anthropic-format path GLM occasionally answered in plain text; forcing the tool call removed that failure mode.
- **Grounding.** The system prompt holds the hotel's published knowledge (about 2.8k tokens for the demo hotel). Every factual answer must cite entry ids, which code validates.
- **Versioned.** Prompt, tool-schema and knowledge versions are recorded in traces, API responses and eval results.
- **Model-agnostic core.** The `LLMProvider` interface isolates protocol details; provider-neutral contract tests run against the GLM, Anthropic and scripted providers, including a failure contract (timeout, 5xx, plain text instead of a tool call all degrade to a grounded offline answer).
- **Why no vector database:** the content fits in the prompt, and retrieval would only add a way to miss evidence. The retrieval interface is ready for when content grows. See [docs/DECISIONS.md](docs/DECISIONS.md).

## Security

- **Credentials:** held only by the backend, read from the environment, and redacted in logs. The secret scanner found none in tracked files or the frontend bundle.
- **Guardrails:**
  - Input: exfiltration attempts blocked before the model; injection attempts flagged and counted; prompt tags neutralised.
  - Output: secret/prompt leakage, uncited answers, unsupported prices and inventory claims.
  - Tools: the model can only request exposed read-only tools.
- **Privacy:** card numbers (Luhn-valid) are always masked, and emails and phone numbers by default, before text reaches the model, the stored transcript or traces. Names and addresses are not detected. See [docs/PRIVACY.md](docs/PRIVACY.md).
- **Isolation:** tenant-scoped data access with isolation tests; optional PostgreSQL adapter with row-level security forced on every table and composite tenant keys (verified locally once against PostgreSQL 17; not run in CI).
- **Admin API:** refuses until real auth is configured; RBAC with tenant and hotel scoping.
- **HTTP hygiene:** rate limits, CORS allow-list, 64 KB body limit, production configuration validation, and security headers set by the backend middleware (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`, HSTS in production).
- **Hosting requirement (not implemented in this repo):** whatever serves the built SPA in a real deployment must set `Content-Security-Policy`, `Permissions-Policy` and, at TLS termination, `Strict-Transport-Security`.

Threats, residual risks and roadmap: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Observability

Structured JSON logs carry request, trace, tenant, hotel, conversation and channel context. There is an `AITrace` per turn (model, versions, evidence, tool calls, guardrails, tokens, LLM/tool/app latency, PII masked, fallback reason), Prometheus metrics with low-cardinality labels, and domain events that never include message text. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) and [docs/SRE.md](docs/SRE.md).

## Multi-tenancy

`app/data/tenants.json` maps tenants to hotels. Each hotel has its own `hotel.json` (profile, brand, languages, rooms, knowledge with lifecycle) and `inventory.json`. Requests resolve `hotel_id` to a `TenantContext`. Conversations, bookings and caches are keyed by tenant and hotel, so a conversation from one hotel is a 404 through another, on any replica. Feature flags can be overridden per tenant.

## Limitations

- **Unverified:** the live Anthropic API has not been called, and the CI workflows have never run on GitHub. GLM results do not verify Claude.
- **Mocked:** availability and bookings use a mock provider. There is no PMS integration or payment flow ([RESERVATION_INTEGRATION](docs/RESERVATION_INTEGRATION.md)).
- **Partly persistent:** with the optional `STATE_BACKEND=redis` adapter, conversations, rate limits, idempotency and locks are shared across processes. PostgreSQL stores the audit trail plus the tenant and hotel rows synced from the tenant registry at startup; the other tables exist as schema only. Knowledge and availability caches stay per process by design.
- **Authentication:** admin authentication is not production-grade (development static tokens only); guest chat is unauthenticated by design.
- **Offline mode is literal:** it matches keywords, answers in English only, and recognises ISO dates only.
- **Hindi UI strings are a draft** that needs native review.
- **Not wired up yet:** WhatsApp and voice have render adapters but no inbound channels; semantic retrieval isn't implemented.
- **Unmeasured in production:** load tests are a local benchmark on one machine, not production capacity; nothing has been deployed, and every SLO in the docs is a proposal.

## Production roadmap

1. **Remaining persistent stores:** PostgreSQL repositories for tenants, knowledge, bookings, conversations and messages (schema exists).
2. **Authentication:** OIDC for admin and staff, and guest authentication for booking management.
3. **Real reservation integration:** a PMS or channel-manager adapter behind `ReservationProvider`.
4. **Live evaluation:** run the eval suites against Claude when a credential is available, and gate prompt and model changes on them.
5. **Observability stack:** OpenTelemetry export, dashboards and alerts; an LLM-judge groundedness check on sampled traffic.
6. **Edge protection:** WAF and bot protection, per-tenant LLM budgets.
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
| [CONFIGURATION](docs/CONFIGURATION.md) | Every setting, defaults and production validation |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | Running locally, optional Redis/PostgreSQL adapters, migrations, hosting requirements |
| [PERFORMANCE](docs/PERFORMANCE.md) | Local load-test method and results |
| [PRIVACY](docs/PRIVACY.md) | PII masking, data minimisation, retention and deletion |
| [RESERVATION_INTEGRATION](docs/RESERVATION_INTEGRATION.md) | Reservation boundary, resilience, idempotency, PMS integration path |
| [THREAT_MODEL](docs/THREAT_MODEL.md) | Threats, mitigations, residual risk |
| [SRE](docs/SRE.md) · [OBSERVABILITY](docs/OBSERVABILITY.md) | Proposed SLOs, runbooks, DR, dashboards, metric definitions |
| [COST_MODEL](docs/COST_MODEL.md) | LLM cost drivers and levers |
| [ENTERPRISE_READINESS](docs/ENTERPRISE_READINESS.md) | Capability-by-capability status and verification record |
| [EVALUATION](docs/EVALUATION.md) | Test and eval results, current and historical |

## AI tools used

- **Claude Code** (Anthropic's coding agent, running Claude Opus 5): design, implementation, tests, reviews and documentation, including parallel sub-agents that drafted documents from the code. Every result quoted in this repository comes from commands that were actually run.
- **GLM (`glm-5.2`):** the default runtime provider, called through the GLM-native adapter (OpenAI-compatible protocol). Earlier development runs used an Anthropic-compatible gateway. GLM results are GLM-runtime evidence only, not Claude verification.
- **Claude API (`claude-opus-5`):** supported through the alternative Anthropic adapter (`LLM_PROVIDER=anthropic`). Not exercised live.
