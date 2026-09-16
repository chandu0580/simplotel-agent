# Hotel Guest Assistant

An AI guest assistant for a hotel website, built as a full-stack app. Guests can ask about the property, rooms, amenities and policies, and check room availability for their dates, all in one conversation.

- **Frontend:** React + TypeScript (Vite). Chat UI with an inline booking form and room result cards.
- **Backend:** Python + FastAPI. Hotel knowledge base in JSON, a deterministic availability service, and Claude (`claude-opus-5`) for understanding questions and writing answers.
- **Keeps working when the AI fails:** if the model is down, times out, refuses or returns bad output, the backend answers from the FAQ using deterministic matching and tells the guest.

The demo property, *The Palm Grove Resort, Goa*, is fictional.

| Doc | What's in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow and API contract |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Product, UX, engineering and AI decisions, plus answers to the assignment's questions |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Test and eval scenarios with observed results |

## Status

| | |
|---|---|
| Implemented and tested locally | Chat UI, booking form, room results, loading/error/offline states, FastAPI API, knowledge-base grounding checks, deterministic availability, fallback engine |
| Automated results (2026-09-16) | Backend **75 passed** · Frontend **9 passed** · E2E **6 passed** (desktop + mobile) · Offline eval **17/17** (6 AI-only skipped) |
| **Live Claude API** | **Not yet verified.** No Anthropic key was available during development. The request format was checked with the real SDK against a mocked HTTP transport, but the live API hasn't been called. See [Evaluation § C](docs/EVALUATION.md#c-live-model-evaluation). |
| Mocked | Room inventory and rates (`inventory.json`), booking (there is none; guests are pointed to the front desk) |

## The customer problem

A guest deciding whether to book has quick questions: check-in time, breakfast, cancellation, "is there a room for three of us next weekend?". The answers are scattered across policy pages or need a call or WhatsApp to the front desk, so guests drop off or book through an OTA, which costs the hotel commission. This assistant answers from the hotel's own data straight away, checks live availability (mocked here), and hands off to staff whenever it can't answer reliably.

## How it works

```
Browser (React)  ──POST /api/chat──────────►  FastAPI  ──►  ChatService
      │                                                     ├─ ClaudeAssistant: one Claude call per turn
      │                                                     │    system prompt + knowledge base, 2 tools, JSON output
      │                                                     │    → code checks citations, runs check_availability,
      │                                                     │      builds the booking form, adds contact details to fallbacks
      │                                                     └─ on any model failure → OfflineAssistant (keyword FAQ + intent rules)
      └──POST /api/availability (booking form)──►  check_availability (deterministic: dates, capacity, inventory, price)
```

The model interprets questions and writes answers. **Code decides the facts that matter:** dates are validated, and room capacity, inventory and prices come only from backend data and go straight to the UI. The API key lives only in the backend. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Quick start

**Prerequisites:** Python 3.11+ (developed on 3.13), Node.js 20+ (developed on 22), and an Anthropic API key if you want AI mode.

