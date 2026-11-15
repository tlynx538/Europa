# Phase 4B validation — Home Coordinator

Branch `feature/phase4b-home-coordinator` (base PR #5 `1d13685`). Behavior and API are
described in [phase4b-home-coordinator.md](phase4b-home-coordinator.md).

Environment: macOS, CPython 3.13.0 (`.venv`), Node v24.19.0, Playwright 1.64.1,
Google Chrome 156.0.8078.80. `SSL_CERT_FILE` unset for Python runs.

## Commits

| Commit | Content |
|---|---|
| `f3fe920` | Deterministic demo provider; Home backend gap fixes |
| `3ec07b2` | Home Coordinator backend tests (`tests/test_agent_home.py`, 25 tests) |
| `ee16310` | Home Coordinator panel (frontend) and vitest tests (13 tests) |
| `a6f9083` | Agent browser journeys (`web/playwright.agent.config.ts`, `web/browser-agent/`) and screenshots |
| `94b6886` | This report, the 4B design doc, README/AGENTS updates, owner brief |
| (review cycle 1 commit) | Agent spec selector fix; coordinator validation results |

Python sources (`api/`, `tests/`) are unchanged after `3ec07b2`, and `web/src` is
unchanged after `ee16310`, so the counts below apply to the current sources.

## Results

| Check | Command | Result |
|---|---|---|
| Agent backend tests | `.venv/bin/python -m pytest -q tests/test_agent_harness.py tests/test_agent_home.py` | **48 passed**, 0 skipped (23 harness + 25 Home) |
| Python suite (no Gateway) | `.venv/bin/python -m pytest -q` | **353 passed, 105 skipped** (PR #5 baseline 328 passed / 105 skipped; +25 new) |
| Frontend unit tests | `cd web && npm test` | **107 passed** in 19 files (baseline 94; +13 new) |
| Type check and build | `cd web && npm run build` | passed (`tsc --noEmit` and Vite build) |
| Agent browser journeys (after the selector fix below) | `cd web && EUROPA_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npx playwright test -c playwright.agent.config.ts` | three sequential runs: **3 passed** (18.7 s), **3 passed** (16.2 s), **3 passed** (15.7 s); exit 0 each; 0 retries, 1 worker |
| Python suite with real local Gateway (coordinator run, source `94b6886`) | `.venv/bin/python -m pytest -q --run-local-gateway` | **379 passed, 79 skipped**, exit 0 |
| Phase 3 Chrome suite (coordinator runs, source `94b6886`) | `cd web && EUROPA_CHROME=… npm run test:browser` | run 1: **55 passed, 1 failed, 1 skipped**; run 2: **56 passed, 1 skipped**, exit 0 (see below) |

Skips: the 105 skipped tests in the plain Python run are opt-in integration tests
("requires --run-local-gateway" or `--run-containerd`). In the Gateway run all 79 skips
are containerd tests ("Linux required; macOS host has no containerd/runc/runsc/socket
or identified Linux VM"). Skipped tests are not counted as validation.

### Phase 3 Chrome suite failure (run 1)

Run 1 failed `browser-dashboard-c/sessions.spec.ts:41`: the "Imported" session row was
still "Starting", not "Idle", within 5 s. This failure is **not counted as a pass**.
Discriminating check by the coordinator: the `sessions` project alone, three
alternating warm runs each. Unmodified `main` `c5a8a60` failed 2 of 3 runs the same
way; 4B failed 1 of 3. The failure is therefore a pre-existing intermittent timing
failure in the Phase 3 sessions journey, not a 4B regression. The host had 238
pre-existing `kernel_gateway` processes and a load average of about 4 during these runs.

The Phase 3 suite overwrites the committed captures in `docs/screenshots/phase3h`;
the coordinator restored them with `git checkout` after the runs.

### Agent spec selector defect (review cycle 1)

An independent run of the agent journeys gave 1 passed / 2 failed (create and denial,
in `freshHome`). Cause: `panel(page).locator('summary')` in
`web/browser-agent/home-coordinator.spec.ts` also matched the nested run-panel
`<summary>` elements ("Completed · 0 actions") from the previous journey's Home thread,
a Playwright strict-mode violation whenever that thread rendered before the assertion.
The earlier passing runs had won that race. Fix (test-only; app code unchanged): the
panel summary is selected with `:scope > summary`, and the run-panel summaries with
`.home-thread details.agent-run > summary`, asserted as exactly one run in each fresh
thread. The other locators were audited: they are unique (approval card list items,
exact-name buttons, text-filtered `article.message`) or select deliberately
(`.last()` on thread messages). No sleeps, timeouts or retries were added. After the fix:
three sequential runs passed (above). After them there were no listeners on port
18400, no `uvicorn` processes, and the `kernel_gateway` process list was identical to the
238-process baseline. `docs/screenshots/phase3h` was unchanged; the phase4b captures were
re-taken and still show the documented states.

## Backend coverage (PRD §10 cases 1–18)

`H` = `tests/test_agent_harness.py` (PR #5), `N` = `tests/test_agent_home.py` (4B).

| # | Case | Tests |
|---|---|---|
| 1 | v6→v7 migration, append-only, Home link rejection | H `test_v6_to_v7_migration_preserves_records_and_append_only` |
| 2 | Unregistered scope-free conversation 409 | H `test_unregistered_scope_free_conversation_is_not_writable` |
| 3 | Events append-only, runs undeletable | H `test_agent_events_are_append_only` |
| 4 | Text reply, event sequence, fences | H `test_text_reply_…`; N `test_discussion_reply_creates_nothing_and_fences_workspace_data` |
| 5 | Policy / invalid | H `test_policy_rejects_…`, `test_home_role_cannot_receive_notebook_execution_privileges`; N `test_invalid_names_and_forbidden_tools_are_rejected` |
| 6 | Approval, exact `Revenue Forecasting` files, origin, model called twice | N `test_approved_creation_makes_exactly_the_previewed_resources`, `test_existing_project_receives_only_the_notebook` |
| 7 | Denial | N `test_denial_creates_nothing_and_tells_the_model` |
| 8 | Four concurrent decisions | N `test_four_concurrent_identical_decisions_create_once`, `test_four_concurrent_distinct_decisions_accept_exactly_one` |
| 9 | Collisions | N `test_proposal_time_conflict_leaves_owner_file_untouched`, `test_file_appearing_before_approval_fails_as_conflict_untouched`, `test_existing_non_project_folder_is_a_conflict`, `test_collision_beyond_first_listing_page_and_by_letter_case_is_detected` |
| 10 | Expiry | H `test_expired_approval_cannot_be_used`; N `test_expired_approval_is_409_and_run_completes_without_creating` |
| 11 | Cancellation | H `test_cancel_queued_waiting_and_running_runs`; N `test_cancel_waiting_home_run_invalidates_approval`, `test_cancel_running_home_run_discards_late_reply` |
| 12 | Stale fence | H `test_stale_owner_cannot_advance_a_run` |
| 13 | Budget exhaustion | H `test_model_call_budget_fails_run_explicitly`; N `test_endless_home_tool_loop_exhausts_budget` |
| 14 | Restart and crash recovery | H restart tests; N `test_restart_waiting_home_run_completes_after_approval`, `test_crash_after_create_effect_recovers_by_replay_without_duplicate`, `test_crash_with_uncertain_project_creation_is_interrupted_not_retried` |
| 15 | HTTP contract | H `test_http_contract_…`, `test_http_status_when_disabled`; N `test_home_http_conversation_runs_and_scope_checks`, `test_home_http_disabled_returns_503_except_status` |
| 16 | Provider adapter | H `test_openai_compatible_adapter_…`, `test_provider_configuration_…` |
| 17 | No secrets | H `test_secrets_never_reach_persisted_steps_or_events`; N `test_demo_run_stores_no_secrets` |
| 18 | DemoProvider | N `test_demo_names_and_owner_message_extraction`, `test_demo_discussion_creates_nothing`, `test_demo_markov_chain_proposal_approval_and_report`, `test_demo_reports_denial_and_conflict` |

Backend fixes made in 4B, each with a regression test that fails on the pre-fix code:

1. §8 — `GET /agent/home/conversation` and `POST /agent/home/conversations` returned
   200 and created a conversation while agents were disabled; they now return 503
   (`api/src/agents/router.py`). Test: `test_home_http_disabled_returns_503_except_status`.
2. §6/§2.2 — `bind_create` read only the first 100 directory entries, so a collision
   later in the listing produced a wrong "Create project folder" preview; it now pages
   through the directory, treats a letter-case-only difference as a conflict and maps
   workspace errors to tool errors (`api/src/agents/tools.py`). Test:
   `test_collision_beyond_first_listing_page_and_by_letter_case_is_detected`.
3. §5 — the placeholder `DemoProvider` was replaced by the deterministic provider.

## Frontend coverage

`web/src/AgentRun.test.tsx` (7) and `web/src/HomeCoordinator.test.tsx` (6):
ApprovalCard exact actions; Approve posts exactly `{decision, digest, operation_id}` to
`/api/agent/approvals/<id>` and clears the pending key; a 503 keeps the pending key and
Retry sends the same `operation_id`; a 4xx clears it; a completed panel is collapsed with
no Stop; an active panel has Stop with its explanation; poll cadence and change-only
notifications; HomeCoordinator disabled renders an empty container after exactly one
fetch (`/api/agent/status`); malformed status renders nothing; Send posts the message,
then `/api/agent/runs` with exactly `{conversation_id, message_id, operation_id}`;
Shift+Enter does not send; unknown run-start outcome and Retry with the stored
operation; Open notebook and a single workspace refresh on a newly succeeded creation.
The 94 existing tests pass unchanged; the extra `/api/agent/status` fetch on Home
receives the existing stubs' generic responses, which are not `enabled: true`, so the
panel renders nothing there.

## Browser journeys

`web/playwright.agent.config.ts`: separate from `playwright.config.ts`; disposable root
under the system temp directory (`europa-agent-*`), uvicorn on `127.0.0.1:18400`
serving a fresh build, `EUROPA_AGENT_PROVIDER=demo`, local runtime provider, provider
keys blanked, one worker, zero retries, `gracefulShutdown: SIGTERM`.

`web/browser-agent/home-coordinator.spec.ts` (each journey starts a new Home
conversation; waits are on persisted thread messages, approval cards and API listings):

1. Discussion: Files/Running tabs and the demo label are visible; the reply asks to set
   up a notebook; no approval card; the workspace root listing is unchanged.
2. Create: Markov chain proposal; the card lists both actions; nothing exists before
   approval; Approve posts `{decision, digest, operation_id}`; the reply reads
   "Created Markov Chain/markov-chain.ipynb"; Files shows the **Markov Chain** project
   without a manual refresh; the folder holds only `markov-chain.ipynb` (dotfiles are
   hidden by the listing); Open notebook → `/notebooks/<id>` showing the origin message;
   the wordmark returns Home with the thread intact; reload keeps the thread and Files.
3. Denial: the pending approval card survives a reload (Stop agent visible); Deny
   produces "Nothing was created"; no `Titanic Survival` entry exists via API or Files.

Screenshots (`docs/screenshots/phase4b/`): `discussion-desktop.png`,
`discussion-mobile.png`, `approval-desktop.png`, `approval-mobile.png`,
`created-desktop.png`, `created-mobile.png`, `notebook-origin-desktop.png`,
`denied-desktop.png` (desktop 1360×900, mobile 390×844, full page).

### Service cleanup finding

The first agent browser run passed, but it left one orphaned local `kernel_gateway`
(started by the run from this worktree's Gateway config, cwd in a runtime temp dir).
Cause: Playwright stops `webServer` with SIGKILL by default, so uvicorn never ran its
lifespan shutdown and the Gateway, which runs in its own process group, survived. That
process was stopped. The agent config now sets `gracefulShutdown: { signal: 'SIGTERM' }`;
the next two runs logged "Application shutdown complete" and left no new
`kernel_gateway`/`uvicorn` processes and no listener on port 18400 (process lists
compared before and after). About 240 older `kernel_gateway` processes from other
configurations/checkouts were present before this work and were not touched; the
existing Phase 3 configs probably leak Gateways the same way. That is not changed here
and needs an owner decision.

## Limitations

- Real OpenAI/OpenRouter providers were not exercised.
- The demo provider's Journey B naming differs from the brief (`Revenue/revenue.ipynb`).
- The Phase 3 sessions journey (`browser-dashboard-c/sessions.spec.ts:41`) is
  intermittently failing on this host, including on unmodified `main`.
- On mobile, the "Stop agent" explanation can sit below the fold of the scrollable
  Home thread (`max-height: 46vh`) until the thread is scrolled.
