# Release checklist

Status at submission (2026-09-17, branch `master`, no remote, nothing pushed). Re-verified after the
landing page, the UI redesign and the live scenario sweep; every command below was run again on this code.

- `[x]` verified by a command or test run during the audit (evidence given).
- `[ ]` not yet verified, or needs the human manual test pass ([MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md)).

## Code

- [x] **Tests pass.**
  - Backend `python -m pytest`: 460 passed, 22 skipped. The skipped tests are the optional Redis/PostgreSQL integration tests.
  - Frontend `npm test`: 30 passed (landing page and conversation).
  - Playwright `npm run test:e2e`: 14 passed (desktop + mobile, AI disabled).
- [x] **Lint passes.** `ruff check app tests evals scripts perf` and `npx oxlint src e2e` are clean.
- [x] **Typecheck passes.** `npx tsc -b` is clean.
- [x] **Build passes.** `npm run build` succeeds (JS bundle 260 kB, 80 kB gzip).
- [x] **Dependency audit.** `pip-audit -r requirements.txt` found no known vulnerabilities; `npm audit --omit=dev --audit-level=high` found 0.

## AI

- [x] **Offline eval passes.** Development suite 36/36 (6 AI-only scenarios skipped), critical 16/16, groundedness 15/15, fallback correctness 3/3, no regressions against `evals/results/offline.json`.
- [x] **Holdout eval passes.** Offline 12/12, critical 10/10, no regressions against `evals/results/holdout-offline.json`. The earlier GLM live holdout run was also 12/12 (GLM runtime evidence only).
- [x] **Grounding verified (automated).** For check-in, check-out, breakfast, pool, parking, cancellation, room capacity and room features, answers cite the matching knowledge entries. Draft and expired entries are never served, and a model citing a draft entry is rejected (`unknown_source`).
- [x] **Tool calling verified (automated).**
  - Unknown, unexposed and malformed tool calls are rejected.
  - Availability comes only from the deterministic tool.
  - The GLM development suite has 17/17 decision accuracy in the latest live run (`glm-rev8`).
- [x] **Fallback verified (automated).**
  - LLM timeout or error gives a grounded offline answer with `meta.degradation` (`LLM_TIMEOUT` / `LLM_UNAVAILABLE`).
  - A reservation outage gives a safe reply or 503, and never false availability.
- [x] **Prompt injection tested (automated).**
  - Injection, prompt extraction, key extraction, "every room available", policy change, booking without confirmation and fake prices were tested.
  - Injected history is rejected by v1; the legacy API neutralises it.
  - Tool results are rendered from structured data.
- [x] **Anthropic live API: explicitly unavailable.** NOT VERIFIED: there is no Anthropic credential, and the adapter is tested only with a mocked HTTP transport. GLM 5.2 development runs are not Claude verification.
- [x] **Live scenario sweep (GLM 5.2).** ~60 real turns across small talk, identity, every knowledge topic,
  unknown facts, local-area questions, draft content, off-topic, availability (relative, past, sold out,
  over-long, over-capacity), prompt injection, payment and PII, typos, emoji, multi-question turns and
  Hindi. Four defects found and fixed with regression tests; see the revision 8 section of
  [EVALUATION.md](EVALUATION.md). Re-run after the fixes: development 42/42 (critical 16/16), holdout 12/12.
- [ ] **Manual AI quality pass** (sections B-E and G of the manual test plan).

## Security

- [x] **Secret scan clean.**
  - All 154 tracked files: 0 findings.
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
- [ ] **Manual security pass** (section G).

## API

- [x] **OpenAPI current.** `docs/openapi.json` was regenerated and the snapshot contract test passes. Every v1 operation now documents the error statuses it can return (404/413/422/429/500/503, plus 409 on conversation turns).
- [x] **Error schema current.**
  - One envelope `{"error": {code, message, request_id, details}}`.
  - Verified for invalid JSON (422), wrong types (422), oversized body (413), unknown route (404), 405, rate limit (429 + `Retry-After`), injected server exception (500, no stack trace), Redis unavailable (503 `STATE_UNAVAILABLE`) and reservation outage (503).
- [x] **Frontend uses v1.** `frontend/src/api/client.ts` calls only `/api/v1/hotels/{hotel_id}/…` and validates response shapes.
- [x] **README examples work.** The create-conversation, message and availability examples were run against a live backend in offline mode. Reply types, sources, room prices and the message match; the README `meta` example was updated with `degradation: null`.

## UX

- [ ] **Landing page** (manual L1-L7). Automated: Vitest landing suite and two Playwright landing journeys.
- [ ] **Desktop** (manual H30). Automated: Playwright desktop project passes.
- [ ] **Mobile** (manual H31). Automated: Playwright Pixel 7 project passes.
- [ ] **Loading** (manual H34). Automated: Vitest loading-state test.
- [ ] **Retry** (manual F20/F20b, H35). Automated: Vitest and Playwright retry tests.
- [ ] **Accessibility** (manual H32 keyboard pass). Automated during audit: WCAG AA contrast met for all text colour pairs, labels and live regions present, focus moves into new forms. No axe-style scanner is installed, and none was added.
- [ ] **Hindi.** Implemented as a draft (65 of 66 keys; `contact.whatsapp` falls back to English by design). **Native review pending.**

## Repository

- [x] **README correct.** Windows PowerShell quick start, Node.js 22.12+, test commands. Every relative doc link resolves.
- [x] **Docs correct.** The code-vs-docs audit found 1 setup blocker (Node version) and 17 minor inconsistencies; all fixed. No production, Docker, CI-passed or Anthropic-verified claims remain.
- [x] **Git clean.** Working tree clean after commit `e4ec2d1` (fix: four defects found by a live scenario sweep); no remote; nothing pushed.
- [x] **No generated files tracked.** No `dist/`, `node_modules`, `.venv`, `__pycache__`, test results or probe scripts are tracked.
- [x] **No credentials.** See Security.
- [ ] **CI.** Configuration validated locally: YAML parses, and every step's command was run locally. **GitHub execution: NOT RUN** (no remote).
