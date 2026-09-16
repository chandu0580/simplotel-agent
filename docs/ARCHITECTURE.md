# Architecture overview

A short map of the codebase as it is today. For depth, see:

| Document | Covers |
|---|---|
| [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md) | Target architecture, boundaries, multi-tenancy, data model, scalability, migration path |
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | Request lifecycles, sequence diagrams, failure paths, shared state |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Threats, mitigations, residual risk |
| [SRE.md](SRE.md) / [OBSERVABILITY.md](OBSERVABILITY.md) | Reliability, proposed SLOs, runbooks, telemetry, dashboards |
| [COST_MODEL.md](COST_MODEL.md) | LLM cost drivers and levers |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Images, compose stacks (single instance and 3 replicas), migrations, verification |
| [CONFIGURATION.md](CONFIGURATION.md) | Environment variables, defaults and production validation rules |
| [PERFORMANCE.md](PERFORMANCE.md) | Local load-test results and what they do and don't show |
| [PRIVACY.md](PRIVACY.md) | Personal-data masking, events, deletion and retention |
| [RESERVATION_INTEGRATION.md](RESERVATION_INTEGRATION.md) | The reservation boundary and how a real PMS would plug in |
| [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md) | Implemented vs designed, capability by capability |
| [DECISIONS.md](DECISIONS.md) | Product, engineering and hardening decisions with their reasons |

## Shape: a modular monolith

One FastAPI process, split into packages with one-way dependencies. Every boundary is an interface, and the implementation for each is chosen in one file, [`app/container.py`](../backend/app/container.py). The same image runs as one instance with in-memory state, or as several replicas sharing Redis ([DEPLOYMENT.md](DEPLOYMENT.md)).

```
Browser (React)
   │  /api/v1/hotels/{hotel_id}/...          (legacy /api/* kept, deprecated)
   ▼
api/            middleware (request id, W3C trace id, 64 KB body limit, security headers, access log, latency metric)
                deps (tenant resolution, rate limits, admin authorization) · error model · routes (sync; only /health is async)
   │
   ▼
conversations/  ConversationService: server-side conversations, context window, lease locks, compare-and-set saves, expiry
   │
   ▼
assistant/      AssistantService (one guest turn, any channel)
                 ├─ PII masking       (core/privacy: cards, emails, phone numbers, before the model)
                 ├─ InputGuardrails   (exfiltration block, injection flags, prompt-tag neutralisation)
                 ├─ AIAssistant       (one LLM call, three strict tools, OutputGuardrails)
                 └─ OfflineAssistant  (deterministic fallback)
   │
   ├──► tools/          ToolRegistry: validation, exposure, flags, authorization, timeout, audit
   │       └──► reservations/   ReservationProvider → Resilient wrapper (cache, breaker around deadline-bounded retries, timeout) → Mock
   ├──► knowledge/      KnowledgeProvider (JSON, content lifecycle) · Retriever → Evidence
   └──► llm/            LLMProvider (GLM adapter: default · Anthropic adapter · latency mock · scripted test provider) · ModelRouter

core/     config & validation · feature flags · errors · structured logging & redaction · metrics · privacy
          AI traces · domain events · cache · rate limiter · locks · resilience · clock · versioning
state/    Redis implementations: conversations, rate limiter, idempotency, locks (STATE_BACKEND=redis)
db/       PostgreSQL audit sink · migration runner · retention job     migrations/  SQL schema with row-level security
tenancy   TenantRegistry · TenantContext           auth/      Principal, roles, auth providers
channels/ web · WhatsApp · voice render adapters   data/      tenants.json, hotels/<hotel_id>/{hotel,inventory}.json
```

