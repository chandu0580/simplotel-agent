# Architecture

```
┌──────────────────────────── Browser ────────────────────────────┐
│ React app                                                        │
│  useChat: messages, history (last 12), booking_context, retry   │
│  MessageList → answer / fallback / form / room cards / error     │
└───────────────┬───────────────────────────────┬──────────────────┘
                │ POST /api/chat                │ POST /api/availability
                │ {message, history,            │ {check_in, check_out,
                │  booking_context}             │  adults, children}
┌───────────────▼───────────────────────────────▼──────────────────┐
│ FastAPI                                                           │
│  middleware: request id, access log, 500 envelope                │
│  validation → 422 envelope                                        │
│                                                                   │
│  ChatService ──► ClaudeAssistant ──(1 call)──► Claude API         │
│      │               │  system prompt + knowledge base (cached)   │
│      │               │  model must call exactly one tool:         │
│      │               │   answer_guest {type, text, source_ids,    │
│      │               │                 suggestions}               │
│      │               │   check_availability / request_booking_... │
│      │               ▼                                            │
│      │           post-processing (deterministic):                 │
│      │            • tool call → run availability / build form     │
│      │            • validate source_ids against knowledge base    │
│      │            • uncited "answer" → fallback                   │
│      │            • fallback → always append contact details      │
│      │                                                            │
│      └─ on LLMError (API error, timeout, refusal, bad JSON) ──►   │
│         OfflineAssistant (keyword FAQ + intent regex)             │
│         response.mode = "offline", notice set                     │
│                                                                   │
│  availability.check_availability  ◄── used by both paths + form  │
│  knowledge.py (hotel.json)  inventory.json                        │
└───────────────────────────────────────────────────────────────────┘
```

## Frontend

- **`api/client.ts`:** the only place that calls `fetch`. It adds a 45 s timeout and sorts failures into `network`, `timeout`, `validation` or `server`, so the UI can show the right message and decide whether a retry makes sense. The browser only calls the backend. No keys or model settings exist in frontend code.
- **`hooks/useChat.ts`:** holds conversation state. It sends the last 12 text turns as `history`, plus `booking_context` (the last dates and guests the guest searched), so "what about 3 adults?" works without re-parsing old messages. Retry re-sends the failed question without adding it to the chat a second time.
- **Reply rendering depends on `reply.type`:**
  - `answer` / `clarification`: text, plus a "Based on" source line and suggestion chips.
  - `fallback`: accent styling plus Call, WhatsApp and Email buttons.
  - `collect_booking_details`: an inline form, pre-filled with whatever the backend already knows. After a search it collapses to a one-line summary.
  - `availability`: room cards with total price, average nightly rate, breakfast and scarcity badges, and a sold-out state.
- **Booking form submission** goes straight to `/api/availability`, with no LLM round-trip. Once the guest has filled in structured fields there's nothing left for the model to interpret, so this path is faster, cheaper and always correct.

## Backend

| Module | Responsibility |
|---|---|
| `main.py` | Routes, CORS, request-ID middleware, a single error format, access logging. Runs the synchronous Anthropic client in a threadpool. |
| `schemas.py` | Strict Pydantic contract: unknown fields are rejected, messages are capped at 1,000 characters, history at 20 items. |
| `knowledge.py` | Loads `hotel.json`, validates it, and flattens it into citable entries like `policies.cancellation` or `rooms.family-suite`. Room entries are generated from room data, so facts are never duplicated. |
| `availability.py` | Mock `checkAvailability`: validates dates (not past, check-out after check-in, at most 30 nights, at most 12 months ahead), filters rooms by adults, children and total occupancy, takes the minimum free inventory across all nights, and applies seasonal pricing. Pure code, so it's unit-testable. |
| `claude_assistant.py` | One model call per turn in which the model calls exactly one of three strict tools, followed by deterministic checks on the result (see below). |
| `offline.py` | Conservative fallback engine: keyword scoring over knowledge entries, a regex for availability intent, a party-size parser for "room for three guests", and ISO date extraction. |
| `service.py` | Uses AI mode when it's configured and falls back to offline on `LLMError`. Logs mode, reply type and sources for each request. |

### What happens on an AI turn

