# Cost Model

How the guest assistant spends money, what the knobs are, and how to measure it once real traffic
exists. Written for engineering and product leads deciding how to run the assistant economically.

> **Status of the numbers in this document.**
> There is no production traffic and the live Anthropic API has **not** been verified. Every dollar
> figure below is **illustrative — verify current pricing before use**. Token counts are either
> measured on the demo hotel (stated as such) or explicit assumptions. No infrastructure or real
> spend figures are given.

Illustrative list prices used throughout (Anthropic list prices as cached 2026-06; USD per 1M tokens):

| Model | Input | Output | Notes |
|---|---:|---:|---|
| `claude-opus-5` (current default) | $5.00 | $25.00 | Adaptive thinking tokens are billed as output |
| `claude-sonnet-5` | $2.00 | $10.00 | |
| `claude-haiku-4-5` | $1.00 | $5.00 | |

Prompt-cache reads are billed at a discounted fraction of the input price, and cache writes may carry
a premium — check current cache pricing; this document does not assume a multiplier.

---

## 1. What drives cost

### One LLM call per guest turn (no agent loop)

`AIAssistant.reply` (`backend/app/assistant/agent.py`) makes **exactly one** `generate_with_tools`
call per guest message. The model answers by calling one tool:

- `answer_guest` — the reply text, citations and suggestions are the tool arguments; nothing is sent back to the model.
- `check_availability` / `request_booking_details` — executed by the `ToolRegistry`, and the result goes **straight to the UI** (`_action`). Availability data never passes back through the model.

Because there is no "call tool → feed result back → call model again" loop, cost per turn is bounded
by a single request: one prompt, one completion (capped by `max_tokens`). An agent loop would multiply
input tokens by the number of iterations, each re-sending the full prompt plus growing tool output.

### The drivers, in rough order of size

| Driver | Where it comes from | Scales with |
|---|---|---|
| **Prompt `P`** (system prompt + tool schemas) | `render_system_prompt` injects every knowledge entry (`FullContextRetriever`) plus 3 tool schemas | Hotel knowledge-base size. Measured on the demo hotel: ≈2.8k tokens (≈9.5k chars prompt + ≈1.7k chars tool JSON) |
| **History `H`** | `ConversationService.post_message` sends the last `conversation_context_window` = **12** stored messages (each truncated to 4,000 chars) | Conversation length, up to the window |
| **Message `M`** | `build_messages`: a `<context>` block (date, booking details, locale) + the guest message (max 1,000 chars) | Guest verbosity |
| **Output + thinking `O`** | Tool-call arguments plus adaptive thinking at `ANTHROPIC_EFFORT` (default `low`) | Effort level, question complexity; hard cap `LLM_MAX_TOKENS` = 16,000 |

Output tokens cost 5× input on every model above, so `O` matters more per token than `P`, but `P`
is re-sent on every turn and usually dominates volume.

### Paths that cost nothing (or extra)

- **Input guardrail blocks** (`InputGuardrails.check`, e.g. exfiltration attempts) return a canned reply before any LLM call — **0 tokens**.
- **Offline engine** (`OfflineAssistant`) — used when `ai_assistant_enabled` is off for a tenant, when no API key is configured, or after an `LLMError` — is deterministic keyword matching — **0 tokens**.
- **Booking-form availability searches** (`ConversationService.check_availability`) never touch the LLM — **0 tokens**.
- **Failed-after-spend turns**: a response that is truncated (`max_tokens`), refused, or has invalid output is billed and *then* falls back to the offline engine. These are pure waste; they should be rare and are visible via `assistant_fallback_total{reason}`.
- **Refusal fallback**: with `ANTHROPIC_REFUSAL_FALLBACK=default`, the API re-runs a policy-declined request once on a substitute model server-side. Assume that turn can be billed for more than one attempt (verify with current API docs).
- **Retries**: the SDK client is built with `max_retries=LLM_MAX_RETRIES` (default 1) and a 20 s timeout. Retries on connection errors, 429s and 5xxs can re-send the full prompt; a request that times out client-side may still have been processed and billed.

---

## 2. Per-turn token model

### Formula

```
input_tokens(turn)  = P + H + M
cost(turn)          = (1 − h)·P·p_in            # uncached prefix
                    + h·P·p_cache_read           # cached prefix (discounted; check pricing)
                    + (H + M)·p_in               # history + new message, never cached today
                    + O·p_out                    # visible output + thinking
```

