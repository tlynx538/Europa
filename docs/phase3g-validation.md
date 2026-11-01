# Phase 3G — Notebook UX validation

**Status: integrated notebook conversation and visual refinement in validation; milestone not yet complete.** Updated 2026-10-31.

Branch: `feature/phase3g-notebook-ux`. The inherited Phase 3F-D baseline is
`5dcb964`; the reviewed editor/settings checkpoint is `3d4a771`. Root `AGENTS.md`
and unrelated work are preserved. See the [editor checkpoint report](phase3g-editor-checkpoint-validation.md)
for the original reference captures, before/after comparisons, menu-library feasibility,
Docker limitation and its completed regression results. Those results do not certify
subsequent integrated-conversation changes.

## Current architecture and scope

React 19, CodeMirror, `useNotebook`, standard `.ipynb` files, authoritative execution
receipts, revision checks, recovery and interruption remain. There is no
JupyterLab/Lumino migration or agent engine. The owner approved the integrated
conversation UI now and deferred orchestration, generated plans/cells and execution
by agents to the next milestone.
The owner's turn structure is recorded in
[Notebook agent interaction](notebook-agent-interaction.md): user message,
collapsible real progress/tool activity, agent reply, then affected cells/outputs,
with navigation to an existing target cell. This remains an engine integration
contract, not simulated UI activity or a claimed working agent.

Each notebook owns one durable canonical conversation, initialized on creation and
resumed by an idempotent GET. Standalone notebooks work independently of project
membership. Legacy conversations retain their records and separate read-only
presentation; they are not merged into the canonical thread. See
[the persistence contract](conversational-notebooks.md),
[conversation design](phase3g-conversation-design.md) and
[lifecycle decision](phase3g-notebook-conversation-lifecycle.md).

SQLite schema v3 includes a unique notebook/conversation mapping and a separate
placement map for older active messages. The v1 migration
preserves original records, IDs, sequences, timestamps, versions and operations,
reinstates record triggers/indexes and verifies foreign keys. Notebook file creation
and SQLite initialization are separate stores: if initialization fails after save,
the structured 503 returns the saved notebook ID for recovery without a second
creation POST. This is tested with injected application failure, not physical
SQLite corruption.

The notebook canvas displays persisted messages and linked cell context with a
floating composer above the full-width footer. No manual conversation creation or
selector remains. View controls the optional left roadmap outline; header Roadmap
and Discussion buttons are removed. The outline follows actual stored task order
and progress. It does not simulate agent activity.

## Current evidence

The candidate now removes the separate Discussion section and adds visible
per-cell move arrows. New canonical messages receive a server-captured placement
after the current last saved cell, independently of explicit cell context. A
message sent before any cells precedes later cells. This supports a saved
message → cell → message stream without inventing cell timestamps or executing
code. Older active records receive a one-time current-layout snapshot in the
separate placement map. This is not recovered historical chronology. Their raw
message content, IDs, authors and timestamps remain immutable. Deleted anchors
are retained in a labelled fallback.

**Owner-reproduced defect:** an older active canonical message without placement
remained in the tail fallback, so each subsequently added cell appeared above it.
The toolbar also inserted after the previously selected cell. The source now
adopts stable positions for older active messages and makes the toolbar append;
explicit relative insert commands remain relative. Compact time-only metadata
keeps the original machine timestamp and full tooltip. Unit/build and Python
checks pass; the authored toolbar Chrome regression still needs execution.

| Check | Observed result | Scope |
| --- | --- | --- |
| Canonical Python targeted regression | 106 passed | Application, notebook and web contracts, including layout adoption |
| Complete Python regression | 254 passed, 103 skipped | Canonical backend, placement and adoption; infrastructure skips are not passes |
| Frontend unit tests | 65 passed | Stream, menu and inline-comment candidate |
| TypeScript / production build | Passed | Same inline-comment candidate |
| Complete Chrome | Latest complete run: 35 passed, 2 failed, 1 skipped | Before the subsequent comment sizing refinement; readiness and cell-move failures remain under investigation |
| Go | Passed, cached | Unchanged bridge source; editor checkpoint evidence |

Local logs are in `/private/tmp/europa-phase3g`: `canonical-targeted.log`,
`canonical-python.log`, and `canonical-ui-{unit,build}.log`, with corresponding
exit files. Refinement logs are `refinement-{unit,build}.log`, both exit 0.
The placement candidate uses `stream-{targeted-python,python,unit,build}.log`
and matching exit files, all exit 0.
Inline comment checks are `comments-unit-6.log` and `comments-build-4.log`,
with exit 0. Earlier corrective runs remain separate evidence.
The adoption repair uses `adoption-{targeted-python,python,unit,build}.log`
and matching exit files, all exit 0.
Complete Chrome results must be recorded
before this milestone can be marked complete.

The subsequent comment sizing refinement gives cards a 720px maximum width,
150px minimum height, separated actions, and a 120px minimum editor textarea.
Node 24 production build and `git diff --check` passed. An independent Chrome
fixture check created a real comment and reply, inspected desktop and mobile
layouts, and confirmed controls remained above the floating composer without
horizontal overflow. Measured desktop card width was 720px; mobile card width
was 286px within a 390px viewport. See the
[desktop thread](screenshots/phase3g/after/comment-thread-1440x900.png) and
[mobile reply editor](screenshots/phase3g/after/comment-reply-editor-390x844.png).
This focused visual check does not resolve the two full-suite failures.

