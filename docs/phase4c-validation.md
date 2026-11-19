# Phase 4C validation — Notebook Coordinator

Branch `feature/phase4c-notebook-coordinator` (base `main` `ad0ca1a`). Behavior and API are
described in [phase4c-notebook-coordinator.md](phase4c-notebook-coordinator.md); the owner
brief and PRD are in [agent-prompts/phase4c-notebook-coordinator.md](agent-prompts/phase4c-notebook-coordinator.md).

Environment: macOS, CPython 3.13.0 (`.venv`), Node v24.22.2, Playwright 1.64.1,
Google Chrome 156.0.8078.80. Run in the git worktree `.worktrees/phase4c`.

## Commits

| Commit | Content |
|---|---|
| `4326c85` | Notebook role without delegation; prompt sentences; `_notebook_roadmap` paging and documentation; backend tests 1–10 |
| `1c378a2` | Demo provider notebook branch; demo tests (case 11) |
| `7bbdc86` | Long task keys kept in revisions, new-key limit and ID-collision rule; notebook context trimmed from the oldest cells; regression tests |
| `29760d3` | Roadmap approval preview, child run panels, tool labels; vitest tests |
| `d017e4d` | Notebook Coordinator integration in `Workspace.tsx`; vitest tests |
| `1c2c80e` | Side-effect-free `GET /health` (owner-requested); tests; README line |
| `5725454` | Agent browser journeys (`web/browser-agent/notebook.spec.ts`), screenshots, Ask Coordinator CSS fix |
| (this commit) | This report, the 4C design doc, README/AGENTS updates, owner brief |

Python sources are unchanged after `1c2c80e`. `5725454` changed only `web/src/agent.css`
(CSS), the browser spec and screenshots. After that CSS change, `npm test`, `npm run build`
and the focused agent pytest trio were re-run (see the table).

## Results

| Check | Command | Result |
|---|---|---|
| Focused agent backend tests (at `7bbdc86`, re-run after the `5725454` CSS change) | `.venv/bin/python -m pytest -q tests/test_agent_harness.py tests/test_agent_home.py tests/test_agent_notebook.py` | **70 passed**, 0 skipped (23 harness + 25 Home + 22 notebook) |
| `/health` tests (at `1c2c80e`) | `.venv/bin/python -m pytest -q tests/test_health.py` | **11 passed** |
| Python suite, no Gateway (at `1c2c80e`) | `.venv/bin/python -m pytest -q` | **386 passed, 105 skipped**; base `ad0ca1a` was 353 / 105 (+22 notebook, +11 health) |
| Frontend unit tests (re-run after the CSS change) | `cd web && npm test` | **115 passed** in 21 files (baseline 107; +4 `AgentRunRoadmap.test.tsx`, +4 `WorkspaceAgent.test.tsx`); no existing test edited |
| Type check and build (re-run after the CSS change) | `cd web && npm run build` | passed (`tsc --noEmit` and Vite build) |
| Agent browser suite (source `5725454`) | `cd web && EUROPA_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npx playwright test -c playwright.agent.config.ts` | three consecutive runs: **6 passed** (25.1 s), **6 passed** (26.8 s), **6 passed** (23.6 s); 3 Phase 4B + 3 Phase 4C journeys; 1 worker, 0 retries |
| Python suite with real local Gateway (coordinator, source `a6af17f`) | `.venv/bin/python -m pytest -q --run-local-gateway -rs` | **412 passed, 79 skipped**, exit 0 (386 + 26 Gateway tests). All 79 skips are containerd tests: "Linux required; macOS host has no containerd/runc/runsc/socket or identified Linux VM" |
| Phase 3 Chrome suite (coordinator, source `a6af17f`) | `cd web && EUROPA_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:browser` | **56 passed, 1 skipped**, exit 0, on the first run (2.4 min). The known intermittent failure `browser-dashboard-c/sessions.spec.ts:41` (see Phase 4B validation) did not occur; no leftover `kernel_gateway` processes were running before or after the run. The suite rewrote committed `docs/screenshots/phase3h/` captures, which were restored with `git checkout` |

Skips: the 105 skipped tests in the plain Python run are opt-in integration tests
("requires --run-local-gateway" or `--run-containerd`). Skipped tests are not counted as
validation.

### Backend test coverage (`tests/test_agent_notebook.py`, 22 tests)

PRD §6 cases:

1. Context: provenance, cell source, plain-text receipt output, earlier message, project,
   "No roadmap yet.", reply author and placement. No receipt runtime, kernel or session IDs.
   The role prompt sentences. Context limits, `omitted_leading_cells`, `agent_authored`,
   roadmap fields and the 12-message history.
2. Plan approval.
3. Revision rejects `status`; task state is preserved.
4. Stale plan conflict.
5. Removed tasks in the preview.
6. Isolation: forbidden tools rejected and not offered; no notebook B content; B's roadmap
   not returned to A.
7. Standalone notebook.
8. Restart with a waiting approval.
9. HTTP scope checks (409/422/422).
10. Cancel invalidates the approval.
11. Demo provider: J1, J2+J3, denial and conflict, J5 status taken only from `read_roadmap`
    (verified by changing the roadmap between context assembly and the tool call), J6. The
    4B demo tests still pass.

