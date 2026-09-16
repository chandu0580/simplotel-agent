# Architecture overview

A short map of the codebase as it is today. For depth, see:

| Document | Covers |
|---|---|
| [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md) | Target architecture, boundaries, multi-tenancy, data model, scalability, migration path |
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) | Request lifecycles, sequence diagrams, failure paths |
| [THREAT_MODEL.md](THREAT_MODEL.md) | Threats, mitigations, residual risk |
| [SRE.md](SRE.md) / [OBSERVABILITY.md](OBSERVABILITY.md) | Reliability, proposed SLOs, runbooks, telemetry, dashboards |
| [COST_MODEL.md](COST_MODEL.md) | LLM cost drivers and levers |
| [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md) | Implemented vs designed, capability by capability |

## Shape: a modular monolith

One FastAPI process, split into packages with one-way dependencies. Every boundary is an interface, and the implementation for each is chosen in one file, [`app/container.py`](../backend/app/container.py).

```
Browser (React)
   │  /api/v1/hotels/{hotel_id}/...          (legacy /api/* kept, deprecated)
   ▼
api/            middleware (request id, W3C trace id, security headers, access log, latency metric)
                deps (tenant resolution, rate limits, admin authorization) · error model · routes
   │
   ▼
conversations/  ConversationService: server-side conversations, context window, locking, expiry
   │
   ▼
assistant/      AssistantService (one guest turn, any channel)
                 ├─ InputGuardrails   (exfiltration block, injection flags, prompt-tag neutralisation)
                 ├─ AIAssistant       (one LLM call, three strict tools, OutputGuardrails)
                 └─ OfflineAssistant  (deterministic fallback)
   │
   ├──► tools/          ToolRegistry: validation, exposure, flags, authorization, timeout, audit
   │       └──► reservations/   ReservationProvider → Resilient wrapper (timeout, retry, breaker, cache) → Mock
   ├──► knowledge/      KnowledgeProvider (JSON, content lifecycle) · Retriever → Evidence
   └──► llm/            LLMProvider (Anthropic adapter, scripted test provider) · ModelRouter

core/     config & validation · feature flags · errors · structured logging & redaction · metrics
          AI traces · domain events · cache · rate limiter · resilience · clock · versioning
tenancy   TenantRegistry · TenantContext           auth/      Principal, roles, auth providers
channels/ web · WhatsApp · voice render adapters   data/      tenants.json, hotels/<hotel_id>/{hotel,inventory}.json
```

## One guest turn

1. **Resolve the tenant and apply limits.** The IP rate limit runs first, then the hotel is resolved to its tenant (unknown hotel: 404). Hotel and conversation limits follow.
2. **Load the conversation.** It is looked up by `(tenant_id, hotel_id, conversation_id)`, under a per-conversation lock.
3. **Resolve the turn.** Tenant flags are read, "today" is computed in the hotel's time zone, and the knowledge snapshot for that date is loaded (published entries in their effective window only).
4. **Input guardrails.** Attempts to extract the system prompt or secrets get a canned reply with no model call. Injection patterns are flagged and counted. Prompt tags in the current message and replayed history are neutralised.
5. **AI path.** Retrieve evidence, then make one model call with `answer_guest`, `check_availability` and `request_booking_details`.
   - `answer_guest` goes through the output guardrails: secret or prompt leakage, citations checked against the knowledge base, availability claims, and prices checked against cited entries (the price check can be switched off per tenant).
   - Action tools run through the `ToolRegistry`. Availability results go straight to the reply; prices and inventory never pass through the model.
6. **Degradation.**
   - Any model or provider failure (status, connection, SDK error, refusal, truncation, invalid output, rejected tool call) → the offline engine answers, with a notice.
   - A reservation outage → a safe reply in chat, and 503 `AVAILABILITY_UNAVAILABLE` on availability endpoints.
   - A genuine server bug → a structured 500.
7. **Record.** Messages and availability context are saved, and expiry slides forward. The turn emits an `AITrace`, metrics and domain events (without message text). The response includes `meta` (trace id, prompt, tool-schema and knowledge versions).

Why the answer is a tool rather than a JSON output format, and why there is one call per turn rather than an agent loop: [DECISIONS.md](DECISIONS.md#stack-and-design-choices) and [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md).

## Data

| Data | Today | Notes |
|---|---|---|
| Tenants → hotels | `app/data/tenants.json` | Two demo tenants: `tenant-demo` (Goa resort) and `tenant-metro` (Bengaluru business hotel) |
| Hotel profile, rooms, knowledge | `app/data/hotels/<hotel_id>/hotel.json` | Lifecycle fields: `status`, `version`, `effective_from/until`, `updated_by`; room entries are generated from room data |
| Inventory & pricing rules | `app/data/hotels/<hotel_id>/inventory.json` | Mock provider: weekday demand rules, blackout dates, seasonal multipliers |
| Conversations | In memory, per process | TTL 24 h sliding, 40 messages max, 12 sent to the model |
| Bookings, idempotency records | In memory (mock) | Booking tool is not exposed to the model; behind `booking_tools_enabled` |

Proposed production stores (Postgres, Redis, object storage): [ENTERPRISE_ARCHITECTURE.md](ENTERPRISE_ARCHITECTURE.md).

## Frontend

- `src/api/client.ts` is the only place that calls `fetch`. It sets a 45 s timeout and classifies errors as network, timeout, validation, rate_limited, not_found, unavailable or server. The hotel id comes from `VITE_HOTEL_ID`, and there are no provider credentials anywhere in the frontend.
- `src/hooks/useChat.ts` creates the conversation lazily and sends only the new message and locale. It recreates an expired conversation once, transparently, and retries without duplicating the guest's message.
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

- **Logs** (JSON in production, secrets redacted, request/trace/tenant/hotel/conversation/channel context on every line): `http_request`, `llm_failure`, `tool_audit`, `ai_trace`, `domain_event`, `startup`, `conversations_purged`.
- **Metrics** (`/metrics`, internal only): `assistant_requests_total`, `assistant_success_total`, `assistant_failures_total`, `assistant_fallback_total`, `assistant_replies_total`, `unsupported_question_total`, `availability_search_total`, `tool_calls_total`, `tool_failures_total`, `guardrail_interventions_total`, `prompt_injection_signals_total`, `rate_limited_total`, `llm_tokens_total{kind,model}`, `llm_latency_ms`, `tool_latency_ms`, `request_latency_ms`.
- **Events:** `ConversationStarted`, `ConversationDeleted`, `GuestQuestionAsked`, `AssistantResponseGenerated`, `AvailabilityChecked`, `FallbackTriggered`, `GuardrailTriggered`, `ToolFailed`, `BookingRequested`, `BookingConfirmed`.

## Deployment

- **Images:** `backend/Dockerfile` and `frontend/Dockerfile` are multi-stage builds on digest-pinned base images. They run as non-root and include health checks.
- **Compose:** `docker-compose.yml` runs both containers on read-only filesystems with no capabilities and no new privileges. The frontend's nginx proxies `/api` with a non-spoofable `X-Forwarded-For` and adds security headers on every location; `/metrics` and the backend port are not exposed.
- **CI:** `.github/workflows/ci.yml` runs lint, tests, the offline eval regression gate, a dependency audit, E2E and a Docker smoke test. It has not been run on GitHub yet.
- **Live AI eval:** `.github/workflows/ai-eval.yml` is a manual, secret-gated workflow for evaluating against the live model. It has not been run.
