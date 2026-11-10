# Phase 3H validation — historical H1 checkpoint (incomplete)

**Later D+E release validation:** [final combined-candidate results](phase3h-final-validation.md).
The H1 failures and stopping rules below are preserved historical evidence.

**2026-11-10 reconstruction checkpoint:** the new Jupyter-style Home mission
supersedes the welcome-page design below. See the separate
[Home dashboard validation](phase3h-home-dashboard-validation.md) for the preserved
starting state, Checkpoint A fixture screenshots and current review gate. The
historical failures below remain evidence; this link does not mark H1 or Phase 3H
complete.

**Checkpoint B continuation:** the owner approved A's visual structure and
authorized real filesystem integration. The linked reconstruction report now
includes B's physical notebook adapter, real integration screenshots, 294-pass
default backend run (103 skipped), 86 frontend tests, production build and scoped
Chrome results. B awaits owner review; C and the complete milestone regression
remain outstanding.

**Checkpoint C continuation:** B was approved. The linked reconstruction report
now records persisted Recent integration, the six-state read-only observation
contract, bounded refresh, focused real local-kernel/restart validation and actual
Recent/Running screenshots. C awaits review. The old complete Chrome failure
evidence remains outstanding; no shutdown or agent milestone has begun.

**Status:** H1 implementation candidate requires redesign, repair and review. The owner's later answers require a VS Code-style welcome, real launch-directory browsing and editing, and project descriptors; see [the revised design](phase3h-design.md). H2 runtime controls have not begun. The owner explicitly deferred functional Quit/Logout until **after 3H**, superseding the original H3 application-shutdown completion criterion. Do not call Phase 3H complete or commit this candidate as the completed milestone.

## Starting state and scope

- Branch: `feature/phase3h-workspace-navigation`; starting and current HEAD `40386fb946a80e5252cbf4238d459860005812a9` (`main`, merge of the Phase 3G checkpoint). Initial working tree was clean. No H1 commit has been made.
- The Phase 3G report already recorded an incomplete full Chrome regression (35 passed, 2 failed, 1 skipped). The user authorized proceeding on a new branch; the stopped owner preview was not restarted. Tests used disposable fixtures and a loopback test server.
- H1 introduces a real `/` Home with server-persisted bounded recent notebook opens, project folders as views over existing membership, a guarded Europa link and editor File navigation. Existing notebook URLs and legacy catalog/activity routes remain. Leaving the editor does not stop the runtime; H1 shows **Unknown**, not a guessed stopped/idle state. H2 must provide authoritative runtime observations and explicit kernel shutdown; H3 must investigate instance ownership and application shutdown.

## Persistence and API

Application SQLite schema v4 adds `recent_opens`, one row per notebook, capped at 100. `POST /api/notebooks/{id}/opened` records an explicit successful visit independently of background notebook GET/poll, notebook revisions and receipts; `GET /api/notebooks/recent?limit=20` returns up to 20 present notebook IDs and timestamps. The UI joins these IDs with the document and project catalog. Repeated opens update recency without duplicate rows; deleted files are excluded on read. The backend tests cover upgrades from versions 1–3, restart, duplicate visits, deleted files and unchanged document bytes. Runtime status, runtime shutdown and application shutdown APIs have **not** been added.

## Current checks and evidence

| Check | Result | Limit |
| --- | --- | --- |
| Host Python: `.venv/bin/python -m pytest tests/test_application.py tests/test_notebooks.py -q` | Worker reported 99 passed | Focused backend; not Docker or full Python |
| Host Node 24: `npm test -- --maxWorkers=1` | Exit 0; 76 tests passed (14 files) | Unit only |
| Host Node 24: `npm run build` | Exit 0 | Typecheck and production bundle |
| Targeted Chrome: `npx playwright test browser/phase3h.spec.ts --workers=1 --retries=0 --reporter=line` | Exit 0 on second targeted run; 3 passed | Isolated fixture, not accumulated suite; first run failed due fixture history expectation |
| Complete Chrome: `npx playwright test --workers=1 --retries=0 --reporter=line` | **Exit 1: 28 passed, 12 failed, 1 skipped** | Accumulated catalog; H1 and existing regressions unresolved |
| Docker sandbox | Unavailable (daemon socket failure reported by worker) | Host evidence only |
| Go, ARM64/containerd | Not run | H1 does not touch bridge/runtime |

Node/Chrome logs and exit files: `/private/var/folders/wv/_1967qc55074zj0wy7nnjv0w0000gn/T/opencode/phase3h/`. The complete run's `h1-full-browser.log` is authoritative for that source; an isolated pass does not supersede it. Source inspection and `git diff --check` found no whitespace errors. Historical Phase 3G screenshots regenerated during the complete browser run were restored to their initially clean state; only fixture-based H1 screenshots are retained under `docs/screenshots/phase3h/h1/` (Home desktop/mobile, recent Home, project desktop/mobile, editor, File menu). Captures have been viewed for Home and mobile project; this is not full visual acceptance.

## Confirmed failures and next investigation

