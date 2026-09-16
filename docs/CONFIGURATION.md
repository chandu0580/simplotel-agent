# Configuration and feature flags

All configuration is read **once at startup** from environment variables (`Settings.from_env`, `backend/app/core/config.py`). Locally, a `backend/.env` file is loaded too, and variables already set in the environment win. Changing any value requires a restart. There is no runtime configuration API and no hot reload.

`Settings.validate()` runs at startup, and an invalid combination stops the process with a `ConfigError` listing every problem, so a misconfigured deployment fails fast instead of running unsafely. `backend/.env.example` lists every variable with safe defaults and is parse-tested (`test_example_env_file_parses_to_safe_defaults`).

Secret-bearing values (`LLM_API_KEY`, `ANTHROPIC_API_KEY`, `ADMIN_API_TOKENS`, `REDIS_URL`, `DATABASE_URL`) are excluded from `repr(Settings)`. Their values, including passwords embedded in URLs, are registered with the log redactor (`Settings.secret_values()`).

## Environment

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `test` \| `production`. `production` turns on the stricter rules below |
| `DATA_DIR` | `backend/app/data` | Tenant registry and per-hotel JSON |

## LLM

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `glm` | `glm` (OpenAI-compatible adapter, forced tool calls) \| `anthropic` \| `mock` (fixed-latency fake, load tests only) \| `none` |
| `AI_ENABLED` | `true` | Global kill switch. With AI off or no provider configured, guests get the offline FAQ engine |
| `LLM_API_KEY`, `LLM_BASE_URL` | — | Both required for `glm`. Base URL like `https://gateway.example/v1` |
| `LLM_MODEL`, `LLM_MODEL_FAST` | `glm-5.2`, = model | Used when the provider isn't `anthropic` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | — | Used only by `anthropic` |
| `ANTHROPIC_MODEL`, `ANTHROPIC_MODEL_FAST` | `claude-opus-5`, = model | |
| `ANTHROPIC_EFFORT` | `low` | `low`…`max`; sent only to Anthropic |
| `ANTHROPIC_REFUSAL_FALLBACK` | `default` | `default` \| `none` |
| `MOCK_LLM_LATENCY_MS` | `1500` | `mock` only |
| `LLM_TIMEOUT_SECONDS` | `20` | Per call |
| `LLM_MAX_RETRIES` | `1` | Transient errors only |
| `LLM_MAX_TOKENS` | `16000` | |

## HTTP and telemetry

| Variable | Default | Notes |
|---|---|---|
| `WORKER_THREADS` | `150` | Request-handler threads (1–1000). Caps concurrent AI turns per process; see [PERFORMANCE.md](PERFORMANCE.md) |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated |
| `TRUST_PROXY_HEADERS` | `false` | Use the first `X-Forwarded-For` hop as the client IP. Only behind a proxy that overwrites it |
| `EXPOSE_API_DOCS` | `true` outside production | `/docs` and `/openapi.json` |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `text` (`json` in production) | |
| `METRICS_ENABLED` | `true` | `/metrics`; keep it on the internal network |

## Shared state and database

| Variable | Default | Notes |
|---|---|---|
| `STATE_BACKEND` | `memory` | `memory` (one process) \| `redis` (conversations, rate limits, idempotency, locks shared by replicas) |
| `REDIS_URL` | — | Required for `redis`; `redis://`, `rediss://` or `unix://` |
| `REDIS_KEY_PREFIX` | `sa` | Namespaces keys; lets environments share a Redis |
| `DATABASE_URL` | — | Enables the PostgreSQL audit trail. Connect as a non-superuser role |
| `AUDIT_RETENTION_DAYS` | `365` | Default for `python -m app.db.retention` |

## Admin authentication

| Variable | Default | Notes |
|---|---|---|
| `AUTH_MODE` | `disabled` | `disabled`: admin endpoints return 401 `UNAUTHORIZED` with reason `auth_not_configured`. `static_token`: development only |
| `ADMIN_API_TOKENS` | — | `static_token` only: `<token>=<tenant\|*>:<role>[\|role][:<hotel>[\|hotel]],…` |

## Rate limits

| Variable | Default | Dimension |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | |
| `RATE_LIMIT_IP_BURST` / `RATE_LIMIT_BURST_WINDOW_SECONDS` | `30` / `10` | `ip_burst`, checked first |
| `RATE_LIMIT_IP_PER_MINUTE` | `60` | `ip`, checked before hotel resolution so unknown-hotel probes count |
| `RATE_LIMIT_TENANT_PER_MINUTE` | `3000` | `tenant` |
| `RATE_LIMIT_HOTEL_PER_MINUTE` | `1200` | `hotel` |
| `RATE_LIMIT_CONVERSATION_PER_MINUTE` | `20` | `conversation`, keyed per hotel |

