# Privacy: data minimisation, retention and deletion

What guest data this system handles, where it goes, how long it stays, and what isn't protected. This is an engineering description, not legal advice or a compliance claim.

## Data inventory

| Data | Source | Where it goes | Retention | Status |
|---|---|---|---|---|
| Guest message text (after masking) | Chat input | LLM provider (current message + last `CONVERSATION_CONTEXT_WINDOW` messages); conversation state | Conversation TTL (24 h sliding), or until the guest deletes it | IMPLEMENTED + TESTED |
| Booking context (dates, adults, children) | Form or model tool call | Conversation state; model context | Conversation TTL | IMPLEMENTED + TESTED |
| Reply text and type | Assistant | Conversation state | Conversation TTL | IMPLEMENTED + TESTED |
| Client IP address | HTTP | Rate-limit keys: raw in the in-memory limiter, **SHA-256-hashed** in Redis. Not in the app's structured `http_request` log; uvicorn's default access log and nginx's access log do record it (disable or ship them with an IP policy) | Rate-limit window (≤ 1 min) in Redis | IMPLEMENTED + TESTED (hashing) |
| Domain events | Service | Logs; PostgreSQL `audit_events` when `DATABASE_URL` is set | `AUDIT_RETENTION_DAYS` (365) via the retention job | IMPLEMENTED + TESTED |
| AI traces | Service | Logs and an in-memory ring buffer; ids, versions, evidence ids, tool names and arguments for read-only tools (dates, guest counts), token counts, latencies. **No message text** | Log retention (not configured here) | IMPLEMENTED + TESTED |
| Guest identity for bookings | Authenticated principal | Opaque `guest_reference` only; never name, email or phone | Booking lifetime | DESIGNED (no guest authentication is implemented) |
| Payment data | — | Not collected. The assistant tells guests not to share card details | — | Not handled by design |

## Minimisation before the model (`backend/app/core/privacy.py`)

Before a guest message reaches the model, traces or storage, `AssistantService.handle` masks personal data. It does the same for client-supplied history on the legacy endpoint.

| Kind | Rule | Replacement | Default |
|---|---|---|---|
| Payment card number | 13–19 digits, spaces or dashes allowed, **Luhn-valid** | `[card number removed]` | Always on |
| Email address | `local@domain.tld` | `[email removed]` | On (`PII_MASK_CONTACT_DETAILS=true`) |
| Phone number | Optional `+`, 8–15 digits with spaces/dashes/brackets; ISO dates and `dd/mm/yyyy` excluded | `[phone number removed]` | On |

Why: no feature needs these values. The assistant can't charge cards, call guests or send email, so sending them to a third-party model provider or storing them only creates risk.

Evidence (`tests/test_privacy.py`):
- Masking works for each kind in several formats, and the function is idempotent.
- **False positives are avoided** for ISO and `dd/mm/yyyy` dates, prices, times, room numbers, and a 16-digit booking reference that fails the Luhn check.
- End to end, the request sent to the model and the stored conversation contain neither the card number nor the email; the trace records `pii_masked: ["card", "email"]`; and `pii_masked_total{kind}` increments.
- The live GLM holdout scenario `holdout-card-number` passed: the reply didn't repeat the number, and the model told the guest not to share card details.

**Residual risk.** Masking is pattern matching:
- Names, postal addresses, passport numbers, booking-site credentials and free-text descriptions are **not** detected.
- Unusual phone formats and card numbers split across messages can slip through.
- Masked text still reaches the model provider, so its data-processing terms apply to everything else a guest writes.

## Retention

| Store | Mechanism | Evidence |
|---|---|---|
| In-memory conversations | Sliding TTL, checked on read; background purge every 5 min; LRU cap `CONVERSATION_MAX_ACTIVE` | `test_conversations_expire_and_can_be_deleted` |
| Redis conversations | Key TTL set from `expires_at` on every save; Redis deletes expired keys | `test_conversation_repository_cas_and_native_ttl` |
| Redis rate-limit and lock keys | Expire with their window or lease | `test_rate_limit_window_slides`, `test_lock_store_across_clients` |
| Redis idempotency results | 24 h TTL | Code: `RedisIdempotencyStore(ttl_seconds=24 * 3600)` |
| PostgreSQL `audit_events` | `python -m app.db.retention` deletes events older than `AUDIT_RETENTION_DAYS`, tenant by tenant under row-level security with the app role | `test_retention_purges_old_audit_events_and_expired_conversations_per_tenant` |
| PostgreSQL `conversations` / `messages` / `tool_calls` | Same job deletes expired conversations, and child rows cascade. These tables are schema only; nothing writes to them yet | Same test |
| Logs and traces | Owned by the log pipeline. **Not configured** in this repository | — |

The retention job is a command. Scheduling it (cron, a Kubernetes CronJob) is part of deployment and **NOT IMPLEMENTED** here.

## Deletion

A guest (or the UI on their behalf) can delete a conversation:

```
DELETE /api/v1/hotels/{hotel_id}/conversations/{conversation_id}   → 204
```

After deletion, `GET`, `POST …/messages` and a second `DELETE` all return 404. The conversation can't be recreated by a late write, because version checks refuse to save a conversation that no longer exists. A `ConversationDeleted` event (ids only) is published. A hotel can't delete another hotel's conversation: the lookup is keyed by tenant, hotel and id. Evidence: `test_deleted_conversation_is_gone_and_cannot_be_written_again`, `test_repository_save_is_compare_and_set`, `test_cross_tenant_access_is_rejected_on_another_replica`.

**Not implemented:**
- Deleting a guest's data across conversations (there is no guest identity).
- Erasing audit events for one conversation before the retention period ends. Events hold ids and counts only, never message text.
- Purging the model provider's copy.
