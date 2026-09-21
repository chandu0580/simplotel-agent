# Running and deploying

**Docker/containerization: NOT REQUIRED FOR CURRENT PROJECT — removed intentionally.** The assignment is about the AI product: the assistant, its tools, grounding, evaluation, reliability and APIs. The application runs as a plain Python service and a static React build, and nothing here depends on containers.

Status labels follow [ENTERPRISE_READINESS.md](ENTERPRISE_READINESS.md).

## Hosted demo

| Piece | Where | URL |
|---|---|---|
| SPA | Vercel, static build of `frontend/` | <https://simplotel-agent.vercel.app> |
| API | Render, free web service | <https://simplotel-agent-api.onrender.com> |

Configuration is committed in [`render.yaml`](../render.yaml) and [`frontend/vercel.json`](../frontend/vercel.json).
`LLM_API_KEY`, `LLM_BASE_URL` and `CORS_ORIGINS` are set in the Render dashboard (`sync: false`) and are never committed.

**Why it runs `APP_ENV=development`.** The demo's GLM gateway is reachable only over plain HTTP, and production
startup validation rejects a non-HTTPS model endpoint. Rather than weaken that check, the demo runs as development
with the production-shaped settings set explicitly: `LOG_FORMAT=json`, `RATE_LIMIT_ENABLED=true`, `AUTH_MODE=disabled`
and `METRICS_ENABLED=false`. A deployment with an HTTPS model endpoint runs `APP_ENV=production` unchanged.

**Deliberate exceptions.** `/docs` is left reachable (`EXPOSE_API_DOCS=true`) so a reviewer can read the live API.
`/metrics` is switched off rather than exposed, because the demo has no internal network to keep it on.
The admin API is unreachable by design: it returns `401 auth_not_configured` until real authentication is configured.

**What the demo is not.** One free Render instance holding all state in memory: no shared state, no load balancer,
no secret manager, no log shipping. The instance sleeps when idle, so the first request after a quiet period takes
roughly a minute. Nothing here has been measured against the SLOs in [SRE.md](SRE.md).

## Local development (canonical)

Backend (Python 3.13), Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1        # cmd.exe: .venv\Scripts\activate.bat
pip install -r requirements.txt   # requirements-dev.txt for tests, lint and the load test
uvicorn app.main:app --reload --port 8000
```

macOS / Linux: the same commands, activating with `source .venv/bin/activate`.

Frontend (Node.js 22.12+):

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173, proxies /api to http://127.0.0.1:8000
```

Configuration is read from environment variables or `backend/.env` (copy `backend/.env.example`). Without a configured LLM provider the assistant runs in offline FAQ mode, so the app works with no credentials. See [CONFIGURATION.md](CONFIGURATION.md).

State is in memory by default (`STATE_BACKEND=memory`). That's the intended local setup: nothing else needs to be installed.

## Production build of the frontend

```bash
cd frontend
npm run build                     # static files in frontend/dist
```

`frontend/dist` can be served by any static host. That host must also:

- route `/api/*` to the backend (or set `VITE_API_BASE_URL` at build time and list the site origin in the backend's `CORS_ORIGINS`)
- set **Content-Security-Policy** (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`), **Permissions-Policy** (camera, microphone, geolocation, payment, usb denied), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer` on HTML and assets
- send **Strict-Transport-Security** where TLS terminates

These static-hosting headers are **implemented for the hosted demo** in [`frontend/vercel.json`](../frontend/vercel.json)
and verified on the deployed site; its `connect-src` also allows the Render API origin, without which the browser would
block every API call. Any other static host must set them itself. The backend sets its own headers on every API response (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store` for `/api`, and HSTS when `APP_ENV=production`), and `test_security_headers_request_ids_and_trace_propagation` asserts `X-Content-Type-Options`, `X-Frame-Options` and `Cache-Control`; `Referrer-Policy` and HSTS are set in code (`app/api/middleware.py`) but not asserted by a test.

## Running the backend outside development

```powershell
$env:APP_ENV="production"; uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 25
```

Bash: `APP_ENV=production uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 25`.

`APP_ENV=production` turns on startup validation. The process refuses to start if:
- a model endpoint isn't `https://`
- the mock provider is selected
- `CORS_ORIGINS` contains `*` or localhost
- logs aren't JSON
- rate limits are off
- development token auth is on

**Graceful shutdown.** On SIGTERM, uvicorn stops accepting connections and waits for in-flight requests (up to `--timeout-graceful-shutdown`). The FastAPI lifespan then calls `Container.close()`, which flushes queued audit events, closes the database pool, the LLM HTTP client and Redis, and logs `shutdown_complete`. A guest turn can take up to the LLM budget (`LLM_TIMEOUT_SECONDS × (LLM_MAX_RETRIES + 1)`, 40 s by default), so pick the timeout with that in mind. Not implemented: `/ready` doesn't switch to "draining" on SIGTERM.

**Health.** `/health` is liveness (event loop only). `/ready` is readiness: it requires the knowledge store and the conversation state store, and reports reservations, LLM and audit store without failing on them. `/metrics` (Prometheus) must stay on an internal network; the hosted demo has none, so it sets `METRICS_ENABLED=false`.

## More than one backend process (optional)

In-memory state is per process. If the backend ever runs as several processes or hosts, they must share state:

| Setting | What it provides | Status |
|---|---|---|
| `STATE_BACKEND=redis`, `REDIS_URL` | Shared conversations (compare-and-set), rate limits, idempotency results and locks | IMPLEMENTED; tests in `tests/integration/test_redis_state.py` (including three app instances sharing one Redis) passed locally once against Redis 7.4; **not run in CI** |
| `DATABASE_URL` | PostgreSQL audit trail; migrations `python -m app.db.migrate`; retention `python -m app.db.retention`; row-level security (connect as a non-superuser role) | IMPLEMENTED; `tests/integration/test_postgres.py` passed locally once against PostgreSQL 17; **not run in CI**. Other repositories (bookings, knowledge, conversations in SQL) are DESIGNED only |

Both are optional. They aren't needed for development, tests, evaluation or the assignment, and no setup for them is included here. When `TEST_REDIS_URL` / `TEST_DATABASE_URL` aren't set, their tests skip.

## Deployment infrastructure

**Demo only.** The hosted demo above is a free-tier SPA host plus one backend instance; TLS terminates at Vercel and
Render. No load balancer, secret manager, log shipping or container setup is implemented or proposed here. When the
product needs a real deployment, choose it then, using the requirements on this page:
- the static-hosting headers
- HTTPS model endpoints
- shared state for more than one process
- `/metrics` kept internal
- graceful-shutdown timing