- `P` prompt + tool schemas, `H` history tokens, `M` message + context-block tokens, `O` output incl. thinking.
- `h` cache-hit fraction for the prefix (0 ≤ h ≤ 1). The system block carries `cache_control: ephemeral`, so only the system prompt/tools prefix is a cache candidate.
- `p_in`, `p_out`, `p_cache_read` are per-token prices. Cache writes on misses may add a premium on top of `p_in`.

On the Anthropic API, `usage.input_tokens` counts only the *uncached* part; cache reads and cache writes appear
separately as `cache_read_input_tokens` and `cache_creation_input_tokens` (recorded by the app as
`cache_read_tokens` / `cache_write_tokens`). The total prompt is the sum of all three.

### Shape examples (not Claude)

Two calls logged during development came from a **different model and tokenizer** (GLM via an
Anthropic-compatible gateway), so they show shape, not Claude numbers:

| Call | input_tokens | cache_read_tokens | output_tokens (thinking included) |
|---|---:|---:|---:|
| A | 138 | 2,880 | 650 |
| B | 3,016 | — | 102 |

Call A shows the intended pattern: the ≈2.8k prefix served from cache, only ~140 fresh tokens.
Call B shows an uncached prompt of similar size with a short output. Output varied ~6× between the
two, which is why `O` below is a range-driven assumption. The eval result files
(`backend/evals/results/glm-dev-v1.1-run{1,2}.json`) record no token counts.

### Worked example (illustrative — verify current pricing before use)

Assumptions (not measurements, except `P`):

| Variable | Short FAQ, turn 1 | Follow-up, turn 6 |
|---|---:|---:|
| `P` | 2,800 (measured, demo hotel) | 2,800 |
| `H` | 0 | 750 (5 prior turns × (60 user + 90 assistant); 10 messages, inside the 12-message window) |
| `M` | 100 | 100 |
| `O` | 400 (≈150 tool-call args + ≈250 thinking at `low`) | 500 |
| `h` | 0 (no cache) | 0 (no cache) |
| **Input total** | **2,900** | **3,650** |

**Turn 1, short FAQ** (`2,900 in / 400 out`):

| Model | Input | Output | Per turn |
|---|---|---|---:|
| Opus 5 | 2,900 × $5 / 1M = $0.01450 | 400 × $25 / 1M = $0.01000 | **$0.0245** |
| Sonnet 5 | 2,900 × $2 / 1M = $0.00580 | 400 × $10 / 1M = $0.00400 | **$0.0098** |
| Haiku 4.5 | 2,900 × $1 / 1M = $0.00290 | 400 × $5 / 1M = $0.00200 | **$0.0049** |

**Turn 6, follow-up** (`3,650 in / 500 out`):

| Model | Input | Output | Per turn |
|---|---|---|---:|
| Opus 5 | 3,650 × $5 / 1M = $0.01825 | 500 × $25 / 1M = $0.01250 | **$0.03075** |
| Sonnet 5 | 3,650 × $2 / 1M = $0.00730 | 500 × $10 / 1M = $0.00500 | **$0.0123** |
| Haiku 4.5 | 3,650 × $1 / 1M = $0.00365 | 500 × $5 / 1M = $0.00250 | **$0.00615** |

**Per 1,000 conversations.** Assume a 4-turn conversation, `M` = 100 and `O` = 400 each turn, history
growing by 150 tokens per turn:

```
input  = (2,800+0+100) + (2,800+150+100) + (2,800+300+100) + (2,800+450+100)
       = 2,900 + 3,050 + 3,200 + 3,350 = 12,500 tokens
output = 4 × 400 = 1,600 tokens
```

| Model | Per conversation | Per 1,000 conversations |
|---|---|---:|
| Opus 5 | 12,500 × $5/1M + 1,600 × $25/1M = $0.0625 + $0.0400 = $0.1025 | **$102.50** |
| Sonnet 5 | 12,500 × $2/1M + 1,600 × $10/1M = $0.0250 + $0.0160 = $0.0410 | **$41.00** |
| Haiku 4.5 | 12,500 × $1/1M + 1,600 × $5/1M = $0.0125 + $0.0080 = $0.0205 | **$20.50** |

Sensitivities worth noting:

- **Prefix share.** 4 × 2,800 = 11,200 of the 12,500 input tokens (≈90%) are the repeated prefix. If caching applies, most of the input bill moves to the discounted cache-read rate.
- **Thinking.** Thinking tokens vary per request and the 250-token figure is an assumption. If `O` were 1,000 per turn on Opus 5, output becomes 4,000 × $25/1M = $0.10 and the conversation ≈$0.1625 (≈$162.50 per 1,000).
- **Model choice changes cost ≈2.5× (Opus → Sonnet) and ≈5× (Opus → Haiku)** at these prices; quality impact is unmeasured.