Regression tests, each failing on the code before its fix:

- `_notebook_roadmap` beyond the first 100 project roadmaps (`4326c85`).
- A 40-character agent key kept in a later revision.
- A generated-ID collision.
- Newest cell kept with oldest-first trimming.
- An oversized single cell (`7bbdc86`).

### Agent browser journeys (`web/browser-agent/notebook.spec.ts`)

The project and notebook are set up through `/api/workspace/projects` and
`/api/workspace/notebooks`, each journey in its own folder. Waits are on persisted state
(messages, approval cards, run panel summaries and `/api/roadmaps`), with no sleeps,
forced clicks or retries.

- **J1 + J2 + J7 + J3 + J5 (one test).**
  - Discussion reply.
  - Create card with 4 tasks and dependency text.
  - Mobile 390×844: `scrollWidth ≤ clientWidth` for the document and `.notebook-editor`;
    the Approve button bottom ≤ composer-shell top after scrolling it to the end.
  - The pending approval survives reload and a Home round trip.
  - Approval: Roadmap panel shows 4 *upcoming* at 0 % without reload; API revision 1, all
    `pending`.
  - Revise card "Bound to roadmap revision 1" with 5 tasks; approval gives revision 2 with
    the first four IDs unchanged.
  - Status reply.
  - After reload, all four panels are collapsed with "Completed · N actions".
- **J4.** Owner `PUT` during a pending revision, then approve: conflict reply; API objective
  is the owner's, revision 2.
- **J6.** Standalone notebook: refusal, then discussion.

Screenshots in `docs/screenshots/phase4c/` (checked by eye):
`plan-approval-{desktop,mobile}`, `plan-saved-desktop`, `revise-approval-{desktop,mobile}`,
`completed-{desktop,mobile}`, `conflict-desktop`, `standalone-desktop`.

### Defects found in this stage

- **Ask Coordinator checkbox floated over the stream (app, CSS).** The first screenshots
  showed the checkbox label with a transparent background over the message stream (over the
  approval card on desktop, over "Stop agent" on mobile). The label and the start alerts now
  have an opaque surface (`web/src/agent.css`). Afterwards `npm test` (115), `npm run build`
  and the focused pytest trio (70) passed.
- **Mobile reachability check timing (test only).** One run of a superseded series failed
  the "Approve above composer" assertion (button bottom 681 px vs composer top 634 px).
  Cause: `waitForFunction` polls in `requestAnimationFrame`, which runs before
  ResizeObserver in a frame. The first frame at 390 px therefore still had the desktop
  `--composer-reserve` scroll padding (a gap of about 115 px against a 161 px composer), and
  the immediate `scrollIntoView` used the stale value. Fix: wait until the column's
  `--composer-reserve` equals the composer-shell height, then scroll. The three runs in the
  table above are a fresh series after this fix.
- **Screenshot framing (test only).** The stream scrolls inside `.notebook-editor`, so the
  capture helper scrolls a focus element (card button or last reply) into view first.

### Cleanup verification

Before the first run: nothing listening on 18400, 0 `uvicorn main:app`, 0 `kernel_gateway`
processes. After each run: nothing on 18400, 0 uvicorn, 0 `kernel_gateway` (unchanged).
`docs/screenshots/phase3h` was not modified. The 4B spec rewrites
`docs/screenshots/phase4b/*.png` on every run; those files were restored with
`git checkout` so that the 4C commits contain only 4C captures. The agent config's
disposable `europa-agent-*` roots under the system temp directory are created per run, as
in 4B.

## Deviations

- **Demo planning pattern.** `\b(plan|roadmap|steps?|break down)` instead of `steps`, so
  that J3 "Add a step…" matches (accepted by the coordinator).
- **Demo revision.** When every template task already exists, the demo appends one task
  titled from the owner's message (for example "Compare with a 3-state chain").
- **"Show cell".** The button on cell-linked tool calls (`onRevealCell`) is groundwork for
  4D; 4C tools produce no cell links.
- **Run panel wrapper.** Panels are wrapped in an `.agent-stream` element after the user
  message.
- **First load.** The first load of runs does not reload messages or the roadmap, because
  they are already loading.
- **Blocked run start.** A run start is refused while an earlier start in the same
  conversation has an unknown outcome; the alert offers Retry of that earlier start. The
  new message itself is posted.
- **`GET /health`.** Added at the owner's request, although PRD §4.5 otherwise asked for no
  API changes. `GET /api/health` is unchanged.
- **Ask Coordinator surface.** The label and start alerts have an opaque surface because the
  composer floats over the stream (found from the screenshots).
- **Browser spec layout.** J1, J2, J3, J5 and J7 (reload and Home round trip) run in one
  sequential test because they build on the same roadmap. J7 server restart is covered by
  backend test 8, not by the browser suite.
- **Owner brief.** The brief's "Owner decisions" bullet for `/health` was updated to record
  the `/api/health`-unchanged decision conveyed by the coordinator.
