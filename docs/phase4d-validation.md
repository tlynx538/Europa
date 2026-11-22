# Phase 4D validation — PR A (Coding Agent, kickoff, progress)

Branch `feature/phase4d-coding-agent` (base `main` `c83c683`). Behaviour is described in
[phase4d-coding-agent.md](phase4d-coding-agent.md); the owner brief and decisions are in
[agent-prompts/phase4d-coding-agent.md](agent-prompts/phase4d-coding-agent.md).

Environment: macOS, CPython 3.13.0 (`.venv`), Node v24.22.2, Playwright 1.64.1, Google
Chrome 156.0.8078.80. Git worktree `.worktrees/phase4d`. One worker and one heavy job at a
time; no `kernel_gateway` process was alive before or after any recorded run.

## Commits

| Commit | Content |
|---|---|
| `4113459` | Stage 1: `ask_owner`, kickoff runs, demo kickoff/answer branches, Needs-your-input UI, tests |
| `83fd810` | Stage 2: owner assignment API, approval-gated `delegate_task`, deterministic execution ID, own-cell and repair rules, demo coding flow, Assign button and Approve guards, tests |
| `84f1d03` | Stage 3: progress table and verified auto-completion, trusted acceptor path, Re-propose for stale cards, roadmap status UI, PRD test 12, tests |
| (this commit) | This report, the 4D design doc, the saved brief, README and AGENTS.md updates |

The validated candidate is `84f1d03`. This commit changes documentation only.

## Baseline (`c83c683`, before any change)

| Suite | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 386 passed, 105 skipped |
| `.venv/bin/python -m pytest -q --run-local-gateway` | 412 passed, 79 skipped |
| `cd web && npm test` | 115 passed (21 files) |
| `cd web && npm run build` | passes |
| Phase 3 Chrome (`npm run test:browser`) | 56 passed, 1 skipped (2.5 min) |
| Agent browser suite, 3 runs | 6 / 6 / 6 passed |

These equal the brief's expected numbers.

## Results on the candidate `84f1d03`

| Check | Command | Result |
|---|---|---|
| Python, no Gateway | `.venv/bin/python -m pytest -q` | **437 passed, 109 skipped**, exit 0 (+51 tests; +4 skips are the new opt-in Gateway tests) |
| Python, real local Gateway | `.venv/bin/python -m pytest -q --run-local-gateway` | **467 passed, 79 skipped**, exit 0. The 79 skips equal the baseline (containerd tests, Linux required) |
| Frontend unit | `cd web && npm test -- --maxWorkers=1` | **132 passed** (24 files), exit 0 |
| Type check and build | `cd web && npm run build` | passes |
| Agent browser suite | `cd web && EUROPA_CHROME=… npx playwright test -c playwright.agent.config.ts` | three consecutive runs: **8 passed** (38.8 s), **8 passed** (38.1 s), **8 passed** (39.0 s), exit 0; 1 worker, 0 retries |
| Phase 3 Chrome suite | `cd web && EUROPA_CHROME=… npm run test:browser` | see the next section: two failing runs, then two passing runs in interleaved pairs; treated as the existing load flake (owner decision) |

Skipped tests are not counted as validation. The 109 skips without `--run-local-gateway`
are opt-in integration tests (local Gateway or containerd).

### New tests

