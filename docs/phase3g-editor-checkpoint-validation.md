# Phase 3G — Jupyter-native notebook UX validation

**Historical editor/settings checkpoint: `3d4a771`.** Date: 2026-10-31.

## Starting point and scope

Branch: `feature/phase3g-notebook-ux`. Starting checkpoint:
`5dcb96425ee38dfeec4a3ab58e3c5a3453b1e9de`, preserving the inherited Phase 3F-D
working tree from `53aaee1`. Git status, HEAD, diff and the complete 3F-D report
were inspected before implementation. Root `AGENTS.md` is preserved.

Before extensive changes, the complete host Chrome baseline passed **25 tests**,
with **one ARM64 opt-in skip** and zero failures, using one worker and no retries.
The previously reported full-suite failure did not reproduce in that baseline;
this does not establish that every historical intermittent failure was eliminated.
Results below apply to the editor/settings checkpoint, before the integrated conversation update.

React 19, TypeScript, CodeMirror, `useNotebook`, `.ipynb` persistence, receipt
lifecycle and backend APIs are retained. No JupyterLab/Lumino migration, Phase 4
agents, runtime/containerd/CNI changes or new backend settings were introduced.
The [approved brief](agent-prompts/phase3g-brief.md) and
[design decisions](phase3g-design.md) include the owner's subsequent UI feedback.

## Actual Jupyter reference