> **Windows:** clone into a short path (e.g. `C:\dev\simplotel-agent`) or [enable long paths](https://pip.pypa.io/warnings/enable-long-paths). Some files in the Anthropic SDK have very long names; in a deeply nested folder `pip install` cannot write them (OSError) and the backend then fails with `ModuleNotFoundError: anthropic.types...`. This was hit during clean-clone verification.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY in .env
uvicorn app.main:app --reload --port 8000
```

Check it's running: `curl http://localhost:8000/api/health` returns `{"status":"ok","mode":"ai"}`. The mode is `"offline"` if no key is set. Interactive API docs are at http://localhost:8000/docs.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000`, so the browser only talks to your own backend and never sees the API key. To point at a different backend, set `VITE_PROXY_TARGET` in dev, or `VITE_API_BASE_URL` for a production build.

### Try it

1. Ask "What time is check-in?" You get an answer with a "Based on: Check-in and check-out times" source line.
2. Follow up with "Can I check in early?" The conversation context carries over.
3. Ask "Do you have rooms available?" A date and guest form appears in the chat.
4. Pick a weekday stay for 3 adults. You get room cards with total price, breakfast badge and rooms left.
5. Pick a Friday–Sunday stay for 4 adults + 1 child. Only the Family Suite fits, and it's sold out on Saturdays, so you see the sold-out state.
6. Ask "Is there a casino?" You get a fallback with Call, WhatsApp and Email buttons.
7. Stop the backend and send a message. An error appears with a "Try again" button.

---

## Running tests

```bash
# Backend unit, API and SDK-contract tests (75); Claude is faked or HTTP-mocked, so no key is needed
cd backend && python -m pytest

# Frontend component tests (9): loading, error/retry, forms, results, follow-up context
cd frontend && npm test

# End-to-end (6 runs: 3 flows × desktop + mobile viewport). Starts real backend + frontend.
cd frontend && npx playwright install chromium && npm run test:e2e
#   E2E_USE_AI=true npm run test:e2e   → same flows against live Claude (needs key)

# Scenario evals (23 scenarios; 6 only run in AI mode)
cd backend && python -m evals.run_evals --mode offline   # deterministic engine, free
cd backend && python -m evals.run_evals --mode ai        # live Claude, costs tokens
```

Results are written to `backend/evals/results/<mode>.md`.

---

## API examples

### Ask a question

> **About these examples:** the AI-mode JSON bodies (`"mode": "ai"`) are illustrative. They show the response shape the schema enforces, but they weren't captured from a live Claude run (see Status). The `/api/availability` and error examples are real outputs from the backend.

```bash
curl -s http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Does the hotel have a swimming pool?", "history": []}'
```

```json
{
  "request_id": "c5a4465ed8e9",
  "mode": "ai",
  "reply": {
    "type": "answer",
    "text": "Yes, the resort has an outdoor lagoon swimming pool open daily from 7:00 AM to 8:00 PM, with a separate shallow children's pool.",
    "sources": [{ "id": "amenities.pool", "title": "Swimming pool" }],
    "suggestions": ["Is there a lifeguard?", "Is breakfast included?"],
    "availability": null,
    "booking_prefill": null,
    "form_error": null
  },
  "notice": null
}
```

### Follow-up with conversation context

```bash
curl -s http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
        "message": "Does it include breakfast?",
        "history": [
          {"role": "user", "content": "Which room is suitable for three guests?"},
          {"role": "assistant", "content": "The Deluxe Pool View Room sleeps three, and the Family Suite sleeps up to five."}
        ],
        "booking_context": {"adults": 3}
      }'
```

### Availability request without dates

The backend asks the UI to show the booking form:

```bash
curl -s http://localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"message": "Do you have rooms available?"}'
```

```json
{
  "mode": "ai",
  "reply": {
    "type": "collect_booking_details",
    "text": "Happy to check! Which dates would you like, and how many guests?",
    "booking_prefill": { "check_in": null, "check_out": null, "adults": null, "children": null }
  }
}
```

### Availability with dates in the question

The model calls `check_availability`:

```bash
curl -s http://localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"message": "Any rooms for 3 adults from 2026-10-07 to 2026-10-09?"}'
# → reply.type = "availability", reply.availability = { ...same shape as below... }
```

### Deterministic availability

This is the endpoint the booking form uses. No LLM is involved.

```bash
curl -s http://localhost:8000/api/availability -H "Content-Type: application/json" \
  -d '{"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 3, "children": 0}'
```

```json
{
  "check_in": "2026-10-07", "check_out": "2026-10-09", "nights": 2, "adults": 3, "children": 0,
  "available": true,
  "rooms": [
    { "room_id": "deluxe-pool-view", "name": "Deluxe Pool View Room", "max_occupancy": 3,
      "breakfast_included": true, "rooms_left": 8, "nightly_rate": 7800, "total_price": 15600, "currency": "INR", "...": "..." }
  ],
  "sold_out_room_names": [],
  "message": "2 room types available for 3 adults, 2 nights from Wed 07 Oct 2026 to Fri 09 Oct 2026.",
  "season_label": null
}
```

### Errors

All errors share one shape:

```bash
curl -s http://localhost:8000/api/availability -H "Content-Type: application/json" \
  -d '{"check_in": "2026-10-09", "check_out": "2026-10-07", "adults": 2}'
```

```json
{ "request_id": "c80b465cecb4",
  "error": { "code": "invalid_booking_details", "message": "Check-out date must be after the check-in date.", "details": null } }
```

| Status | `error.code` | When |
|---|---|---|
| 422 | `validation_error` | Malformed body: blank or oversized message, bad date format, unknown fields, too much history. `details` lists the fields. |
| 422 | `invalid_booking_details` | Well-formed but not bookable: past dates, check-out before check-in, stay over 30 nights. |
| 500 | `internal_error` | Unexpected server error. No internals are exposed; use `request_id` to find it in the logs. |

LLM failures do **not** return an error. They return `200` with `"mode": "offline"` and a `notice`.

---

## Configuration

Set these in `backend/.env` (see [.env.example](backend/.env.example)):

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for AI mode. Server-side only. |
| `ANTHROPIC_MODEL` | `claude-opus-5` | |
| `ANTHROPIC_EFFORT` | `low` | `low` / `medium` / `high`. Low keeps chat latency down; raise it if evals show a gap. |
| `ANTHROPIC_REFUSAL_FALLBACK` | `default` | `default`: if Claude declines a request, the API re-runs it once on a substitute model (server-side, beta `server-side-fallback-2026-07-01`). `none`: turn off, e.g. for a model or platform that doesn't support it. |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `20` / `1` | After these, the backend falls back to offline mode. |
| `AI_ENABLED` | `true` | Set to `false` to force offline mode, like a kill switch. |
| `CORS_ORIGINS` | `http://localhost:5173` | Explicit allow-list (no wildcard). Only matters when the frontend is served from a different origin than the API; the Vite dev proxy makes calls same-origin. |

## Project layout

```
backend/
  app/
    main.py               FastAPI app: routes, request IDs, error envelope, logging
    service.py            Chooses AI vs offline assistant; degrades on LLM failure
    claude_assistant.py   Prompt, tools, structured output, grounding checks
    offline.py            Deterministic FAQ matching + availability intent (fallback engine)
    availability.py       Mock inventory, validation, pricing: checkAvailability
    knowledge.py          Loads and validates the knowledge base
    schemas.py            Request/response contract (Pydantic)
    data/hotel.json       Hotel knowledge base (rooms, policies, amenities, FAQs)
    data/inventory.json   Mock inventory, weekend demand rules, blackout dates
  tests/                  pytest: availability, offline engine, knowledge base, API + LLM flows (fake client),
                          SDK request contract (real SDK, mocked HTTP)
  evals/                  Scenario evals runnable offline or against live Claude; results/ holds the latest run
frontend/
  src/api/                Typed API client (timeouts, error classification)
  src/hooks/useChat.ts    Conversation state, history, booking context, retry
  src/components/         MessageList, AvailabilityForm, AvailabilityResults, Composer
  src/App.test.tsx        Component tests
  e2e/                    Playwright end-to-end tests (desktop + mobile)
docs/                     Architecture, decisions, evaluation
```

## Limitations (intentional for this assignment)

- **Mocked availability.** Inventory, weekend demand and rates come from `inventory.json`, not a PMS or booking engine. There's no reservation flow; room cards point to the front desk.
- **Small, static knowledge base.** 17 entries plus 4 rooms in JSON, all placed in the prompt. There's no admin UI and no retrieval.
- **Stateless conversations.** The client sends the last 12 turns plus the last booking details. Nothing is persisted server-side, and a client could forge history. That only affects wording, never prices or availability.
- **No authentication, rate limiting or bot protection.**
- **Children are counted toward room occupancy regardless of age.** The policy says under-6s stay free; the form doesn't collect ages.
- **English only.** Dates in offline mode are recognised only in ISO format (`2026-10-07`); natural-language dates need AI mode.
- **Prompt caching is requested but likely inactive.** The prompt prefix (about 2.8k tokens) is probably below the model's minimum cacheable size.
- **Not verified against the live Claude API** (see Status).

Production next steps are in [docs/DECISIONS.md](docs/DECISIONS.md#what-would-you-improve-before-taking-this-to-production): real booking-engine integration, server-side sessions, rate limiting, observability, an eval pipeline in CI, a knowledge-base admin UI, and multilingual support.

## AI tools used

- **Claude Code** (Anthropic's coding agent, running Claude Opus 5): scaffolding, implementation, tests, the final engineering audit, and documentation drafts. Every test result quoted in this repo comes from commands actually run.
- **Claude API (`claude-opus-5`):** the runtime model the backend is built for. Not yet exercised live; see Status.
