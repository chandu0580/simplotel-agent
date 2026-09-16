# Release checklist

Status at the end of the release-candidate audit (2026-09-16, branch `master`, no remote, nothing pushed).

- `[x]` verified by a command or test run during the audit (evidence given).
- `[ ]` not yet verified, or needs the human manual test pass ([MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md)).

## Code

- [x] **Tests pass.**
  - Backend `python -m pytest`: 275 passed, 22 skipped. The skipped tests are the optional Redis/PostgreSQL integration tests.
  - Frontend `npm test`: 19 passed.
  - Playwright `npm run test:e2e`: 6 passed (desktop + mobile, AI disabled).
- [x] **Lint passes.** `ruff check app tests evals scripts perf` and `npx oxlint src e2e` are clean.
- [x] **Typecheck passes.** `npx tsc -b` is clean.
- [x] **Build passes.** `npm run build` succeeds (JS bundle 247.41 kB, 77.27 kB gzip).
- [x] **Dependency audit.** `pip-audit -r requirements.txt` found no known vulnerabilities; `npm audit --omit=dev --audit-level=high` found 0.

## AI

- [x] **Offline eval passes.** Development suite 28/28 (6 AI-only scenarios skipped), critical 14/14, groundedness 15/15, fallback correctness 3/3, no regressions against `evals/results/offline.json`.
- [x] **Holdout eval passes.** Offline 12/12, critical 10/10, no regressions against `evals/results/holdout-offline.json`. The earlier GLM live holdout run was also 12/12 (GLM runtime evidence only).
- [x] **Grounding verified (automated).** For check-in, check-out, breakfast, pool, parking, cancellation, room capacity and room features, answers cite the matching knowledge entries. Draft and expired entries are never served, and a model citing a draft entry is rejected (`unknown_source`).
- [x] **Tool calling verified (automated).**
  - Unknown, unexposed and malformed tool calls are rejected.
  - Availability comes only from the deterministic tool.
  - The GLM development suite has 18/18 decision accuracy in the final run.
- [x] **Fallback verified (automated).**
  - LLM timeout or error gives a grounded offline answer with `meta.degradation` (`LLM_TIMEOUT` / `LLM_UNAVAILABLE`).
  - A reservation outage gives a safe reply or 503, and never false availability.
- [x] **Prompt injection tested (automated).**
  - Injection, prompt extraction, key extraction, "every room available", policy change, booking without confirmation and fake prices were tested.
  - Injected history is rejected by v1; the legacy API neutralises it.
  - Tool results are rendered from structured data.
- [x] **Anthropic live API: explicitly unavailable.** NOT VERIFIED: there is no Anthropic credential, and the adapter is tested only with a mocked HTTP transport. GLM 5.2 development runs are not Claude verification.
- [ ] **Manual AI quality pass** (sections A–D and F of the manual test plan).

## Security

- [x] **Secret scan clean.**
  - All 191 tracked files: 0 findings.
  - Untracked new files: 0.
  - Frontend `dist/`: 0, with no provider identifiers.
  - All 324 blobs in git history: the only match is the deliberately fake test key (`sk-ant-api03-THIS-IS-A-TE…`). The local provider key and gateway host appear in no blob or commit message.
  - Nothing needs rotating.
- [x] **`.env` ignored and never tracked.** `.gitignore` covers `backend/.env`. `backend/.env.example` holds only empty or placeholder values and is parse-tested.
- [x] **Tenant isolation tested.** Another hotel's conversation returns 404 for read, write, delete and availability. Unknown or malformed hotel ids return 404. A tenant A admin token gets 403 on tenant B. A booking principal of another tenant or hotel gets FORBIDDEN. A foreign knowledge snapshot is rejected by the reservation provider.
- [x] **Tool authorization tested.** Flag off, no authentication, no confirmation, no or short idempotency key are all refused with no booking. Concurrent duplicates (2/5/10) create one booking; a replay returns the same id; key reuse with a different request conflicts.
- [x] **Logs redacted.** A turn containing a name, address, card, email, phone and booking reference produced no guest text in logs. Card, email and phone are masked in stored conversations. **Names, addresses and booking references are not masked** (documented limitation, [PRIVACY.md](PRIVACY.md)).
- [x] **Frontend has no secrets.** The bundle scan is clean, and the frontend holds no provider configuration (only the API base URL and public hotel id).
- [x] **Rate limiting covers invalid traffic.** Fixed in this audit: IP limits now apply in the middleware to every `/api/` request, including unknown routes and invalid bodies (`test_invalid_requests_and_unknown_routes_count_toward_the_ip_limit`). A normal 12-request guest session under default limits was never throttled.
- [ ] **Manual security pass** (section F).

## API

- [x] **OpenAPI current.** `docs/openapi.json` was regenerated and the snapshot contract test passes. Every v1 operation now documents the error statuses it can return (404/413/422/429/500/503, plus 409 on conversation turns).
- [x] **Error schema current.**
  - One envelope `{"error": {code, message, request_id, details}}`.
  - Verified for invalid JSON (422), wrong types (422), oversized body (413), unknown route (404), 405, rate limit (429 + `Retry-After`), injected server exception (500, no stack trace), Redis unavailable (503 `STATE_UNAVAILABLE`) and reservation outage (503).
- [x] **Frontend uses v1.** `frontend/src/api/client.ts` calls only `/api/v1/hotels/{hotel_id}/…` and validates response shapes.
- [x] **README examples work.** The create-conversation, message and availability examples were run against a live backend in offline mode. Reply types, sources, room prices and the message match; the README `meta` example was updated with `degradation: null`.

## UX

- [ ] **Desktop** (manual G30). Automated: Playwright desktop project passes.
- [ ] **Mobile** (manual G31). Automated: Playwright Pixel 7 project passes.
- [ ] **Loading** (manual G34). Automated: Vitest loading-state test.
- [ ] **Retry** (manual E20/E20b, G35). Automated: Vitest and Playwright retry tests.
- [ ] **Accessibility** (manual G32 keyboard pass). Automated during audit: WCAG AA contrast met for all text colour pairs, labels and live regions present, focus moves into new forms. No axe-style scanner is installed, and none was added.
- [ ] **Hindi.** Implemented as a draft (65 of 66 keys; `contact.whatsapp` falls back to English by design). **Native review pending.**

## Repository

- [x] **README correct.** Windows PowerShell quick start, Node.js 22.12+, test commands. Every relative doc link resolves.
- [x] **Docs correct.** The code-vs-docs audit found 1 setup blocker (Node version) and 17 minor inconsistencies; all fixed. No production, Docker, CI-passed or Anthropic-verified claims remain.
- [ ] **Git clean.** Clean after the audit commit (see the final report).
- [x] **No generated files tracked.** No `dist/`, `node_modules`, `.venv`, `__pycache__`, test results or probe scripts are tracked.
- [x] **No credentials.** See Security.
- [ ] **CI.** Configuration validated locally: YAML parses, and every step's command was run locally. **GitHub execution: NOT RUN** (no remote).