---

## 3. Scaling illustration (assumption table — not a forecast)

Assumptions: 100 conversations/day per hotel, 30 days, 4 turns/conversation, per-conversation tokens
from section 2 (12,500 input + 1,600 output), **no prompt caching**, no fallbacks or retries.

| Hotels | Conversations / month | Input tokens / month | Output tokens / month | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3,000 | 37.5M | 4.8M | $307.50 | $123.00 | $61.50 |
| 10 | 30,000 | 375M | 48M | $3,075 | $1,230 | $615 |
| 100 | 300,000 | 3.75B | 480M | $30,750 | $12,300 | $6,150 |
| 1,000 | 3,000,000 | 37.5B | 4.8B | $307,500 | $123,000 | $61,500 |

Example row (1 hotel, Opus 5): 37.5M × $5 + 4.8M × $25 = $187.50 + $120.00 = $307.50.

Real hotels will differ widely in knowledge-base size (`P`), traffic, and conversation length; treat this
table as a way to reason about orders of magnitude and which lever matters, nothing more.

---

## 4. Cost levers, in priority order

Free wins first; quality trade-offs last and only behind evals (`docs/EVALUATION.md`).

1. **Prompt caching with a byte-stable system prompt.** Already wired: the system block is sent with
   `cache_control: ephemeral`, and `AIAssistant` memoises the rendered prompt keyed by
   `(hotel_id, knowledge_version, hash(evidence ids))`. Caching is an exact-prefix match, so a single
   changed byte (a timestamp, reordered entries, a per-request value) turns every call into a cache miss
   plus a write. That is why today's date, booking context and locale live in the **user** message
   (`build_messages`), not the system prompt. With `FullContextRetriever` the evidence set is constant
   per knowledge version, so the prefix is stable until the hotel edits content.
   *Caveats to verify:* Anthropic models have a minimum cacheable prefix length that may exceed the
   ≈2.8k-token demo prefix, so small hotels may get no caching at all; cache entries expire after a
   short idle TTL, so low-traffic hotels will miss often; tool definitions must also be byte-stable
   (they vary with `tenant_flags`). Measure the hit rate before counting on savings.
2. **Trim the history window.** `CONVERSATION_CONTEXT_WINDOW=12` is a config change. Most hotel
   questions need the booking context (already sent structurally) more than old turns. Validate with the
   `conversation` eval tag before lowering.
3. **Retrieval instead of full context once knowledge bases grow.** `P` grows linearly with content
   under `FullContextRetriever`. A top-k retriever caps `P`, but can miss the relevant entry (a grounding
   failure) and makes the evidence set — and therefore the cached prefix — vary per question, which
   lowers cache hits. Switch per hotel only when `P` is large enough to outweigh both effects.
4. **Tune effort per route via evals.** Default is `low`. Raising effort raises thinking (output-priced)
   tokens; do not raise it without an eval showing a quality gain worth the cost.
5. **Deterministic short-circuits.** Input guardrail blocks already cost nothing. Routing trivial FAQ
   questions (e.g. "what time is check-in?") to the offline engine would also cost nothing, but the
   offline engine returns raw knowledge text, cannot handle follow-ups or language nuance, and
   misroutes are a guest-visible quality drop. Only consider with a high-precision matcher and evals.
6. **Model routing via `ModelRouter`.** Routes are per task (`GUEST_TURN`, `INTENT_CLASSIFICATION`,
   `CONVERSATION_SUMMARY`, `EVAL_JUDGE`). Today every task maps to the configured models, and
   `ANTHROPIC_MODEL_FAST` defaults to the primary model. Moving `GUEST_TURN` (or a subset of turns) to
   Sonnet or Haiku is the largest single lever in section 2, and should happen only after evals show
   parity on grounding, tool-calling and safety tags.
7. **Per-tenant budgets, quotas and rate limits.** Cap spend per hotel (see section 5) so one tenant
   cannot consume a disproportionate share, and align quotas with commercial plans.
8. **Response caching for identical FAQ questions.** Could skip the LLM for repeated questions. Risks:
   staleness (key and TTL must be tied to `knowledge_version`), locale (key must include it),
   personalisation (never cache turns that depend on history or booking context), and guardrail
   decisions made on a different message. Lower priority because hit rates on free-text are uncertain.
