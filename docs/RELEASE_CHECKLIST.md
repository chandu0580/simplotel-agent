# Release checklist

Status at submission (2026-09-17, branch `master`, no remote, nothing pushed). Re-verified after the
landing page, the UI redesign and the live scenario sweep; every command below was run again on this code.

- `[x]` verified by a command or test run during the audit (evidence given).
- `[ ]` not yet verified, or needs the human manual test pass ([MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md)).

## Acceptance matrix

Statuses: PASS (verified by a command or run recorded here) · PARTIAL · FAIL · NOT VERIFIED · N/A.
Manual cases in [MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md) are deliberately unticked: they are for the
human pass before submission.

| Area | Status | Evidence | Remaining |
|---|---|---|---|
| Assignment requirements | PASS | Requirement-by-requirement audit in [ASSIGNMENT_SCOPE.md](ASSIGNMENT_SCOPE.md); guest UI, question API, knowledge base, availability tool, conversation context, fallback, validation, errors, logging and automated tests all implemented | Optional deployment/demo not done (allowed: GitHub repo is the deliverable) |
| Frontend | PASS | Landing page and conversation; `npm test` 30 passed, `npx playwright test` 14 passed (desktop + mobile), `npm run build` clean; browser probe at 390/412/768/1280/1920 px found no horizontal overflow and no internals (provider, trace id, prompt version) in guest-visible text | Manual UX pass H30-H37 |
| Backend | PASS | `python -m pytest` 464 passed, 22 skipped; API v1 with structured errors; clean-venv install from `requirements.txt` started the app and served a live turn | Manual failure pass F20-F24 |
| AI | PASS | One model call per turn, three strict tools; degradation verified live with an unreachable endpoint: HTTP 200, offline answers, `meta.degradation=LLM_TIMEOUT`, honest guest notice | Anthropic not live-verified (no credential) |
| Conversation | PASS | Live scripted journey (greeting to goodbye) reads naturally; deterministic fast paths stay small and the model owns the rest | Manual B0-B6 |
| Knowledge grounding | PASS | Live checks of check-in, check-out, breakfast, pool, amenities, capacity, cancellation, ID, extra bed: every answer cited knowledge entries; draft content (Skyline rooftop bar) never served; unknown facts escalated | Manual B1-B6, C7-C9b |
| Availability | PASS | Live edge cases: check-out before check-in 422, zero/negative/excessive adults 422, past dates 422, fully booked 200 `available=false`, valid search returns rooms with capacity, price and rooms left from deterministic code | Manual D10-D15 |
| Tools | PASS | `pytest -k "tenant or isolation or idempot or concurrent or authoriz or booking_tool"` 31 passed, 11 skipped: validation, authorization, tenant context, timeout, audit, idempotency under 2/5/10 concurrent duplicates, confirmation for mutation | Manual E-series security cases |
| Follow-ups | PASS | Live context script: "it", "the deluxe one", "next weekend", "one more night" and "that room's price" all resolved correctly, prices matching the search | Manual E16-E20b |
| Guardrails | PASS | Uncited answers, fabricated prices, inventory claims, secret and prompt leakage blocked; a published figure the answer forgot to cite is now cited rather than discarded (`price_source_added`) | - |
| Security | PASS | Live injection sweep (instruction override, prompt extraction, fake discount, booking without authorization, card details) all refused; secret scan of 156 tracked files and the built bundle: 0 findings | Manual G25-G29b |
| Tenant isolation | PASS | Cross-tenant reads, writes, deletes and availability return 404; admin token of one tenant gets 403 on another (test suite above) | - |
| API | PASS | Single error envelope verified for 404/405/409/413/422/429/500/503; OpenAPI snapshot contract test passes; frontend calls v1 only and validates responses | - |
| Evaluation | PASS | Offline development 36/36 (critical 16/16) and holdout 12/12, both with no regressions against the committed baselines; live GLM development 42/42 and holdout 12/12 | Live runs are GLM, not Claude |
| Tests | PASS | Backend 464 passed / 22 skipped (optional Redis + PostgreSQL), frontend 30, e2e 14, ruff and oxlint clean, `tsc -b` clean | Optional service tests need Redis/PostgreSQL |
| Documentation | PASS | Every relative link in README and docs resolves (checked programmatically); stale evaluation figures refreshed; implemented / tested / designed / not verified labelled throughout | - |
| CI | NOT VERIFIED | Configuration is valid and every step was run locally (ruff, pytest, offline eval baseline gate, secret scans, oxlint, build, vitest, playwright, pip-audit, npm audit) | GitHub Actions has never executed: no remote |
| Performance | PARTIAL | Local load test and in-process benchmark recorded in [PERFORMANCE.md](PERFORMANCE.md), with the mock provider; live GLM latency p50 about 2.6 s per scenario | No production capacity test; not Anthropic latency |
| Git | PASS | Branch `master`, working tree clean, no remote, nothing pushed; no `.env`, build output, virtualenv or temporary files tracked | Add a remote and push at submission |
| Submission package | PASS | README, frontend, backend, tests, evals, docs, configuration and `.gitignore` present; clean-venv install verified | Manual test pass, then push |

## Code

- [x] **Tests pass.**
  - Backend `python -m pytest`: 464 passed, 22 skipped. The skipped tests are the optional Redis/PostgreSQL integration tests.
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
