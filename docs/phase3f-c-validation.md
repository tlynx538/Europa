# Phase 3F-C — Notebook UI reconstruction and validation

## Implementation and provenance

Branch: `feature/phase3f-c-jupyter-ui`. Implementation commit:
`78acd32ab13f022407c7e0c6afcc2e1e03ca2db0`. This report is committed
separately so it can identify the implementation commit without a self-referential
commit hash. Phase 3F-C is **not yet fully validated**: see the browser-suite
intermittency below.

The reference was the public [Notebook 7 source](https://github.com/jupyter/notebook),
especially its `packages/application/src/shell.ts` and
`packages/notebook-extension/src/index.ts`, and the official
[notebook documentation](https://jupyter-notebook.readthedocs.io/en/stable/notebook.html).
The documentation's small blank-notebook image was inspected for title, menu,
toolbar and cell-gutter hierarchy. No Jupyter code, artwork or branding was copied.
That reference image is not an equivalent-size local browser capture; the images
below demonstrate structural resemblance, **not measured visual parity**.

Before this change, `Navigation.tsx` and `Pages.tsx` supplied separate project,
notebook and activity routes; `main.tsx` supplied menus, command shortcuts, code
and Markdown cells; `Layout.tsx`/`Workspace.tsx` supplied optional panels; and
`useNotebook.ts` supplied revision-aware saves, autosave, draft review, receipt
polling, execution and interrupts. The Phase 3E and 3F-B reports describe the
existing safety and persistence behavior. The editor still had multiple prominent
per-cell controls, an oversized toolbar, no dedicated execution gutters, and a
cell-first rather than notebook-document hierarchy.

## Frontend architecture and feature comparison

| Area | Reused | Reconstructed |
|---|---|---|
| Browser | `Navigation.tsx` routes, `Pages.tsx` API mutations | Notebook library search/sort, revision/project metadata, empty states; projects remain a separate view |
| Editor | `useNotebook.ts` document, draft, conflict and receipt controller; `.ipynb` API | Title/save row, File/Edit/View/Run/Kernel/Help menus, compact toolbar, white document canvas, code input/output gutters, rendered Markdown and context cell actions |
| Workspace | `Layout.tsx`, `Workspace.tsx`, project/roadmap/discussion services | Optional roadmap/discussion affordances retained; mobile drawers and dark theme visually coordinated |
| Outputs | `Output.tsx` MIME renderer and `renderSafety.ts` | Inline output presentation beneath code with `Out [n]:` prompt |

Toolbar actions are save, code-cell insert, guarded cut/copy/paste, run, receipt-aware
interrupt, cell-type conversion and autosave toggle. The menu includes sequential
Run all, insert/delete, rename, themes, activity and keyboard guidance. In-app
cell clipboard is not the OS clipboard. Command/edit modes retain A/B, D,D,
X/C/V, M/Y, arrows, Enter/Escape, Shift+Enter, and Ctrl/Cmd+Enter and S; typing
inside CodeMirror, inputs and dialogs is excluded from command shortcuts. There
is no cell-level undo/redo. Restart/shutdown remain unavailable because there is
no safe notebook-scoped, ownership-verified kernel control contract. The Kernel
menu explains that limitation rather than claiming a restart occurred.

## Screenshots and visual assessment

Chrome captures at 1280×720: [populated browser](screenshots/phase3f-c/browser.png),
[code and Markdown editor](screenshots/phase3f-c/editor.png),
[saved table and static plot](screenshots/phase3f-c/table-and-chart.png),
[roadmap open](screenshots/phase3f-c/roadmap.png),
[discussion open](screenshots/phase3f-c/discussion.png), and
[dark editor](screenshots/phase3f-c/dark.png). The
[mobile editor](screenshots/phase3f-c/mobile.png) uses a 390×844 viewport
(full-page image is 390×861). The table/chart screenshot uses a **saved fixture
MIME bundle**, not a freshly executed Python chart; the plot is an inert static
preview. These images were inspected against the official blank-notebook image:
the light title/menu/toolbar rows, centered white document, aligned `In`/`Out`
gutters, cell density and restrained borders resemble a notebook rather than
dashboard cards. Differences include Europa's optional panel buttons, project
links and the denser mobile wrapping. No identical-size running Notebook 7
reference screenshot or quantitative pixel comparison was obtained, so visual
parity is **not asserted**. Actual browser zoom, contrast measurements and
screen-reader audit were not performed.

## Checks and recovery/security evidence

On the macOS host after the implementation commit was prepared:

- `npm test` (from `web/`): **42 passed**; `npm run build`: **passed**
  (`tsc --noEmit` and Vite); `git diff --check`: **passed**.
- `.venv/bin/python -m pytest -q`: **246 passed, 103 skipped**. Skipped tests
  do not validate real containerd or opt-in Gateway infrastructure.
- `go test ./...` (from `containerd-bridge/`): **passed**.
- The implementer's full Chrome browser run reported **20 passed, 1 skipped**
  (opt-in ARM64 VM), including real backend restart. An independent isolated
  restart rerun passed (**1 passed**); a previous attempt exposed a macOS
  `/var` symlink storage-root failure, repaired by canonicalizing the test temp
  directory. A later coordinator full run, `EUROPA_TEST_PORT=18090 npm run
  test:browser`, reported **19 passed, 1 failed, 1 skipped**. The failure was
  in `notebook canvas uses input/output gutters and markdown command/edit modes`:
  after two runs the click to navigate to the library was refused while an
  execution operation was still pending; the test then failed waiting for
  `Sort notebooks`. The UI showed the explicit guard error, not a silent
  navigation. An isolated rerun of that test passed (**1 passed**, port 18091).
  **The most recent full-suite result is therefore not a pass.** Do not count
  the targeted rerun as resolution of the full-suite intermittency.
- Configured Docker sandbox frontend attempt: **blocked**, `tsc: Permission
  denied` in the dependency mount. Host frontend checks are the evidence.
  Bounded ARM64 runc/VM tests were not run; no containerd, CNI or forwarding
  configuration changed.

Existing browser tests exercise actual execution, refresh, revision conflict
recovery, browser drafts, receipt reconnect, receipt-scoped interrupt,
application mutation retry, backend restart and sanitized rich output. Python
protocol tests exercise backend behavior. The change did not modify backend
APIs, runtime ownership checks, `.ipynb` persistence, `useNotebook.ts` recovery
logic, `Output.tsx`/`renderSafety.ts` HTML/SVG sanitization, CSP, same-origin
routes or project/conversation stores. Opening a notebook does not provision
a runtime. Saved outputs are not snapshots of live Python memory.

## Known limitations and next verification

The full browser suite has an unresolved timing-dependent navigation assertion
after consecutive runs. Stabilize the test around the notebook controller's
settled state **without** weakening the pending-operation guard, then rerun
the complete suite before declaring Phase 3F-C complete. Kernel restart needs
a future verified notebook-scoped API; import/export, OS clipboard, cell undo,
and a global agent feed are not provided. The rich plot screenshot is a safe
static renderer, not an interactive plot. Do not infer that skipped ARM64 or
container-backed checks passed, and do not begin Phase 4 from this report.
