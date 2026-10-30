# Phase 3F-B — navigation and notebook workspace validation

## Baseline and scope

Branch: `feature/phase3f-b-navigation`. Base: Phase 3F UI refinement commit
`c0a3f01d22a9c08100908e835166f965116a4791` on `feature/ui-refinement`.
The starting working tree was clean. The previous editor combined notebook editing,
project selectors, roadmap controls, and discussion in one screen, with both
sidebars open by default and a dark-green-oriented palette. Its reference
[desktop screenshot](screenshots/phase3f/desktop.png) was inspected before this
change.

## Navigation and components

`web/src/Navigation.tsx` maps `/` to `/notebooks` and manages the browser history
for `/projects`, `/projects/:id`, `/notebooks`, `/notebooks/:id`, and `/activity`.
`web/src/Pages.tsx` owns project browsing/overview, notebook browsing and scoped
activity. The existing notebook controller remains the owner of revision-aware
drafts, saves, execution and polling; `Workspace.tsx` retains roadmap and
conversation persistence in optional contextual panels, rather than exposing
project creation in the editor. `Layout.tsx` retains resizable desktop panels
and modal mobile drawers. Backend `api/src/main.py` explicitly allowlists
production UI routes, not `/api` or unknown routes. Opening a notebook does not
provision a kernel.

Project archive/delete remain logical operations against existing application
records; neither deletes notebook files. Notebook deletion is document-only,
not runtime disposal. `/activity` shows existing project-scoped owner activity,
not an invented global or agent feed. No new persistence system was added.

## Menu, toolbar, and keyboard matrix

| Menu | Implemented actions | Deliberately unavailable or indirect |
|---|---|---|
| File | Save drafts, revision-checked rename, confirmed reload, return to library | New notebook opens the library creation flow; no import/export |
| Edit | Copy, guarded cut/delete, paste with server-generated ID, code/Markdown conversion | No cell undo/redo or merge; conversion clears old execution output |
| View | System/light/dark theme, contextual roadmap/discussion, scroll to execution history, activity context | History is shown as an existing disclosure, not a separate persistence store |
| Run | Selected code cell, sequential Run all, existing per-cell receipt-scoped interrupt | No above/below range run; no synthetic completion on lost HTTP response |
| Kernel | Observed kernel status in workspace status | Restart/shutdown disabled: no verified notebook-scoped control contract |
| Help | Shortcut and saved-state guidance | No unsupported documentation links |

Light is the initial theme; System and Dark are explicit persisted preferences.

The compact toolbar supports code/Markdown insertion, insert above/below,
reorder, copy/cut/paste, type toggle, selected run, Run all, contextual
interrupt, autosave, save and revision display. Roadmap/discussion toggles are
in the adjacent notebook header. Actions that could discard a saved cell are
blocked while drafts or execution make them unsafe; source edits are flushed
before supported document mutations and execution. Clipboard is **in-app cell
source/type only** (not the OS clipboard); duplicated cells receive fresh IDs
from the existing insert endpoint, never stale outputs or execution identity.

Command mode supports Enter (focus cell), A/B (insert above/below), D,D (guarded
delete), M/Y (change type with output-loss confirmation), X/C/V (cut/copy/paste),
and J/K or arrows (selection). CodeMirror edit mode retains Shift+Enter
(execute/advance), Cmd/Ctrl+S (save), and Escape (exit edit focus). Command keys
are not processed in inputs, textareas, selects, dialogs or CodeMirror. No
cell-level undo/redo or Shift+M merge is claimed.

Notebook-scoped restart and shutdown need a future ownership-verified backend
control contract; the observational session endpoint does not reveal a safe
kernel ID. Saved outputs do not checkpoint Python variables.

## Browser evidence