| File | Tests | Covers |
|---|---|---|
| `tests/test_agent_kickoff.py` | 13 | exactly one kickoff per project create; 3–5 questions plus a pending draft approval; standalone questions only; restart between create and kickoff (twice) still one; denied create no kickoff; REST coordinator-authored trigger 422; `ask_owner` replay and once-per-run; kickoff failure path; pre-4D creations never kicked off |
| `tests/test_agent_coding.py` | 22 | PRD tests 1–11 (full flow, dependencies and second coder, approval-gated delegation and denial, Coding Agent cannot delegate/plan/create, owner edit → conflict for insert and replace, non-agent cell → policy, execution-ID replay 409, lost acknowledgement, restart while executing, repair rule and budget, cancel after insert), REST assign validation and replay, `execution_id` store/service tests, demo flow |
| `tests/test_agent_coding_gateway.py` | 3 (integration) | PRD test 13 on the real Gateway: cancel during a long run, lost acknowledgement, unknown outcome; each settles from the receipt and is never replayed |
| `tests/test_agent_progress.py` | 16 | every progress table row, notes-only completion, code edit without execution (no completion), owner edit between start and completion (no overwrite, event), completion unblocks the next task, REST cannot set `accepted_by='coding_agent'`, operation-ID replay, Re-propose (stale → denial `stale_revision`, exact conflict text, run continues, new approval at the new revision; non-stale → 409; replay idempotent) |
| `tests/test_agent_acceptance_gateway.py` | 1 (integration) | PRD test 12: full app lifespan with the demo provider — Home → kickoff → approve plan → assign task 1 → approve insert → approve run → real `applied` receipt with `row sum = 1.0` → auto-completion (`accepted_by='coding_agent'`, receipt evidence, activity) → restart: everything persists, receipt count unchanged |
| `web/src/KickoffWorkspace.test.tsx` | 7 | badge, placeholder, one-shot scroll/focus, no focus steal from a cell editor |
| `web/src/CodingAgent.test.tsx` | 6 | Assign enabled/disabled reasons and request body; `system/tool` assignment message, its run panel and approval card render; Approve disabled with drafts (`disabled`, `aria-describedby`) and on a stale revision (Re-propose, Deny enabled); other cards unaffected |
| `web/src/Progress.test.tsx` | 4 | status chips, Show approval, ✓ tooltip, Reopen prefill, progress % change on a run-state change |
| `web/browser-agent/kickoff.spec.ts` | 1 | Home → approve → Open notebook → badge, questions, draft card; approve draft; answer → revised plan bound to roadmap revision 1 |
| `web/browser-agent/coding-agent.spec.ts` | 1 | plan approved → Assign task 1 → `system/tool` message and approval card → drafts-blocked Approve → stale card → Re-propose → new card at the new revision → approve insert (owner edit and agent cell both persisted) → approve run → applied receipt with `A: row sum = 1.0` → report → task 1 ✓, overall progress 25 %, task 2 Assign enabled, without reload (desktop and mobile) |

### Edits to existing tests (each extends an exact check; none loosened)

- `tests/test_agent_home.py:81` — the exact Home tool list gains `ask_owner`.
- `tests/test_agent_home.py:123–127` — wait for idle; the new notebook's message count goes
  from 1 to 2, with exact checks of the origin message ID and the kickoff reply.
- `tests/test_agent_home.py:372–374` — wait for idle; count 1 → 2, with an exact check that
  the only extra message is the kickoff reply (the origin message is still not duplicated).
- `tests/agent_support.py` — test support, not an assertion: scripted-provider tests route
  kickoff runs to a separate scripted provider (default reply "Kickoff noted."), so existing
  scripts and `h.provider.calls` counts are unchanged.
- `tests/test_agent_notebook.py` (intended 4C contract change: delegation approval-gated):
  the exact role tool tuple gains `ask_owner` and `delegate_task`; `FORBIDDEN` drops
  `delegate_task`; the test `…has_no_delegation…` becomes `…has_approval_gated_delegation…`
  (`delegate` capability present, tool requires approval, allowed at depth 0 and refused at
  depth 1); the prompt sentence "You cannot assign work…" becomes "You never edit cells or run
  code yourself." plus the `delegate_task` approval sentence, and the assertion
  `'delegate_task' not in system` is **removed** because the prompt now names the tool; the
  isolation test now waits for the delegate approval, checks its exact title, denies it and
  asserts exactly one denied approval and no coding run.
- `tests/test_agent_coding.py:172` (written in Stage 2, changed in Stage 3) — the task after a
  verified run is `('completed', True, 'coding_agent', <agent cell>)` instead of
  `in_progress`.
- `web/src/HomeCoordinator.test.tsx` — asserts the kickoff focus flag is set on Open notebook.
- `web/src/CodingAgent.test.tsx` (Stage 2 → 3) — the revision-mismatch text becomes "Notebook
  changed since this was proposed." (Re-propose decision).
- `web/browser-agent/home-coordinator.spec.ts:107–111` — after Open notebook, asserts the
  badge, the first question and the draft card for the exact PRD sentence.

## Phase 3 Chrome suite: load flake investigation

**Failing runs on `84f1d03`** (fresh disposable workspace each run):

| Run | Result | Failed tests |
|---|---|---|
| 1 (3.2 min) | 54 passed, **2 failed**, 1 skipped | `browser-dashboard-c/sessions.spec.ts:7` (assertion at line 41), `browser-acceptance/journeys.spec.ts:23` project (line 64) |
| 2 (3.9 min, load average 5–8) | 53 passed, **3 failed**, 1 skipped | `sessions.spec.ts:7` (line 41), `journeys.spec.ts:23` catalog (line 64), `journeys.spec.ts:121` (line 139) |

Every failure was a dashboard Running row that did not show `Idle` within the 5 s UI wait;
the page snapshots showed the row as `Starting` (sessions) or `Stopped` (journeys).