The reference is **Notebook 7.6.4**, running with authentication on loopback in a
separate disposable environment. Chrome captured the actual editor. The official
[UI anatomy](https://jupyter-notebook.readthedocs.io/en/stable/ui_components.html)
provided terminology; no Jupyter branding, source or frontend assets were imported.

Both editors used the same five representative cells: Markdown, computed stdout
and an expression result, a three-row HTML table, a genuine Python/IPython static
SVG plot, and a deliberate Python traceback. Outputs were actually executed.
The separate one-cell notebook is unexecuted. The chart is SVG, not matplotlib;
no pandas/matplotlib dependency or invented execution result is claimed.

Viewports: 1440×900, 1280×800 and 390×844; normal browser zoom, device scale 1.
Notebook 7 measured 42px title, 28px menu, 32px toolbar, 64px prompt gutter and
13px/18.2px code. Its single-cell paper fits its content. An unsigned reference
SVG initially showed fallback content; only the trusted disposable fixture was
signed in a private NotebookNotary store before recapture. No global trust rule
was relaxed. The reference server and kernel were stopped after capture.

## Defects, root causes and repairs

| Defect | Cause and resulting behavior |
| --- | --- |
| A small notebook occupied a tall empty white box | `.notebook-surface` forced `min-height:65vh`, giving 585px at a 900px viewport. Paper now follows content; the single-cell capture occupies about 71px, with reading width independent of full-width chrome. |
| Weak cell/output rhythm | Metadata bands, output borders and default text margins consumed space. Compact gutters, cell padding and output rows now let code and outputs flow together. Routine applied/ok metadata remains accessible; draft, error, historical, conflict and uncertain states remain available. |
| Stdout, tables and errors received false result prompts | One outer `Out` wrapper labelled all outputs. Individual output rows now give `Out [n]:` only to `execute_result`, using that output's own count. Streams/display data/errors retain empty result gutters. Unknown counts are not inferred from a newer cell execution. |
| Centered navigation and roadmap-docked status | Header alignment and status portal ownership followed the notebook paper/sidebar. Layout now owns fixed viewport-wide chrome, one global status footer and measured header/footer offsets. The notebook scrolls between them. |
| Oversized floating button stacks | Hand-built top menus lacked mature menu behavior. Radix provides compact semantic rows, separators, checkbox/radio items, a theme submenu, keyboard navigation and collision-aware portals. |
| Inconvenient notebook/project exit | File now exposes Open project, Open notebook and Close notebook through existing guarded navigation. Open project leads to a project and its linked notebooks. Standalone Projects/Notebooks title links were removed after owner review. |
| Excess header spacing | Nested row gaps and mismatched control sizing inflated the header. Measured final desktop rows are 36px title, 32px menu and 34px toolbar, plus border: **103px total**. Text aligns at 14px on desktop and 12px on narrow screens; mobile controls wrap. |
| Project administration dominated the roadmap | File → Project settings opens one dialog over a dark, blurred gradient backdrop. Project context, notebook membership information, planning and history are grouped; rename/archive/membership management uses the existing project page through guarded navigation. Empty roadmap actions open the same dialog. |
| Raw cell IDs and sparse activity presentation | Discussion uses readable cell references and timestamps, a scrollable history and bottom composer. Cross-notebook references avoid falsely claiming a valid foreign cell is missing. Activity renders persisted records as a dated timeline, with result labels and available references; empty states link to useful project context. |

New real-browser scenes exposed editor selection/focus problems after scrolling
past isolated outputs. An internal notebook scroll viewport and explicit reveal
of keyboard/scene targets replaced window-offset assumptions. The real pointer
click still must enter edit mode. Intrusive focus/scroll tracing is opt-in.
The exact original native iframe/click mechanism was not established; these
checks do not prove behavior for every arbitrarily obscured click or browser.

A responsive assertion initially sampled geometry before ResizeObserver had
measured the wrapped footer. It now polls the actual editor/footer relationship
instead of using a delay. Other diagnosed test repairs retained behavior:
updated menu roles; stdout versus result prompt expectations; an orphaned badge's
visible “Missing cell” label while retaining its orphaned class, disabled target
and persisted history; closing a mobile modal before clicking the background
menubar; and checking visible project metadata instead of a hidden option.

The settings test also exposed a real Tab-boundary focus escape. Explicit
Tab/Shift+Tab wrapping keeps keyboard focus within its controls. Backdrop
handling distinguishes outside coordinates from the dialog's own padding;
inside-padding clicks remain open. Escape, Close and backdrop dismissal restore the visible invoking control,
with File as fallback when the opener is no longer available. Mobile opening from Roadmap closes that drawer before showing the
settings modal, avoiding stacked competing modal layers. Cancelled project
navigation leaves the dialog and browser drafts intact.

## Architecture and changed files

| Files | Responsibility |
| --- | --- |
| `web/src/main.tsx`, `EditorIcon.tsx` | Single concise command toolbar, original SVG icons, File/Edit/View/Run/Kernel/Help, guarded File navigation and settings entry. Existing execution handlers and eligibility remain. |
| `web/src/Layout.tsx`, `style.css` | Global header/status slots, measured viewport bounds, optional panels, settings-open coordination, coherent light/dark/focus/status tokens and responsive spacing. |
| `web/src/Output.tsx`, `Output.test.tsx` | Output-specific result prompts and bounded presentation. MIME selection/order, sanitizer, empty iframe sandbox, referrer policy and CSP remain unchanged. |
| `web/src/Workspace.tsx`, `Workspace.test.tsx` | Task-first roadmap, shared settings dialog, discussion/history/composer, readable references and existing project/roadmap/comment actions. |
| `web/src/Pages.tsx`, `Pages.test.tsx`, `navigation.css`, `ActionMenu.tsx` | Focused browser lists/breadcrumbs, separated row actions, real Activity timestamps/status/references and useful empty navigation. Confirmation and draft-deletion protections remain. |
| `web/package.json`, `package-lock.json` | Pinned `@radix-ui/react-menubar` 1.2.1 and `@radix-ui/react-dropdown-menu` 2.2.1, with their locked transitive dependencies. |
| `web/browser/*.spec.ts`, `fixtures/phase3g.json` | Existing regressions preserved with current semantic selectors; genuine execution scenes, project/notebook flow, settings modal and pending-execution navigation. |
| `README.md`, `RELEASE_NOTES.md`, `docs/agent-workflow.md`, Phase 3G docs/screenshots | Current behavior, validation evidence, reference comparisons and reusable repository lessons. |

No Python/Go implementation, `useNotebook`, navigation controller, output
sanitizer, runtime configuration or CSP source changed during Phase 3G.

## Menu-library feasibility and accessibility

The installed primitives declare React 19 compatibility and compile/run with
Europa's React 19 frontend. Actual Chrome checks cover single-open top menus,
arrow/Enter navigation, Escape/outside dismissal, focus return, visible/semantic
disabled state, submenu/theme behavior, CodeMirror command/edit interaction,
viewport bounds and mobile native dialogs. Portals coexist with the existing CSP;
no policy was loosened. The new settings dialog uses the browser top layer,
labelled controls, keyboard wrapping and focus return.

The integration has a measurable bundle cost: shared libraries grew from
158.31 to 255.85 kB raw, **51.89 to 84.10 kB gzip** (about 32.2 kB gzip added).
Final build sizes are 21.05 kB CSS (5.22 kB gzip), 78.82 kB application JS (23.37 kB gzip), 222.26 kB React (69.19 kB gzip), 255.85 kB shared libraries (84.10 kB gzip), and 467.50 kB editor (156.04 kB gzip). The primitives improve behavior
without replacing the frontend; their cost is accepted as a bounded dependency.
Native cell-action disclosures remain; the menu primitives cover the top menubar
and project/notebook row actions.

These are browser/DOM and keyboard observations, not a screen-reader conformance
claim. Manual assistive-technology testing, Safari/Firefox and non-default browser
zoom were not performed. Narrow layouts were tested at the stated dimensions;
“zoom-sized viewport” test names do not establish actual browser zoom testing.

## Before, reference and after screenshots

All links below are real captures. There are 11 reference, 29 before and 31 checkpoint-after
PNGs. Reference scenes use the same trusted fixture; Europa-only panels and
browsers have no Notebook 7 counterpart. The settings overlay was added during
owner review and therefore has no pre-existing modal reference.

| Scene | Notebook 7 | Europa before | Europa after |
| --- | --- | --- | --- |
| Single cell, 1440 | [Reference](screenshots/phase3g/reference/one-cell-1440.png) | [Before](screenshots/phase3g/before/one-cell-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/one-cell-1440x900.png) |
| Several executed cells | [Reference](screenshots/phase3g/reference/representative-overview-1440.png) | [Before](screenshots/phase3g/before/overview-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/overview-1440x900.png) |
| 1280px document | [Reference](screenshots/phase3g/reference/representative-overview-1280.png) | [Before](screenshots/phase3g/before/overview-1280x800.png) | [After](screenshots/phase3g/checkpoint-editor/overview-1280x800.png) |
| Markdown/code | [Reference](screenshots/phase3g/reference/markdown-code-1440.png) | [Before](screenshots/phase3g/before/markdown-code-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/markdown-code-1440x900.png) |
| Table | [Reference](screenshots/phase3g/reference/results-table-1440.png) | [Before](screenshots/phase3g/before/table-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/table-1440x900.png) |
| SVG plot | [Reference](screenshots/phase3g/reference/line-chart-1440.png) | [Before](screenshots/phase3g/before/plot-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/plot-1440x900.png) |
| Traceback | [Reference](screenshots/phase3g/reference/value-error-traceback-1440.png) | [Before](screenshots/phase3g/before/traceback-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/traceback-1440x900.png) |
| Command selection | [Reference](screenshots/phase3g/reference/selected-command-cell-1440.png) | [Before](screenshots/phase3g/before/selected-command-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/selected-command-1440x900.png) |
| Edit focus | [Reference](screenshots/phase3g/reference/focused-edit-cell-1440.png) | [Before](screenshots/phase3g/before/focused-edit-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/focused-edit-1440x900.png) |
| File menu | — | [Before](screenshots/phase3g/before/menu-file-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/menu-file-1440x900.png) |
| Roadmap | — | [Before](screenshots/phase3g/before/roadmap-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/roadmap-1440x900.png) |
| Discussion | — | [Before](screenshots/phase3g/before/discussion-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/discussion-1440x900.png) |
| Project browser | — | [Before](screenshots/phase3g/before/projects-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/projects-1440x900.png) |
| Notebook library | — | [Before](screenshots/phase3g/before/notebook-library-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/notebook-library-1440x900.png) |
| Activity empty | — | [Before](screenshots/phase3g/before/activity-empty-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/activity-empty-1440x900.png) |
| Activity populated | — | [Before](screenshots/phase3g/before/activity-populated-1440x900.png) | [After](screenshots/phase3g/checkpoint-editor/activity-populated-1440x900.png) |
| Narrow notebook | [Reference](screenshots/phase3g/reference/representative-narrow-390.png) | [Before](screenshots/phase3g/before/notebook-390x844.png) | [After](screenshots/phase3g/checkpoint-editor/notebook-390x844.png) |
| Settings overlay | — | — | [Desktop](screenshots/phase3g/checkpoint-editor/settings-1440x900.png), [mobile](screenshots/phase3g/checkpoint-editor/settings-390x844.png) |

Inspection found content-sized paper, prompt/output alignment, restrained cell
boundaries, readable table/plot/traceback scenes and distinct command/edit states.
The settings overlay and narrow drawers fit their viewports; the footer spans the
screen independently of panels. Notebook 7 remains a reference for hierarchy and
interaction quality, not a claim of pixel parity. Separately named full-page
supplements can equal viewport images because the notebook now scrolls internally;
they are not evidence of the whole document fitting on one screen.

## Regression results on the editor/settings checkpoint

Actual host versions: Node 24.19.0, npm 11.17.0, CPython 3.13.0, Go 1.27.2
darwin/arm64 and Google Chrome 154.0.8037.93.

| Check | Invocation / candidate evidence | Result |
| --- | --- | --- |
| Frontend unit | `cd web && npm test -- --maxWorkers=1`; validated editor/settings source | **47 passed**, 9 files, exit 0 |
| TypeScript and production build | `npm run build`, executed inside the final complete browser command | **Passed**, exit 0 |
| Complete Chrome | `cd web && npm run test:browser -- --workers=1 --retries=0`; isolated loopback test server, capture prefix `after` | **31 passed, 1 ARM64 opt-in skipped, 0 failed**, exit 0 |
| Python regression | `.venv/bin/python -m pytest -q` | **246 passed, 103 skipped**, exit 0; reused after subsequent frontend-only edits, backend unchanged |
| Go | `cd containerd-bridge && go test ./...` | **Passed (cached)**, exit 0; Go source unchanged |

The final unit and complete Chrome runs use the same frozen source. Before/after
maps cover 37 frontend source/test/package/config files and match byte-for-byte;
recorded source digest is
`17ad5a516d1678ae6304f8769b5b99bd8d839ccef82e83b39a70b92433b6ac40`.
Local full logs and exit files are `acceptance-{unit,browser}.{log,exit}`;
unchanged-backend results are `final-{python,go}.{log,exit}`, under the disposable
Phase 3G validation directory. Earlier passing candidate results are not
substituted for these final frontend checks.

The real browser workflow creates a notebook through the UI, enters and executes
code, inserts Markdown, reorders cells, saves/reopens, reviews a traceback, toggles
panels and returns through File. Another workflow opens a project and its linked
notebook. A real running receipt is held by an explicit release-file condition
with a hard deadline; Close notebook and Open project are refused until the
receipt is applied **and** the UI operation has settled. The gate is released in
cleanup. No blind UI sleeps, forced clicks, automatic test retries or receipt
lifecycle overrides were introduced.

Rich-output/CSP isolation, revisions/drafts/conflict review, reconnect, historical
output provenance, exact application-operation replay, interrupt behavior and
actual backend restart remain in the complete Chrome suite. Earlier full runs
failed on the old orphaned-label expectation and then settings test setup/focus;
those failures were diagnosed and retained as intermediate evidence. They are
not counted as final passes.

Historical 3F-B/C screenshots are restored individually from the pre-run snapshot
and checked by SHA256; no broad screenshot restore discards new evidence or
inherited work. The background preview on loopback port 8000 remains running
with separate disposable data; browser validation services use other ports and
are stopped afterward.

## Supervision, review and limits

OpenCode agents implemented and tested the changes under bounded sequential
assignments. The Maintainer approved the initial architecture with explicit
provenance/focus/security constraints. A Researcher failed to produce a useful
handoff within budget and was stopped. Several coding turns hit tool budgets;
verified logs were retained and remaining work received explicit follow-ups,
rather than treating an interrupted handoff as a successful check.

One diagnostic Vitest invocation selected the entire suite with default workers;
subsequent checks explicitly use one worker. Durable repository guidance now
covers correct test filtering, responsive geometry waits, semantic menu selectors
and keeping corrective logs/scopes distinct. This is instruction/workflow tuning,
not model-weight training or proof of the original macOS exhaustion's root cause.

The independent Maintainer review requested opener-specific focus return for settings.
That finding was corrected and covered by real Chrome assertions for File and
Roadmap origins. The reviewer made no edits or test runs and could not read the
external temporary logs; the primary supervisor verified actual logs, current
source hashes and historical screenshot hashes directly.

Docker and ARM64 runc/containerd infrastructure were not exercised. The ARM64
browser case and 103 Python cases remain explicit skips, not passes. Go reported
cached results. No permission broadening, blanket chmod, daemon/network change or
Docker workaround was used. The earlier 3F-D noexec-mount diagnosis remains
historical evidence, not a fresh Docker reproduction in this milestone.

Project settings expose supported controls and existing project-management flows;
no new backend preferences are claimed. Activity cell references remain metadata
alongside supported notebook navigation; no unsupported cell URL route was added.
Manual assistive-technology, cross-browser and non-default zoom coverage remain
limitations. Phase 4 work is outside this milestone.

## Owner-directed integrated conversation update

After this clean checkpoint, the owner clarified that conversation belongs in
the notebook canvas, with a fixed floating composer above the footer and explicit
cell-reference chips. The left roadmap should resemble an ordered chapter/progress
timeline. The owner confirmed **integrated UI now; agent engine next**.

The [updated brief](agent-prompts/phase3g-conversation-canvas.md) supersedes the
right-sidebar conversation presentation. Checkpoint screenshots are preserved in
`checkpoint-editor/`; new integrated screenshots and regression results must be
recorded before claiming Phase 3G complete. Existing results above validate this
checkpoint, not the upcoming integrated conversation implementation.
