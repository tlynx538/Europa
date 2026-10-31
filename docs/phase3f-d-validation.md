# Phase 3F-D — notebook UI recovery

## Latest supervised recovery checkpoint

October 31: bounded agent exercises reproduced a further controller defect:
direct notebook switching could discard visible receipt history after the action
lock released while a receipt remained pending. `useNotebook.open()` now also
checks `active`. The new hook regression failed before the one-line repair and
passed afterward; it verifies retained drafts/receipts and switching after receipt
settlement. On the actual working tree with Node 24.19, all **43 frontend unit
tests** and the **production build** pass. A Node 20.17 attempt failed to start
seven test files; runtime selection was corrected without dependency changes.

See [the supervised agent validation](agent-training-validation.md) for methods,
observed failures and remaining limits. The complete Chrome and milestone
regressions have **not** been rerun on this candidate, and this milestone remains
incomplete. The earlier results below describe the interrupted session's work.

## Scope and decisions

This branch changes the React UI, its browser tests, and visual evidence only; no
backend API, Gateway, kernel ownership, receipt persistence, or output sanitizer
was changed. The bounded menu implementation retains native `<details>` and the
existing toolbar rather than migrating the application to a component framework.
Menus share one open state, support Arrow Up/Down and Home/End among enabled
actions, Escape with trigger focus return, outside-pointer dismissal, and popup
positioning within the viewport. Native select controls retain their own arrow
handling. Keyboard guidance moves to a native modal dialog with Escape dismissal.

The same `canRun`/`runnable` eligibility now gates the Run menu, toolbar, cell
actions, command-mode Ctrl/Cmd+Enter, and CodeMirror Shift+Enter/Ctrl/Cmd+Enter.
Paused/conflicted or pending-receipt states cannot advance the selection or submit
through a shortcut. The controller continues to save drafts before submission,
uses authoritative receipts, and stops Run all when a receipt is non-terminal,
conflicted or failed. No blind execution retries were introduced.

Activity now distinguishes a loaded empty project from one with owner-recorded
history. Revision 2 moves project switching, refresh and roadmap creation into a
Project settings disclosure after the task-first roadmap, with a concise empty
state when no roadmap exists. Progress remains server-derived; runtime status
remains anchored at the bottom. The toolbar keeps save, insert, run, interrupt
and cell type; Edit retains cut/copy/paste, View owns autosave, and Kernel opens
a dedicated information dialog. The dirty-cut explanation lives on the disabled
action's title instead of occupying a menu paragraph.

### Official Notebook 7 and JupyterLab architecture comparison