1. Two H1 browser cases close an editor to `/` rather than the source project under accumulated-suite latency. `web/src/main.tsx` chooses the parent from an asynchronous project lookup; closing before it resolves takes the Home fallback. Preserve the common leave guard and remember a verified project origin for immediate close. The H1 delayed-recent-POST test also did not show its row before the five-second assertion under the accumulated suite; inspect POST completion and subsequent catalog/recent GET ordering before changing code or timeouts.
2. Several inherited Phase 3G readiness checks observed `API: connecting` until their five-second assertions expired; the execution poll uses REST, not a kernel WebSocket connection. The inherited Markdown move still returned HTTP **409**; compare the request revision with completed save responses before repairing it. Do not weaken revision checks or label this a stale assertion.
3. Further failures include a previously visible project roadmap now hidden by H1's collapsed details, plus other older browser assertions. Review expectations against the new product journey without discarding behavioral coverage.
4. One bounded diagnostic with 110 small notebook fixtures found reconciliation called `_open` 110 times but took about **0.004 s** per recent-query transaction. It does **not** prove reconciliation caused the 3–4-second logged requests on the populated suite. No validation cache or timeout increase was applied. Measure representative populated files and request timing before choosing a performance repair.

H1 is **not ready for acceptance**. The configured three Coder/Reviewer repair cycles were used; stop repair dispatch and obtain the owner's direction before further implementation. Preserve the current data and all failure logs. Do not start H2/H3 or Phase 4 on this candidate.

## Revised-design continuation checkpoint — 2026-11-01

The owner reviewed the provisional interface and provided new visual and storage requirements. The former three-cycle H1 attempt is closed; the revised design is a separate, explicitly documented task with its own three-cycle repair limit, not a successful reclassification of the failed full suite. No old validation result certifies the revised design.

H1-A now exposes **read-only** `GET /api/workspace`, listing real files below a pinned launch root (default process working directory; `EUROPA_WORKSPACE_ROOT` or test injection selects an alternative). H1-B adds **read-only** `GET /api/workspace/text` for bounded UTF-8 content and an observed revision. Symlinks, traversal, hidden names, unsafe/nonregular files and oversized text are rejected. There are **no file/folder creation, save, physical notebook move or project descriptor endpoints yet**; the provisional UI does not display the new file browser. Coder-reported host `.venv/bin/python -m pytest tests/test_workspace_browser.py -q`: 11 passed. Source inspection confirmed only GET routes; this is not evidence for real create/edit or an interactive browser journey.

The planned write slice stopped without shipping endpoints: a filesystem write cannot be treated as atomic with the existing completed-response-only SQLite application operation journal. Intent, exact replay, crash reconciliation and uncertain outcomes need a separate durable design. During the delegated sandbox test, Docker failed to connect to `unix:///Users/vjaiwantx/.docker/run/docker.sock`; coordinator inspection confirmed that socket is absent, sandbox status still `starting`. Stop dependent dispatch until infrastructure is available. No host test was substituted for that failed sandbox test. The worker reverted its unfinished write edits and preserved the initial branch changes. No complete-suite rerun has occurred on this revised candidate.

### Later continuation (supersedes the paused-infrastructure statement above)

The owner restored Docker, and the configured focused sandbox test accepted and ran: baseline `tests/test_workspace_browser.py tests/test_application.py -q`, exit 0, **70 passed** (`.git/bdfl/evidence/114b0f20e6e64dd4888892f4a7a2da5e.json`). The resumed same Coder session added journaled folder/file creation and revision-checked text saves. A reviewer-confirmed `.ipynb` overwrite bypass was corrected before journal intent and covered by lower/uppercase ASGI regressions. The corrected backend candidate passed **80** focused Docker tests, exit 0 (`.git/bdfl/evidence/4df69350dc2c458f9cfbee9a6c764f2b.json`). A reviewer subsequently incorrectly reported that no such regression existed; inspection found `test_text_save_rejects_existing_notebook_without_intent_or_byte_change` in `tests/test_workspace_browser.py`. The claim is disproved, not a new implementation instruction.

The frontend worker added a provisional welcome, physical Browse and text editor. Worker-reported Node 24 unit tests: **85 passed** and `npm run build` passed; no browser journey or current visual acceptance was established. Text-save 409 currently preserves its pending operation and browser draft; a reviewer suggestion to simply discard pending state is **not** a safe repair to an unknown side effect. Physical notebook opening, migration and project-scoped recents remain absent.

The next backend slice implemented real project directories with `.europa-project.json` descriptors and private identity binding (schema v6). `GET /api/workspace/project?path=<relative path>` recognizes only verified bound directories or their descriptor paths; `POST /api/workspace/projects` creates new bound projects. Pending folder creation before inode recording remains honestly uncertain rather than retried destructively. Frozen candidate `2a2b0ac0115eb1b00715efea25f26496e2c3e9799cc5ca4e182be9f0f33719fb`: configured Docker focused suite exit 0, **87 passed**, evidence `.git/bdfl/evidence/59ef829f53b04a13a56640a218bb75a8.json`. Reviewer claimed v6 migration must auto-register pre-v6 project records by filesystem scanning; **disproved as a required fix**: those legacy projects are private SQLite records with no confirmed physical descriptor or workspace location, and owner forbids implicit migration. Existing legacy routes remain available. Source review is not a proof of race-free filesystem behavior; browser regression and external writer stress remain unverified.

**Current next action:** wire the frontend to the new physical project recognition/create contract, without inventing notebook mapping or project recents; then design the ID-to-physical-path notebook seam and explicit legacy moves. A follow-up to the existing frontend Coder session failed with native delegation error `All fibers interrupted without error`; no result was returned and no work is attributed to that call. Check worker/tool availability before resuming; do not silently dispatch a replacement or claim it completed. Keep H1 incomplete and the old 28/12/1 full Chrome failure authoritative until a new complete frozen-candidate run. Branch HEAD remains the uncommitted `40386fb946a80e5252cbf4238d459860005812a9` baseline unless separately verified.