9. **Batch processing for evals.** Eval runs are not latency-sensitive; running them through a batch
   API (discounted where available) reduces the cost of eval-gating every change.

---

## 5. Guardrails against cost incidents

| Control | Current state | What it bounds |
|---|---|---|
| `max_tokens` | `LLM_MAX_TOKENS=16000` per call | Worst-case output per call: 16,000 × $25/1M = **$0.40 on Opus 5** (illustrative). Generous relative to a ~400-token typical reply; consider lowering after measuring the real `O` distribution, keeping headroom for thinking so replies are not truncated |
| Rate limits | Per IP 60/min, per conversation 20/min, per hotel 1,200/min (`RATE_LIMIT_*`); cannot be disabled in production | Request volume. Note the per-hotel limit alone permits a theoretical ceiling of 1,200 × $0.40 = $480/min of output on Opus 5 if every call hit `max_tokens` — rate limits bound abuse, not spend |
| Message length | `MAX_MESSAGE_CHARS = 1000` | `M` |
| History window | 12 messages, each truncated to 4,000 chars | `H` |
| Stored messages | `CONVERSATION_MAX_MESSAGES=40` | Storage, not tokens |
| Kill switch | `ai_assistant_enabled` feature flag, per tenant (and `AI_ENABLED` globally) | Stops all LLM spend for a tenant; guests get the offline engine with a notice |

**Proposed (not implemented):**

- **Per-tenant token budgets** (daily/monthly), enforced before the LLM call, degrading to the offline engine when exhausted.
- **Alerts on `llm_tokens_total`**: rate-of-change alerts per `kind` (e.g. output tokens/min above a multiple of the trailing baseline), plus alerts on `assistant_fallback_total{reason="truncated"}` and refusal spikes, which indicate paid-but-wasted calls.
- A lower per-hotel rate limit for tenants on smaller plans.

---

## 6. Measuring cost in production (proposed)

What exists today:

- **`llm_tokens_total{kind, model}`** (Prometheus) with `kind` ∈ `input`, `output`, `cache_read`, `cache_write`, labelled by the model that served the response. It has no tenant label by design (series cardinality).
- **`ai_trace` log events** (`app/core/tracing.py`) per turn with `tenant_id`, `hotel_id`, `conversation_id`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `prompt_version`, `knowledge_version`, `mode` and `fallback_reason`.

Proposed practice:

1. **Fleet-level spend** from `llm_tokens_total` as Σ over `model` and `kind` of tokens × that model's price for that kind (input, output, cache read, cache write). Because the counter carries `model`, spend stays priceable when routing sends turns to more than one model.
2. **Per-tenant attribution** by aggregating `ai_trace` logs on `tenant_id` (and `model`) in the log pipeline, pricing input, output, cache-read and cache-write tokens separately.
3. **Cache hit rate** = `cache_read / (input + cache_read + cache_write)` per hotel, to confirm lever 1 is actually working; a persistently high `cache_write` share means the prefix is not stable or the cache TTL is expiring between visits.
4. **Key unit metric: cost per resolved conversation** — LLM cost divided by conversations that ended in an `answer` or `availability` reply without a fallback or front-desk handoff. Cost per turn rewards short, unhelpful conversations; cost per resolved conversation does not.
5. **Eval-gated changes.** Any change to model, effort, history window or retrieval strategy runs the eval suite first and reports both pass rates and token usage per scenario, so quality and cost move together in one review. (Current eval result files do not record tokens; adding them is part of this proposal.)

---

## 7. Non-LLM costs (qualitative)

Numbers are intentionally omitted; there is no deployment to measure.

- **Compute.** App overhead per turn is a few milliseconds (guardrails, prompt assembly, tool
  execution); the API process mostly waits on the LLM. Sizing is driven by concurrent in-flight LLM
  calls and timeouts, not CPU.
- **Storage.** Conversations (TTL 24 h, capped at 40 messages), knowledge bases and tenant config are
  small. Retention of traces and events is the larger, policy-driven item.
- **Observability.** Structured `ai_trace` logs are emitted per turn; log ingestion and retention
  volume scales with traffic and is often a non-trivial line item. Metrics are low-cardinality by design.
- **Reservation-provider API fees.** `check_availability` calls a reservation provider (cached for
  `AVAILABILITY_CACHE_TTL_SECONDS=15`, with retries and a circuit breaker). Some providers charge per
  call or impose quotas; check the commercial terms per integration.