Previous: [Phase 3F desktop](screenshots/phase3f/desktop.png).
The browser fixture creates a disposable project, associated code notebook,
roadmap and owner conversation; it does not invent agent activity. The captured
screenshots include [projects](screenshots/phase3f-b/projects.png),
[project detail](screenshots/phase3f-b/project-detail.png),
[notebook library](screenshots/phase3f-b/notebook-library.png),
[editor without panels](screenshots/phase3f-b/editor-no-panels-light.png),
[roadmap](screenshots/phase3f-b/editor-roadmap-light.png),
[discussion](screenshots/phase3f-b/editor-discussion-light.png),
[both panels](screenshots/phase3f-b/editor-both-light.png), and
[dark editor](screenshots/phase3f-b/editor-both-dark.png). Also captured and
inspected [mobile editor](screenshots/phase3f-b/mobile-editor.png),
[mobile roadmap drawer](screenshots/phase3f-b/mobile-roadmap.png), and
[mobile discussion drawer](screenshots/phase3f-b/mobile-discussion.png).

I inspected the original Phase 3F desktop image and the newly captured editor
with no panels, the roadmap-open view, the both-panels dark view, and the three
browser pages. In the new no-panels view the canvas occupies the available
width rather than sharing it with permanently open project/discussion columns;
projects and notebook navigation are distinct. The selected cell has a narrow
accent rather than a floating card; the dark theme uses slate backgrounds and
readable light text. At laptop width the expanded toolbar wraps when a panel is
open; this remains a discoverability/space trade-off, not proof of universal
zoom accessibility. Mobile and 125/150/200% zoom must be reported separately.
At 390px the mobile editor fits the viewport and the contextual drawers overlay
it; the controls wrap into several rows, and small per-cell actions are not
ideal touch targets. These images do not prove accessibility at actual browser
zoom levels, keyboard screen-reader behavior, or contrast ratios by themselves.

## Validation

The Go bridge command `go test ./...` passed on the macOS host during this
milestone. Host `.venv/bin/python -m pytest -q` reported **246 passed, 103
skipped**; `tests/test_web_contracts.py` reported **10 passed**. The skipped
Python cases require explicit infrastructure opt-ins, and do not validate real
Gateway/containerd operation. Host `npm test` **40 passed**, `npm run build`
passed TypeScript and production Vite compilation, and production Chrome
`npm run test:browser` **17 passed, 1 skipped** (the explicitly gated ARM64 VM
test). The browser run covered routing/refresh/history, panels/theme/mobile,
actual Python execution, receipt recovery, confirmed interruption, autosave,
draft conflicts, sanitized rich output, CSP protections, comments, mutation
retry and an actual backend restart. There were no final-suite failures; earlier
in-progress frontend test failures were fixed before this run. A Docker
frontend test attempt could not launch `tsc` because
the sandbox dependency mount denied execution (`Permission denied`); it is not
a frontend test pass.

The Go checks do not include opt-in real ARM64 containerd+runc runs. This
frontend-focused milestone did not change CNI, forwarding, runtime ownership
or execution protocols.

No actual browser zoom at 125%, 150% or 200% was run. The browser suite checks
mobile and narrow CSS viewports, including 320px, as an approximation only.
No real ARM64 runc, VM or opt-in local Gateway tests were run for this
frontend-oriented milestone; those remain explicit infrastructure validations.

## Accessibility and remaining work

Menus use native disclosures and labelled actions; the toolbar has a labelled
group, focus indicators, disabled states, and tooltips. Desktop sidebars retain
keyboard-resizable separators. Mobile drawers use native modal dialogs with
Escape and focus return; browser tests exercised these paths. Reduced-motion
preferences are respected. An automated screen-reader audit, actual Chrome
zoom at 125/150/200%, and measured WCAG contrast audit were **not** performed.

The only backend extension in this milestone is explicit allowlisting of SPA
page paths. A future notebook-scoped, runtime-ownership-verified restart and
shutdown API (with confirmation and uncertain-outcome reporting) is needed
before those Kernel commands can safely work. Cell-level undo/redo/merge,
OS clipboard integration, import/export, global activity aggregation and
dataset controls were not implemented. No agent, MCP, LLM, upload,
authentication or scheduler features were introduced.