## Autonomous continuation checkpoint — 2026-10-31

The latest completed Chrome run (`supervised2-browser.log`, exit 1) recorded
35 passed, 2 failed, 1 infrastructure skip. It preceded the comment sizing change.
The following required checks remain unresolved; earlier targeted passes do not
supersede these failures:

1. `web/browser/phase3g.spec.ts`: **File project settings is one modal with
   bounded mobile layout and guarded project management** failed in `ready()`
   before settings interaction. The footer remained API connecting / kernel
   checking during the default five-second assertion. The focused case passed.
   Reproduce on an accumulated suite dataset and measure actual readiness before
   deciding whether application latency or test synchronization is responsible.
   A large catalog scan is a hypothesis, not an established root cause. Do not
   add a sleep, blanket retry or unexplained timeout increase.
2. The same file's **library creation through execution, markdown, reorder,
   save/reopen, traceback and panels** failed when moving the saved Markdown
   cell above the code cell. The move POST reached the server and returned 409;
   this was not simply a missed click. Compare request revisions with completed
   save responses and controller/poll state. A rendered Markdown preview alone
   does not prove persistence has settled. Determine whether the test lacks a
   save-state condition or the application regresses revision state before
   changing either. Preserve conflict protection and never automatically retry
   the mutation to conceal the failure.

After a justified repair, run focused checks, then unit/build and the complete
Chrome suite on the same frozen source candidate (one worker, zero retries).
Retain failed logs, exact commands and exit statuses. Recheck relevant Python
contracts if backend behavior changes. Update this report and review the final
diff before the dedicated branch commit; no completion commit has been made for
the integrated conversation refinements. The editor checkpoint commit is not
final acceptance of them.

The owner requested the preview shut down after review. Its verified API process
received graceful shutdown, exited, and port 8000 was checked closed. Saved
notebooks and application data were retained. Leave the preview stopped unless
the owner requests it again; do not start it as part of a routine resume.

The preview on loopback port 8000 was restarted only after its receipts were
terminal. Application SQLite was backed up after shutdown; notebook/application
roots and saved data were retained. Repeated canonical GETs returned the same
conversation ID; saved notebook revision 20 and six cells were preserved. This
preview check is not a substitute for full browser regression. After the placement
implementation, a second owned restart preserved the user's then-current revision
23/eight cells, canonical conversation ID and every saved `.ipynb` SHA256. Receipts
were checked first; SQLite received a separate consistent backup after shutdown.
The adoption-backend restart subsequently preserved revision 27/eleven cells and
all notebook hashes. Comparing the backup to the migrated database verified that
all ten original records retained identical data, timestamps and revisions; one
position was adopted in the new map. These are observed preview checks, not a
substitute for full Chrome acceptance. The user may continue editing this data.

## Actual browser zoom

A separate disposable persistent Chrome profile was tested at native page zoom
100%, 125%, 150% and 200%, selected through Chrome's own Appearance settings.
No user browser profile, application code, CSS zoom or pinch scaling was changed.
The same physical 1440×913 content viewport produced these observed values:

| Native zoom | CSS viewport width | Device pixel ratio | Result |
| --- | --- | --- | --- |
| 100% | 1440 | 1 | Passed |
| 125% | 1152 | 1.25 | Passed |
| 150% | 960 | 1.5 | Passed |
| 200% | 720 | 2 | Passed |

At each level, CSS `zoom` and `visualViewport.scale` remained 1. Header/column and
composer/footer bounds settled correctly, Send remained above the footer, no
horizontal document overflow occurred, and composer position stayed fixed when
scrolling to the end. Measured scroll reserve matched the overlay height.

Private evidence is `actual-browser-zoom.{mjs,json}` and `actual-zoom-*.png` in the
temporary validation directory. The 200% capture was inspected. These captures
use the owner's active preview and must not be committed. An initial Playwright
screenshot cropped the physical viewport at native zoom; direct CDP viewport
capture corrected the artifact without changing the application. Full regression
screenshots must use disposable test fixtures.

## Screenshots and remaining validation

Original reference, before, and editor checkpoint screenshots remain in
`screenshots/phase3g/{reference,before,checkpoint-editor}`. Current integrated UI
after captures and browser verification are pending. The latest requested changes
are compact inset menu focus, per-cell history beneath output, and a composer
overlay aligned with the notebook paper. The white page extends behind the
overlay throughout scrolling; its measured height reserves space for the final
cell/message. Source changes and unit/build checks are present. Chrome screenshot
acceptance remains pending: an early mobile capture sampled stale header/footer
measurements immediately after resizing and appeared to clip the composer.
The browser owner must diagnose this with settled geometry and a fresh narrow
viewport; that capture is not accepted as evidence of responsive correctness.

Manual screen-reader testing, Safari/Firefox,
Docker and approved ARM64/containerd infrastructure have not been exercised in
this update. No permission-broadening workaround or automatic test retries are
used. Infrastructure skips remain explicit. The agent engine is intentionally
outside this checkpoint, not a claimed implemented feature.