The [JupyterLab frontend](https://github.com/jupyterlab/jupyterlab/blob/main/packages/application/src/frontend.ts)
inherits Lumino `Application` and provides a widget shell. Its
[notebook widget](https://github.com/jupyterlab/jupyterlab/blob/main/packages/notebook/src/widget.ts)
uses Lumino `PanelLayout`/`Widget`, `NotebookActions` and `INotebookModel`;
the [notebook extension](https://github.com/jupyterlab/jupyterlab/blob/main/packages/notebook-extension/src/index.ts)
registers commands and toolbar components through `ICommandPalette` and
`IToolbarWidgetRegistry`. [Notebook 7's `NotebookShell`](https://github.com/jupyter/notebook/blob/main/packages/application/src/shell.ts)
extends a Lumino `Widget` and arranges top, menu, main and side widget areas.
Europa instead uses React `Navigation`/`Layout`/`Workspace` and a CodeMirror
editor with a separate `useNotebook` controller for revision-checked drafts,
receipt polling, explicit conflict review and receipt-scoped interruption.
Integrating Jupyter's shell/widget or notebook model wholesale would require a
Lumino/React lifecycle bridge, command registry, notebook-model adapter and a
review of how those actions interact with Europa's draft and receipt contracts;
it is not equivalent to swapping a menu. Both upstream projects carry
[modified BSD/three-clause terms](https://github.com/jupyterlab/jupyterlab/blob/main/LICENSE)
([Notebook license](https://github.com/jupyter/notebook/blob/main/LICENSE)):
redistribution requires preserving notices and disclaimers; upstream names
cannot imply endorsement. No upstream code, artwork or branding was copied.
The visual hierarchy references their public designs only. A future migration
would require dependency/license inventory and explicit recovery compatibility
tests before adopting widget or command plumbing.

### Baseline failures and diagnosis

The coordinator's baseline Chrome run on port 18117 exited 1: **18 passed, 2
failed, 1 ARM64 opt-in skipped**. The error contexts in `web/test-results/` showed:

- `phase3fb.spec.ts:188`: output gutter `Out [2]` appeared before the notebook
  controller completed its operation; navigation then hit the intentional pending
  operation guard. The test now waits for the applied receipt, no pending receipt
  and an enabled run control before leaving. It does not relax the guard.
- `notebook.spec.ts:161`: after the first linked-navigation prompt was dismissed,
  `Navigation.failedOpen()` still checked the target and held `navigating.current`.
  The next click could be ignored before the first attempt settled. The test now
  waits for the visible cancellation alert and verifies one switch confirmation
  on the accepted attempt. Source inspection found **no duplicate confirmation
  on notebook-to-notebook links**: `Navigation.beforeLeave` applies only when the
  destination is *not* a notebook; `useNotebook.openOnce` owns the confirmation
  when switching notebooks. Removing either prompt would weaken the corresponding
  draft guard rather than repair this race.

### Menu library feasibility prototype

A disposable temporary Vite prototype (not added to the project lockfile)
installed [Radix Dropdown Menu 2.2.1](https://www.radix-ui.com/primitives/docs/components/dropdown-menu)
and [React Aria Components 1.22.1](https://react-spectrum.adobe.com/react-aria/Menu.html)
with React 19.2.0. Both declared React 19-compatible peers. Vite built the
two-library prototype (435.76 kB JS, 140.77 kB gzip); installed library folders
occupied about 1.9 MB for `@radix-ui` and 10 MB for `react-aria-components`.
Chrome opened and Escape-closed a menu from each library under the existing
[production CSP](../api/src/main.py); no CSP/console errors were observed in that
small prototype. Radix emitted Vite warnings about ignored `use client`
directives, not a build failure. This does **not** establish compatibility for
Europa's six mixed-content menus, the modal drawer, or the full production bundle.
Replacing six menu surfaces and their tests to gain this functionality would
exceed the bounded recovery, so the implementation uses native controls and adds
targeted browser coverage instead. No new production dependency was added.

## Disposable visual comparison

The `phase3f-d/` screenshot fixture uses a private temporary notebook/application
root and loopback services on ports 18118, 18119 and 18131. It contains no owner
notebooks. Captures are **CSS viewport approximations**, not actual Chrome zoom:
1280×720 (~100%), 853×720 after / 854×720 before (~150%), 640×720 (~200%)
and 390×844 mobile. Device
scale factor remained 1. These viewport sizes exercise responsive breakpoints,
but do not establish behavior at actual Chrome 150% or 200% zoom, physical DPI,
or with assistive technology. Observed document scroll widths matched those four
viewport widths in the final captures.

Before images: [100](screenshots/phase3f-d/before-100.png),
[150](screenshots/phase3f-d/before-150.png),
[200](screenshots/phase3f-d/before-200.png),
[mobile](screenshots/phase3f-d/before-100-mobile.png),
[notebook without panels](screenshots/phase3f-d/before-notebook-no-panels.png),
[discussion](screenshots/phase3f-d/before-notebook-discussion.png),
[narrow desktop discussion](screenshots/phase3f-d/before-narrow-desktop-discussion.png),
[projects](screenshots/phase3f-d/before-projects-browser.png),
[project detail](screenshots/phase3f-d/before-project-detail.png),
[empty activity](screenshots/phase3f-d/before-activity-empty.png) and
[persisted activity](screenshots/phase3f-d/before-activity-populated.png).
Individual menu captures: [File](screenshots/phase3f-d/before-menu-file.png),
[Edit](screenshots/phase3f-d/before-menu-edit.png),
[View](screenshots/phase3f-d/before-menu-view.png),
[Run](screenshots/phase3f-d/before-menu-run.png),
[Kernel](screenshots/phase3f-d/before-menu-kernel.png),
[Help](screenshots/phase3f-d/before-menu-help.png).

After images: [100](screenshots/phase3f-d/after-equivalent-100.png),
[150](screenshots/phase3f-d/after-equivalent-150.png),
[200](screenshots/phase3f-d/after-equivalent-200.png),
[mobile](screenshots/phase3f-d/after-mobile-390.png),
[roadmap](screenshots/phase3f-d/after-roadmap.png),
[discussion](screenshots/phase3f-d/after-discussion.png),
[shortcuts dialog](screenshots/phase3f-d/after-shortcuts-dialog.png),
[projects](screenshots/phase3f-d/after-projects.png),
[project detail](screenshots/phase3f-d/after-project-detail.png) and
[activity](screenshots/phase3f-d/after-activity.png).

Visual inspection found the old Help guidance was a long paragraph spilling
from a small anchored popup, Edit consisted mainly of disabled large buttons
before selecting a cell, the empty Activity view rendered a blank strip with no
empty-state explanation, and the narrow desktop screenshot showed stale repeated
header pixels immediately after changing viewport size. Fresh-page after captures
were rendered across animation frames and did not show the repeated header.
At 200%-equivalent width, the notebook toolbar still wraps into two rows and
compact icon-only controls remain a discoverability limitation; the narrower
mobile view needs vertical scrolling to reach longer notebook content.
The layout comparison follows the structural notebook hierarchy described in
[Notebook 7's user guide](https://jupyter-notebook.readthedocs.io/en/stable/notebook.html),
without copying Jupyter branding, source, or imagery.

## Validation and limits

- Host `npm run build` passed TypeScript and Vite; `npm test` passed **42/42**.
- Pre-revision host `EUROPA_TEST_TMP=… EUROPA_TEST_PORT=18130 npm run test:browser`
  passed **23**, skipped **1** opt-in ARM64 VM, failed **0** (4.8 minutes). It included
  real backend restart, receipts, conflict preservation, mobile drawer, menu
  keyboard/focus/viewport, paused shortcuts and activity empty/populated checks.
- Independent reviewer later reported a separate full run on port 18130 with
  **21 passed, 2 failed**: a Shift+Enter execution produced no observed output
  and restart failed to create a usable tempfile. The coordinator's fresh full
  suite on port 18144 passed **23, skipped 1, exit 0** (3.7 minutes).
  Different runs disagree: neither a keymap root cause nor a resolved restart
  environment issue is proven. A receipt-state assertion now distinguishes a
  missing execution receipt from a missing output in the Shift+Enter test;
  it does not retry execution. The restart test uses a canonicalized temporary
  root, but suitability of the reviewer's environment was not established.
- Revision 2 full host suite on port 18148: **23 passed, 2 failed, 1 skipped**.
  The failures exposed a non-exact `Project settings` locator matching empty
  text, and a five-second roadmap refresh assertion while backend requests
  remained active. The locator is now scoped to the disclosure summary and
  the test awaits rendered server progress with a bounded 20-second assertion.
  **Post-correction full-suite validation is pending.**
- `git diff --check` passed. The browser suite rewrote historical Phase 3F-B/C
  screenshot artifacts; only those generated image changes were restored to the
  clean baseline. All new evidence is under `phase3f-d/`.
- The coordinator reported `.venv/bin/python -m pytest -q`: **246 passed,
  103 infrastructure-gated skipped**, and `go test ./...` in `containerd-bridge`:
  **passed**; these are coordinator results, not re-runs by this implementer.
  The configured Docker sandbox test through this worker's
  `poseidon_workspace test` returned `Sandbox configuration changed; end this
  session sandbox before recreating it`. The coordinator's `docker exec`
  forensic check found `/tmp` mounted as **noexec tmpfs**, even though copied
  executables had executable mode bits. Thus the frontend native binary cannot
  be loaded from that mount merely by `chmod`; there is no sandbox frontend
  pass. Workers cannot reconfigure/end the sandbox, and no blanket chmod was
  applied.
- No real ARM64 containerd/VM opt-in, actual browser zoom, screen-reader audit,
  or multi-browser run is claimed here.
