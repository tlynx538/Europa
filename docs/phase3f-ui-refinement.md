# Phase 3F — notebook workspace refinement

## Inventory and layout

Phase 3E had a dark full-width bar, notebook/project selectors in separate regions,
heavy cell borders and toolbars, duplicate agent placeholders, and a fixed full-width
operational footer. At narrower desktop widths both sidebars competed with the editor.

The compact header now holds the Europa identity, project and notebook selectors,
save indicator, secondary notebook actions under More, and roadmap/discussion toggles. The center retains CodeMirror,
execution and recovery controls; cell boundaries and toolbar chrome are quieter.
The roadmap panel groups server-provided progress, compact task list with expandable
details, contextual activity, and desktop operational status at its bottom. The
discussion panel shows timestamps, reply links, anchor status and a bottom-anchored
composer. A single operational status control moves to the notebook when the
roadmap is closed or the layout switches to drawers. Its compact state shows API,
kernel availability, save state, and pending or uncertain receipts; expansion gives
the interpreter-checkpoint caution. Cell move/delete actions live in keyboard-
accessible Cell actions details, keeping Run and Interrupt prominent. Light and
dark color-scheme tokens share consistent surface, line, ink and accent roles.
At 1100px and below sidebars become native modal drawers, retaining Escape, focus
containment and focus return. Linked notebook navigation closes a drawer only after
a successful open; declined/failed navigation leaves drafts and the drawer intact.
Linked cells are selected only when present in the opened document.

Persistence, revision checks, autosave review, receipts, safe output rendering,
task progress and APIs are unchanged. Progress values and display flags are read
from the server, not calculated in the frontend. No agent UI or backend changes.

## Fixture screenshots

Before: [desktop](screenshots/phase3e/desktop.png),
[mobile drawer](screenshots/phase3e/mobile.png).

After: [desktop](screenshots/phase3f/desktop.png),
[narrow desktop](screenshots/phase3f/narrow.png),
[mobile notebook](screenshots/phase3f/mobile-notebook.png),
[mobile drawer](screenshots/phase3f/mobile-drawer.png).

Screenshots contain disposable browser-test fixture content, not user notebooks.

## Validation and limits

On the macOS repository host, `npm test` passed 11 frontend tests, `npm run build`
completed type-check and production build, and `npm run test:browser` passed 14
production Chrome browser tests with one explicit VM opt-in skip. Browser checks
include narrow/mobile horizontal overflow, one accessible status control docked
in the desktop roadmap and in the notebook when closed/mobile, cancellation with
retained drafts and drawer, successful linked-cell navigation, and existing recovery, conflict,
execution, thread, output safety and backend-restart cases. Browser tests used a
private disposable root under the approved host temporary directory; the default
macOS `/tmp` symlink is rejected by private-storage checks.
The first mobile full-page screenshot repeated the header at its bottom because
the shell assumed a one-row header height even after the mobile header wrapped.
The root now fills the viewport without imposing that obsolete shell minimum;
the regenerated short-fixture screenshot is exactly 390 × 844 with no repeat.

The configured Docker sandbox ran the Python suite: 240 passed and 103
infrastructure-gated skipped. Its frontend run was blocked by the sandbox mount:
Vite first could not create `.vite-temp` in read-only symlinked node_modules;
after copying dependencies locally, the Rollup native binary could not map an
executable shared-object segment (`ERR_DLOPEN_FAILED`). These are not frontend
test passes. The VM/containerd opt-ins were not run for Phase 3F. No claim is made
for extreme zoom beyond responsive browser viewport coverage or for real VM work.