LLM providers: the default runtime provider is GLM (`glm-5.2`) through an OpenAI-compatible Chat Completions adapter that forces a tool call. The Anthropic adapter sits behind the same interface and is selected with `LLM_PROVIDER=anthropic`. GLM eval results are evidence about the GLM runtime only. **Anthropic live API: NOT VERIFIED — no Anthropic credential.** Why GLM is the default: [DECISIONS.md](DECISIONS.md#glm-native-adapter-as-the-default-runtime-provider).

## One guest turn

1. **Resolve the tenant and apply limits.** The IP burst and per-minute IP limits run first, then the hotel is resolved to its tenant (unknown hotel: 404). Tenant, hotel and conversation limits follow.
2. **Lock and load the conversation.** A lease lock is taken on the conversation (shared across replicas when state is in Redis; 409 `CONVERSATION_BUSY` if it stays held). The conversation is looked up by `(tenant_id, hotel_id, conversation_id)`.
3. **Mask personal data.** Card numbers, and by default email addresses and phone numbers, are masked in the message and history before anything reaches the model, the trace or storage ([PRIVACY.md](PRIVACY.md)).
4. **Resolve the turn.** Tenant flags are read, "today" is computed in the hotel's time zone, and the knowledge snapshot for that date is loaded (published entries in their effective window only). An unreadable knowledge file gives 503 `KNOWLEDGE_UNAVAILABLE`.
5. **Input guardrails.** Attempts to extract the system prompt or secrets get a canned reply with no model call. Injection patterns are flagged and counted. Prompt tags in the current message and replayed history are neutralised.
6. **AI path.** Retrieve evidence, then make one model call with `answer_guest`, `check_availability` and `request_booking_details`.
   - `answer_guest` goes through the output guardrails: secret or prompt leakage, citations checked against the knowledge base, availability claims, and prices checked against cited entries (the price check can be switched off per tenant).
   - Action tools run through the `ToolRegistry`. Availability results go straight to the reply; prices and inventory never pass through the model.
7. **Degradation.**
   - Any model or provider failure (timeout, status, connection, protocol or SDK error, refusal, truncation, invalid output, rejected tool call) → the offline engine answers, with a notice, and `meta.degradation` reports `LLM_TIMEOUT` or `LLM_UNAVAILABLE`.
   - A reservation outage or tool failure in chat → a safe reply, with `meta.degradation` `RESERVATION_UNAVAILABLE`, `TOOL_TIMEOUT` or `TOOL_UNAVAILABLE`. On availability endpoints a reservation outage is 503 `RESERVATION_UNAVAILABLE`.
   - Redis unavailable → 503 `STATE_UNAVAILABLE` (the rate limiter fails open instead).
   - A genuine server bug → a structured 500.
8. **Record.** Messages (masked) and availability context are saved with a compare-and-set on the conversation version, and expiry slides forward. A version conflict is 409 `CONVERSATION_BUSY`, never a lost update. The turn emits an `AITrace`, metrics and domain events (without message text); with `DATABASE_URL` set, events are also written to PostgreSQL asynchronously. The response includes `meta` (trace id, prompt, tool-schema and knowledge versions, degradation).

Why the answer is a tool rather than a JSON output format, and why there is one call per turn rather than an agent loop: [DECISIONS.md](DECISIONS.md#stack-and-design-choices) and [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md). The full error list and failure table: [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md#5-fallback-and-degradation).

## Data

| Data | Today | Notes |
|---|---|---|
| Tenants → hotels | `app/data/tenants.json` | Two demo tenants: `tenant-demo` (Goa resort) and `tenant-metro` (Bengaluru business hotel). Synced to PostgreSQL `tenants`/`hotels` at startup when `DATABASE_URL` is set |
| Hotel profile, rooms, knowledge | `app/data/hotels/<hotel_id>/hotel.json` | Lifecycle fields: `status`, `version`, `effective_from/until`, `updated_by`; room entries are generated from room data. Cached per process |
| Inventory & pricing rules | `app/data/hotels/<hotel_id>/inventory.json` | Mock provider: weekday demand rules, blackout dates, seasonal multipliers. Availability results cached per process for 15 s |
| Conversations | In memory per process (`STATE_BACKEND=memory`, default) or Redis (`STATE_BACKEND=redis`) | TTL 24 h sliding, 40 messages max, 12 sent to the model, versioned for compare-and-set |
| Rate-limit windows, locks, idempotency records | Same backend as conversations | Redis rate limiter fails open; idempotency shared across replicas |
| Bookings | In memory per process (mock) | Booking tool is not exposed to the model; behind `booking_tools_enabled` |
| Audit events | PostgreSQL `audit_events` when `DATABASE_URL` is set | Asynchronous, best-effort; ids and counts only, no guest text; retention job (365 days default) |
| Other domain tables (conversations, messages, tool calls, bookings, knowledge, evaluations) | Schema only (`migrations/0001_domain_model.sql`) | Designed, not wired. Row-level security forced on all tables |

Proposed production stores beyond this (object storage, database-backed repositories): [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md).

## Frontend

- `src/api/client.ts` is the only place that calls `fetch`. It sets a 45 s timeout, validates the shape of successful responses, and classifies errors as network, timeout, validation, rate_limited, busy, too_large, not_found, unavailable, unexpected or server. The hotel id comes from `VITE_HOTEL_ID`, and there are no provider credentials anywhere in the frontend.
- `src/hooks/useChat.ts` creates the conversation lazily and sends only the new message and locale. It recreates an expired conversation once, transparently, retries once after a 409 busy response (waiting for `Retry-After`, capped at 3 s), and retries on request without duplicating the guest's message.
- Booking-form submissions go straight to the deterministic availability endpoint for the conversation, with no LLM involved.
- `src/i18n/` holds the English catalogue and a draft Hindi catalogue, with a language switcher driven by the hotel's `languages`. The requested locale is sent to the backend with each message.
- Accessibility:
  - The conversation is a `role="log"` live region with `aria-busy`.
  - Errors use `role="alert"`.
  - Focus moves into newly opened forms.
  - `lang` follows the selected locale.
  - A connection-status banner shows when the guest is offline.
- The page is branded from the hotel profile: assistant name and primary colour.

## Observability (names as emitted)

- **Logs** (JSON in production, secrets redacted, request/trace/tenant/hotel/conversation/channel context on every line, including the access log): `http_request`, `llm_failure`, `tool_audit`, `ai_trace`, `domain_event`, `startup` (includes `worker_threads` and `state_backend`), `conversations_purged`, `shutdown_complete`.
- **Metrics** (`/metrics`, internal only): `assistant_requests_total`, `assistant_success_total`, `assistant_failures_total`, `assistant_fallback_total`, `assistant_replies_total`, `unsupported_question_total`, `availability_search_total`, `tool_calls_total`, `tool_failures_total`, `guardrail_interventions_total`, `prompt_injection_signals_total`, `rate_limited_total`, `llm_tokens_total{kind,model}`, `llm_latency_ms`, `tool_latency_ms`, `request_latency_ms`, `turn_latency_ms{mode}`, `app_latency_ms{mode}`, `retrieval_latency_ms`, `pii_masked_total{kind}`, `state_backend_errors_total{component}`, `conversation_conflicts_total`, `audit_events_total{outcome}`.
- **Events:** `ConversationStarted`, `ConversationDeleted`, `GuestQuestionAsked`, `AssistantResponseGenerated`, `AvailabilityChecked`, `FallbackTriggered`, `GuardrailTriggered`, `ToolFailed`, `BookingRequested`, `BookingConfirmed`.

## Deployment

Details and verification results: [DEPLOYMENT.md](DEPLOYMENT.md).

- **Images:** `backend/Dockerfile` and `frontend/Dockerfile` are multi-stage builds on digest-pinned base images. They run as non-root (backend uid 10001, frontend nginx uid 101) and include health checks. The backend image ships the migrations and stops gracefully on SIGTERM (`--timeout-graceful-shutdown 25`).
- **Compose:** `docker-compose.yml` runs both containers on read-only filesystems with no capabilities and no new privileges. The frontend's nginx proxies `/api` with a non-spoofable `X-Forwarded-For` and adds security headers on every location; `/metrics` and the backend port are not exposed. `docker-compose.scale.yml` runs nginx, 3 backend replicas, Redis, PostgreSQL and a migration job; Redis and PostgreSQL publish no host ports, and the application database role is not a superuser and cannot bypass row-level security.
- **CI:** `.github/workflows/ci.yml` runs backend lint, tests, the offline eval gate and dependency audit; integration tests against Redis and PostgreSQL service containers; a secret scan; frontend lint, build, tests, bundle secret scan and audit; Playwright E2E; and Docker jobs (image build and checks, single-instance and 3-replica stack verification, a simultaneous booking probe, graceful stop). Standard CI needs no LLM secret.
- **Live AI eval:** `.github/workflows/live-ai-eval.yml` is a manual, secret-gated workflow (provider `glm` or `anthropic`) for evaluating against a live model.
- **Neither workflow has been run on GitHub.**
- Nothing here is production-ready; see [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md).