**Non-agent diff `c83c683..84f1d03`.** No changes under `api/src/runtime`,
`api/src/workspace` or `api/src/internal` (dashboard session status). `NotebookStore.begin`
and `NotebookService.submit` only gained an optional `execution_id` (`None` keeps the old
path). `Workspace.tsx` changes are roadmap/agent UI only.

**Targeted A/B** (`--repeat-each=3` of the three tests): identical on both commits — the
first iteration of all five test instances passed, iterations 2–3 failed with
`Expected: 201, Received: 409` (fixtures created under fixed names cannot be repeated within
one server). Not discriminating beyond the first iteration.

**Interleaved full-suite pairs** (fresh workspace per run, one run at a time, load 3.1–5.9,
diagnostic reporter saving `GET /api/dashboard/sessions` on each failure):

| Order | Commit | Result |
|---|---|---|
| 1 | baseline `c83c683` | 55 passed, **1 failed** (`sessions.spec.ts:41`, `Imported` row not `Idle`), 1 skipped |
| 2 | 4D `84f1d03` | 56 passed, 1 skipped |
| 3 | baseline `c83c683` | 56 passed, 1 skipped |
| 4 | 4D `84f1d03` | 56 passed, 1 skipped |

The baseline reproduced the symptom; 4D passed both of its interleaved runs. Owner decision:
treat the failures as the existing machine-load flake. It is not limited to
`sessions.spec.ts:41`: **`journeys.spec.ts:64` and `journeys.spec.ts:139` can fail the same
way** under load.

**Capture limitation.** The diagnostic reporter saved only the **first page** of
`/api/dashboard/sessions` (20 items, all `Stopped`, `next_cursor` set); the failing
`Imported` row was on a later page, so its API state at the failure is not recorded.

**4F follow-up.** Dashboard Running tests should wait on the sessions API reaching `Idle` for
the target notebook (bounded polling of the authoritative state) instead of a fixed 5 s UI
wait, and failure capture should page through all sessions.

**Resolved by `fix/dashboard-starting-state` (2026-11-19).** A diagnostic dump at a
reproduced failure showed the API itself reporting `Starting`: the notebook's only owned
kernel had an `applied` receipt on that kernel, runtime and generation, `connections: 0`, and
no busy or active execution, yet the Gateway model kept `execution_state: "starting"` (a
missed status message; not an extra unused kernel). The dashboard now reports such a kernel
as `Idle` ("Owned kernel completed a verified execution; Gateway state is stale") only when a
completed receipt (`applied` or `conflict`, both written after `GatewayClient.execute` saw the
execute reply and the idle status) exists for the same kernel, runtime and generation; new,
busy and actively executing kernels are unchanged (regression test
`tests/test_dashboard.py::test_stale_gateway_starting_after_verified_completed_execution_is_idle`).
The Running tests now wait on the paged sessions API (settled states up to 30 s; `Busy`
confirmed while the cell is still running), save every page on timeout, and refresh with the
dashboard's Refresh button instead of reloading. Three full Phase 3 Chrome runs at load
5.9–8.0 passed (56 passed, 1 skipped each). Remaining: a kernel started only through
`/api/kernel/start` has no receipt and can still show `Starting` if its status messages are
missed.

After each Phase 3 run the regenerated `docs/screenshots/phase3h/` captures (and 4B/4C
captures after agent runs) were restored with `git checkout`.

## Screenshots (`docs/screenshots/phase4d/`)

Inspected by the coordinator: `kickoff-desktop.png` (badge, five purpose-specific questions,
draft card, answer placeholder), `kickoff-revise-desktop.png`, `kickoff-mobile.png`,
`assign-desktop.png`/`-mobile.png`, `insert-approval-desktop.png`/`-mobile.png`,
`approve-enabled.png`, `approve-hover.png` (label readable after the hover fix),
`approve-disabled-drafts.png` (dashed, neutral, reason beside it), `repropose-stale-desktop.png`
("Notebook changed since this was proposed.", Re-propose, Deny, disabled Approve),
`coding-report-desktop.png` (receipt output quoted in the report),
`progress-completed-desktop.png`/`-mobile.png` (task 1 ✓ with Reopen, 25 %, task 2 Assign
enabled). Visual review covered these captures only; no cross-browser checks.

## Deviations

See [phase4d-coding-agent.md § Deviations](phase4d-coding-agent.md#deviations-from-the-brief):
open-questions rule, browser sentence, `web/browser-agent/` folder, Re-propose, owner
assignment run model, the removed 4C assertion, and the unverified-completion row.

## Not validated / open

- Containerd: not run (Linux infrastructure required).
- Real LLM providers: journeys use the deterministic demo provider and scripted providers.
- The "cancelled with evidence → `in_progress`" branch has no dedicated test.
- PR B (project kernel environments and package installer) is not started.