Rejected requests get 429 `RATE_LIMITED` with `Retry-After` and `details: [{"dimension": …}]`.

## Caching, conversations, privacy

| Variable | Default | Notes |
|---|---|---|
| `KNOWLEDGE_CACHE_TTL_SECONDS` | `300` | Per process |
| `AVAILABILITY_CACHE_TTL_SECONDS` | `15` | Per process; `0` disables |
| `CONVERSATION_TTL_SECONDS` | `86400` | Sliding expiry |
| `CONVERSATION_MAX_MESSAGES` | `40` | Stored per conversation |
| `CONVERSATION_CONTEXT_WINDOW` | `12` | Messages sent to the model |
| `CONVERSATION_MAX_ACTIVE` | `50000` | In-memory backend only |
| `CONVERSATION_LOCK_WAIT_SECONDS` | `30` | How long a turn waits for the previous one before 409 `CONVERSATION_BUSY` |
| `CONVERSATION_LOCK_LEASE_SECONDS` | `120` | Must exceed `LLM_TIMEOUT_SECONDS × (LLM_MAX_RETRIES + 1)` |
| `PII_MASK_CONTACT_DETAILS` | `true` | Mask emails and phone numbers; card numbers are always masked ([PRIVACY.md](PRIVACY.md)) |

## Reservation integration resilience

| Variable | Default | Notes |
|---|---|---|
| `RESERVATION_TIMEOUT_SECONDS` | `5` | Per attempt |
| `RESERVATION_READ_RETRIES` | `2` | Reads only; mutations are never retried |
| `CIRCUIT_BREAKER_FAILURES` | `5` | Consecutive logical-call failures to open |
| `CIRCUIT_BREAKER_RESET_SECONDS` | `30` | Cooldown before the single half-open trial |

The overall read budget is `timeout × (retries + 1) + 1 s` (16 s by default). It must stay below the `check_availability` tool timeout (20 s), and a test asserts that.

## Validation rules

Always:
- Enumerated values must be valid (`APP_ENV`, `LLM_PROVIDER`, `ANTHROPIC_EFFORT`, `ANTHROPIC_REFUSAL_FALLBACK`, `LOG_FORMAT`, `AUTH_MODE`, `STATE_BACKEND`).
- `WORKER_THREADS` must be between 1 and 1000.
- `STATE_BACKEND=redis` requires `REDIS_URL` with a Redis scheme.
- `DATABASE_URL` must be `postgresql://`.
- `CONVERSATION_LOCK_LEASE_SECONDS` must exceed the LLM budget.

With `APP_ENV=production`:
- `LLM_BASE_URL` and `ANTHROPIC_BASE_URL` must use `https://`.
- `LLM_PROVIDER` must not be `mock`.
- `AUTH_MODE` must not be `static_token`.
- `CORS_ORIGINS` must not contain `*` or localhost.
- `LOG_FORMAT` must be `json`.
- `RATE_LIMIT_ENABLED` must be `true`.

These rules are covered by `tests/test_platform.py`, `tests/test_contracts.py` and `tests/test_state.py`.

## Feature flags

Flags are booleans with safe defaults (`backend/app/core/flags.py`). They are resolved in this order, highest priority first:
1. The tenant override in `app/data/tenants.json` (`"feature_flags": {…}`).
2. The global override from `FEATURE_<NAME>=true|false`.
3. The default.

Unknown flag names, in either place, stop startup. Like all configuration, flags are read at startup, so changing one needs a restart.

| Flag | Default | Effect | Status |
|---|---|---|---|
| `ai_assistant_enabled` | `true` | Per-tenant AI on/off; off means the offline FAQ engine | IMPLEMENTED + TESTED |
| `guardrail_price_check_enabled` | `true` | Output guardrail blocking prices not present in the knowledge or tool results | IMPLEMENTED + TESTED |
| `booking_tools_enabled` | `false` | Allows the `create_booking` tool (never exposed to the model; needs an authenticated guest, confirmation and an idempotency key) | IMPLEMENTED + TESTED (mock provider only) |
| `semantic_retrieval_enabled` | `false` | **Enabling it fails startup**: no semantic retriever exists | NOT IMPLEMENTED |
| `whatsapp_enabled` | `false` | Reserved; render adapter exists, no webhook | DESIGNED |
| `voice_enabled` | `false` | Reserved; render adapter exists, no telephony | DESIGNED |