1. The request passes schema validation.
2. The backend builds `messages` from the history, dropping a leading assistant greeting because the API requires the first message to be from the user. It then adds a `<context>` block (hotel-local date and booking context) and wraps the guest text in `<guest_message>` tags, so the model treats it as data rather than instructions.
3. Claude is called with:
   - the system prompt and the full knowledge base (about 2.8k tokens including tools, marked for prompt caching, though probably below the model's minimum cacheable size, so it may not cache);
   - three strict tools: `answer_guest` (the reply schema), `check_availability`, `request_booking_details`;
   - `tool_choice: {type: "auto", disable_parallel_tool_use: true}`, plus a prompt rule to always reply through exactly one tool (forced `tool_choice` is rejected while thinking is on);
   - `effort: low` and no output format (see *Why the answer is a tool* below);
   - server-side refusal fallbacks (`fallbacks: "default"`).
4. What comes back determines the reply:
   - **`answer_guest`:** validated against the reply schema, then checked for grounding (below). A JSON text reply without a tool call is accepted if it matches the same schema; plain prose is rejected (`LLMError`).
   - **Action tool call:** arguments are validated with Pydantic first. The string `"null"` or `"none"`, or an empty string, is treated as a missing value (one model sent `"null"` for nullable fields). Wrong types or an unknown tool raise `LLMError`, which means offline fallback, not a 500. Remembered booking dates fill in only when the model gave **no** dates, so a new check-in is never paired with an old check-out.
   - **`check_availability` tool call:** the backend validates the arguments and runs the availability service. The **result goes straight to the UI**, and the summary text is generated by code. Prices and inventory never pass through the model's output, so it can't misquote them.
   - **`request_booking_details` tool call:** the backend returns a form, pre-filled from the tool arguments and the booking context.
   - **Grounding of `answer_guest`:** each `source_id` is checked against the knowledge base, and unknown ids are dropped and logged. An `answer` left with no valid source becomes a fallback. Every fallback gets the front-desk contact details appended.
5. Refusals, truncated output, invalid JSON, invalid tool arguments, API status and connection errors, timeouts and any other Anthropic SDK error all raise `LLMError`, and the offline engine answers instead. Bugs in our own code still return a structured 500 rather than being hidden by the fallback.

### Pointing the app at another endpoint

The Anthropic SDK honours `ANTHROPIC_BASE_URL`, so the same code can be aimed at an Anthropic-compatible gateway for development without code changes. This was used to run the eval suite against `glm-5.2` (see [EVALUATION.md § D](EVALUATION.md#d-development-provider-evaluation-glm-52--not-claude)). Such runs never count as Claude verification. The eval runner writes them under a separate `--label`.

### Refusal fallback

With `ANTHROPIC_REFUSAL_FALLBACK=default`, requests carry `fallbacks: "default"` and the beta header `server-side-fallback-2026-07-01`. If Claude declines, Anthropic's API re-runs the same request once on a substitute model it chooses by refusal category, inside the same HTTP call. There's no client-side retry loop. If the substitute also declines, `stop_reason` is `refusal`, which becomes `LLMError` and then offline mode. SDK retries (`LLM_MAX_RETRIES`, default 1) apply only to transport failures, 429s and 5xx responses. Set `none` for models or platforms that don't support the parameter (it's unavailable on Bedrock, Vertex AI and Foundry).

### Why the answer is a tool, not a JSON output format

The first version combined a JSON-schema output format with the two action tools. Anthropic documents that combination as supported. But when the app's unchanged code path was pointed at a different real model during development (`glm-5.2` behind an Anthropic-compatible gateway), that model **never** called a tool while the output format was set (0/4 probes), and always called the right one without it (4/4). Moving the answer into a third strict tool:

- gives exactly one decision point per turn;
- removes reliance on one provider feature combination;
- keeps the same schema and grounding checks.

That evidence came from GLM, **not Claude**; the change is a portability and robustness choice, not a verified Claude fix.

### Why one call instead of an agent loop

Every tool result here is final. Availability results go straight to the UI, and the form tool is itself the reply. So there's nothing to feed back to the model. That keeps latency to one model round-trip and cost to one call per turn, and it means numbers are never paraphrased.

## Data

- `hotel.json`: hotel profile, 4 room types (occupancy limits, beds, base rate, breakfast), and 17 knowledge entries covering timings, policies, amenities, location and contact.
- `inventory.json`: rooms per type, weekday demand rules (Ocean Villas sold out on Fridays and Saturdays, Family Suites on Saturdays, Deluxe rooms 75% booked on Saturdays), blackout dates (Dec 24, 25 and 31), and seasonal price multipliers (peak season ×1.6, monsoon ×0.75).

## Security boundaries

- The Anthropic key is read from `backend/.env` or the environment by `config.py`, and only the backend's Anthropic client uses it. The frontend bundle contains no provider configuration; the scan found no `sk-ant` or `anthropic` strings in `frontend/src` or `dist`.
- CORS is an explicit allow-list (`CORS_ORIGINS`, default `http://localhost:5173`), never `*`. In development the Vite proxy makes API calls same-origin anyway.
- Guest text is never logged. Logs contain request IDs, reply types, source ids, tool names and arguments (dates and guest counts), token counts and error categories.

## Observability

Every request gets an `X-Request-ID`, taken from the client if supplied and generated otherwise. It's returned in the response header and in error bodies. The backend writes these log lines:

- `http_request`: method, path, status, latency.
- `llm_call`: model, stop_reason, latency, input, output and cache-read tokens.
- `llm_tool_call`: tool name and input.
- `llm_unknown_source` / `llm_ungrounded_answer`: grounding problems.
- `llm_failure`: the error behind a switch to offline mode.
- `chat_reply`: mode, reply type, cited sources.
- `availability_search`: search parameters and whether anything was available.

These logs are what the metrics in [DECISIONS.md](DECISIONS.md#how-would-you-measure-whether-the-feature-is-actually-useful) would be built on.
