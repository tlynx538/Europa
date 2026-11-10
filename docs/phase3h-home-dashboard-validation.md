# Phase 3H Home dashboard reconstruction

## Current status — Checkpoints D+E validated

The owner approved A, B, and C for progression. The combined candidate is now
validated for navigation completion and release on the supported host; see
[the final validation report](phase3h-final-validation.md). Earlier checkpoint
results below are historical, including their stopping rules and failing-suite
evidence. They do not establish acceptance of the final combined candidate.

## Historical Checkpoint C summary

The owner approved B for continuation. C connects Recent to persistent open
history and Running to bounded, read-only runtime observations. The approved
dashboard and B storage/navigation contracts are preserved. See the
[Checkpoint C contract and evidence](#checkpoint-c--recent-notebooks-and-running-sessions-2026-11-10).
**Stop after C for review.** No shutdown controls, application Quit/Logout, agent
orchestration, completed-milestone commit or full-suite acceptance is implied.

## Historical Checkpoint B summary

Checkpoint A's visual design was approved by the owner on 2026-11-10. Checkpoint B
now connects that composition to real workspace browsing, journaled creation,
physical notebook association and the existing editor. The required Home → folder
→ notebook → Home → same notebook journey passes with disposable on-disk data,
real local execution and retained project/conversation/receipt identity. See the
[Checkpoint B results](#checkpoint-b--real-filesystem-integration-2026-11-10).
**Phase 3H remains incomplete; stop here for review.** Checkpoint C, runtime
shutdown, application Quit/Logout and agent orchestration have not started.
No commit has been made.

## Historical Checkpoint A report

2026-11-10. **Phase 3H remains incomplete.** This is the requested static browser
composition checkpoint. The new dashboard is isolated from the application until
the owner reviews its structure. No completed-milestone commit has been made.
Checkpoints B–E, real notebook opening/creation, runtime observations and final
regressions remain required. The yellow fixture banner is review tooling, not
part of the proposed application header.

The latest mission supersedes the welcome/project-welcome model in the earlier
[design](phase3h-design.md). Files and Running become the primary hierarchy;
the existing notebook editor remains the editor. Quit/Logout remain deferred.

## Starting Git state

- Branch: `feature/phase3h-workspace-navigation`.
- HEAD: `40386fb946a80e5252cbf4238d459860005812a9` (Phase 3G merge).
- Recent history: `40386fb`, `ad9a8fc`, `3d4a771`, `5dcb964`, `53aaee1`.
- Existing modified tracked files: `api/src/application/store.py`,
  `api/src/main.py`, `api/src/notebooks/router.py`, `tests/test_application.py`,
  `web/src/Navigation.test.tsx`, `web/src/Navigation.tsx`,
  `web/src/Pages.test.tsx`, `web/src/Pages.tsx`, `web/src/main.tsx`,
  `web/src/navigation.css`, `web/src/style.css`.
- Existing untracked work: `api/src/workspace/`,
  `docs/agent-prompts/phase3h-brief.md`, `docs/phase3h-design.md`,
  `docs/phase3h-validation.md`, `docs/screenshots/phase3h/h1/`,
  `tests/test_workspace_browser.py`, `web/browser/phase3h.spec.ts`,
  and Home, TextFileEditor, WorkspaceBrowse components and their tests.
- Starting tracked diff: 11 files, 504 insertions, 40 deletions. This excludes
  the substantial untracked H1 implementation. No reset, stash, migration or
  cleanup of owner changes was performed.

## Current implementation investigation

Read the repository instructions, README, earlier Phase 3H reports, persistence
and conversation contracts, agent workflow, navigation/editor components, current
styles, workspace browser/mutations and recent-open API changes.

| Area | Current checkout evidence | Remaining work |
| --- | --- | --- |
| Home | Welcome/Start/Recent/Browse/catalog sections | Replace with approved composition in B |
| Physical browse | Root-pinned paginated GET, folders, sizes/modified timestamps; traversal/hidden/symlink rejection | Adapt presentation; preserve cursor and errors |
| Text | Bounded UTF-8 read, journaled create/save, draft and pending-save leave protection | Preserve revision/replay behavior; filter unsupported types before editor |
| Physical project | Verified descriptor and private inode/path binding; journaled project creation | Integrate verified folder recognition; no implicit catalog migration |
| Physical notebook | Existing browser explicitly reports opening unavailable | Design durable ID/path association before opening/creating; never create a catalog notebook merely to disguise it as a physical file |
| Recents | Persisted deduplicated explicit-open events and bounded read | Integrate real locations and existing identities in menu |
| Sessions | Notebook session GET observes supervisor/client without provisioning; exposes connected/unavailable and count | Insufficient by itself to label Busy/Idle or prove stopped; review lifecycle fencing before any shutdown control |
| Editor exit | Existing common guards, wordmark and origin tracking | Verify known source on direct opens/history, Return Home, pending receipts and saved-state reopen journey |

The earlier report's 28 passed / 12 failed / 1 skipped full Chrome run remains
historical failing evidence. The later report notes an origin repair in the
current source, but it does not establish a full-suite pass. Pending-operation
readiness, Markdown move revision conflicts, project roadmap visibility and
delayed recents need reproduction on the integrated candidate. No claim is made
that these failures were reproduced or repaired in this fixture checkpoint.

## Component architecture and routes

- `web/src/Dashboard.tsx`: typed presentation component receiving entries,
  sessions, recents and callbacks. No fetches, controller, persistence or runtime
  imports. Uses existing Radix menu primitives and native tables/checkboxes/dialogs.
- `web/src/dashboard.css`: dashboard-scoped composition; no editor style edits.
  New original inline SVG file icons and an original Europa mark.
- `web/src/dashboard-preview.tsx`: disposable in-memory harness, scenario selector,
  fixture creation dialog and explicit placeholder when opening an editor.
- `web/dashboard-preview.html`: Vite development entry for owner review, separate
  from production `index.html`; not part of the production bundle.
- `web/playwright.dashboard.config.ts` and
  `web/browser-dashboard/composition.spec.ts`: isolated Chrome checks on loopback
  port 18173, with one worker and zero retries. No API/Gateway service starts.

**Production routes changed:** none at A. `/` still renders the existing Home.
The future adapter will replace `/` after review; fixture URLs must not become
real notebook identities. `/browse`, `/text`, catalog and notebook routes remain.

**Backend endpoints added/modified by this checkpoint:** none. Existing H1
endpoints listed above are preserved, not attributed to this checkpoint.

## Composition and reference comparison

Consulted [the official Notebook repository](https://github.com/jupyter/notebook)
and [the classic tree template](https://github.com/jupyter/nbclassic/blob/main/nbclassic/templates/tree.html).
The latter establishes the Files/Running hierarchy, compact selection toolbar,
New dropdown, path/list header, metadata columns and notebook runtime listing.
No Jupyter source code, icons, logos or assets were incorporated.

The two owner screenshot attachments are not present in this session or checkout.
No Jupyter reference screenshot has been fabricated or mislabeled. Exact visual
comparison to those images remains pending; the current comparison is to the
written mission and official classic structure.

At 1440px desktop width, the workspace is 1170px wide with 135px surrounding
margins, a 62px identity header, 33px menu bar, approximately 42px tabs and
39px file rows. The listing has aligned selection/name/modified/size/runtime
columns. New/Recent/Refresh sit at the right; recent history uses a compact menu.
Folders sort before files; projects retain a folder affordance. Missing metadata
uses an em dash. Status has both a label and a colored dot. Unsupported mutations
and upload are absent. Shutdown is visibly disabled with a reason.

At 390px, modified time is hidden, file names wrap, and Running rows place
location/state below the notebook name without breaking words into narrow
columns. The desktop table retains column alignment. No document overflow was
observed in either narrow capture. New creations in the preview only update the
current fixture directory and never persist across reload.

## Captured evidence

All captures use disposable data. Desktop viewport: 1440×900; narrow: 390×844.
Full-page PNGs include the additional fixture notice height. The before capture
intercepts catalog API responses; it does not access the owner's application data.

| View | Screenshot |
| --- | --- |
| Current Home before reconstruction | [Before](screenshots/phase3h/dashboard-a/before-home-desktop.png) |
| Proposed Files | [Files](screenshots/phase3h/dashboard-a/files-desktop.png) |
| Proposed Running | [Running](screenshots/phase3h/dashboard-a/running-desktop.png) |
| Project directory | [Project](screenshots/phase3h/dashboard-a/project-desktop.png) |
| New menu | [New](screenshots/phase3h/dashboard-a/new-menu.png) |
| Recent menu with locations | [Recents](screenshots/phase3h/dashboard-a/recents-menu.png) |
| Multiple selection | [Selection](screenshots/phase3h/dashboard-a/selection-desktop.png) |
| Empty directory | [Empty directory](screenshots/phase3h/dashboard-a/empty-directory.png) |
| Empty running | [Empty running](screenshots/phase3h/dashboard-a/empty-running.png) |
| Unavailable observation | [Unavailable](screenshots/phase3h/dashboard-a/unavailable-running.png) |
| Loading | [Loading](screenshots/phase3h/dashboard-a/loading-directory.png) |
| Narrow Files | [Mobile Files](screenshots/phase3h/dashboard-a/files-mobile.png) |
| Narrow Running | [Mobile Running](screenshots/phase3h/dashboard-a/running-mobile.png) |

Files/Running, menus, selection, empty/error views and mobile captures were opened
for visual inspection. The narrow Running capture prompted the two-line layout
repair. Notebook-open and returned-Home captures require real integration in D;
they have not been replaced with simulated editor acceptance evidence.

## Validation performed

Node 24.19.0 was explicitly selected by prepending its directory to PATH for
the recorded passing checks. An initial npm invocation selected Node 20 despite
the shell reporting Node 24; it was rerun with explicit PATH. No dependencies changed.

| Check | Result | Scope |
| --- | --- | --- |
| `npm run build` with Node 24 | Exit 0, final rerun passed | TypeScript checks preview/component; production bundle remains existing app |
| `npm test -- --maxWorkers=1` with Node 24 | Exit 0, 85 tests / 16 files | Existing frontend unit/component suite |
| `npx playwright test --config playwright.dashboard.config.ts --workers=1 --retries=0 --reporter=line` with Node 24 | Exit 0, 2 passed, 6.8s | Before capture and fixture composition journey |
| `git diff --check` | Exit 0 | Whitespace check |
| Python/backend tests | Not run at A | No backend changes in this checkpoint; existing H1 evidence is not recertified |
| Full application Chrome suite | Not run at A | Required in E; prior failures remain outstanding |
| Real Gateway/containerd/ARM64 | Not run | No runtime changes or real execution at A |

The initial browser attempt was blocked by sandbox listener permissions. A
permitted temporary loopback server/Chrome run then exposed an exact-text
selector mismatch: the empty-state container included its New button. Wrapping
the message in its own span fixed the selector boundary. The next run passed;
the final run also covers contextual fixture creation, loading and improved mobile
layout. No timeout increases, sleeps, forced clicks or automatic retries were used.

The fixture test verifies arrow navigation between tabs and file names, menu
arrow navigation, Escape dismissal/focus restoration, preferences-dialog focus,
multi-selection, disabled multi-open/shutdown, directory breadcrumbs, contextual
fixture creation, loading/empty/unavailable states, mobile overflow and **zero API
requests from the new preview**. It does not validate real persistence or kernels.

The test runner shut down its Vite server; afterward `lsof -nP -iTCP:18173
-sTCP:LISTEN` returned no listener (exit 1). No owner preview was started or
restarted; no private documents, receipts, project IDs or runtime registry changed.

## Review gate and next work

Checkpoint A requests owner review of these concrete screenshots, as required by
the mission. After that review, B must wire the real workspace adapter and resolve
the missing physical notebook association design before presenting notebook
creation/open as working. C must integrate persisted recents and authoritative
read-only runtime observations; shutdown needs a verified notebook-scoped contract
and confirmation. D must exercise guarded exit/reopen and browser history. E must
run the complete required backend/frontend/browser checks, reproduce relevant
historical failures, capture integrated visual evidence and only then commit.

No agent engine, runtime provider, Gateway protocol, notebook persistence,
conversation timeline, comments, roadmap or MIME rendering has been rewritten.

## Checkpoint B — real filesystem integration, 2026-11-10

### Starting state and scope

Continued on the same branch/HEAD and preserved every initially modified tracked
file and the untracked H1/A work. `AGENTS.md` remains intact. A's approved
composition, header, tabs, breadcrumbs, table, menus and responsive layout remain
the baseline. No notebook controller, cell UI, Gateway client, supervisor or
runtime-provider changes were made. The editor's only new menu action is guarded
**Return Home**; the existing Close Notebook action now receives a verified
physical source directory, including on direct opens/history.

### Components and routes

`PhysicalWorkspace.tsx` adapts workspace listings to `Dashboard.tsx`, recognizes
verified project folders, formats real metadata, paginates, limits text opening
to supported extensions and invokes existing text/folder/project mutations. New
notebook creation and initial association use the new physical adapter endpoints.
Browser-persisted operation IDs support exact replay after ambiguous responses;
another create/open cannot silently supersede the pending operation. Physical
notebook creation names the current real directory in its dialog.

`Navigation.tsx` renders the dashboard at `/` and `/browse?path=…`; optional
`view=running` preserves the selected tab in history. `/text?path=…` retains the
existing text editor. FastAPI now serves the SPA on `/browse` and `/text`, fixing
real refresh/deep-link 404s. Existing `/notebooks/{id}`, `/projects`, `/notebooks`
and catalog project routes remain. File-menu access to **Existing catalog
projects / notebooks** preserves private data access without migration.

Directory row metadata optionally includes `project_id` **only after** the
existing descriptor/private-binding verification. An ordinary folder stays an
ordinary folder; a copied descriptor is not a project identity. Private notebook,
application and configured runtime-registry paths/sidecars are excluded from
workspace access. Private-directory inode checks also survive directory renames.

Production CSS initially let the generic input-width rule stretch the dashboard
filter. Increasing the existing scoped selector's specificity restored the
approved 210px desktop/180px narrow widths; no visual redesign was undertaken.

### Physical notebook contract

`WorkspaceNotebookStore` subclasses the existing NotebookStore as a document I/O
adapter. Its inherited document/cell revisions, source checks, receipt execution
and recovery remain authoritative. One canonical physical `.ipynb` is edited in
place; there is **no private document mirror**. Private legacy notebook files
stay in their original location. All execution receipts stay private.

New/initially imported documents receive a server-generated notebook ID once.
Imported standard cells, outputs and ordinary metadata are preserved; copied
Europa identity/execution-marker claims are replaced rather than adopting
another document's history. Only the already-supported nbformat 4.5 code/Markdown
cell contract is accepted. Reopening a bound path preserves ID and revision.

A private atomic manifest journals the workspace-root and parent-directory
identity, relative path, operation identity and old/pending content hashes. It
contains no second notebook document. Writes persist intent before effect,
validate the old content and directory, fsync an exclusive hidden temporary,
publish atomically and fsync the directory before acknowledgement. Creation
publishes without replacing an existing file. A lost acknowledgement is
reconciled from exact committed bytes; an interrupted unpublished creation can
be explicitly retried with its journaled ID. Receipt recovery uses the existing
NotebookStore recovery path and never repeats execution.

The nearest verified ancestor project is bound to the same project ID in the
application catalog when a notebook is first associated. Canonical conversations
are ensured through the existing application method; repeated operations do not
create additional conversations. Physical membership cannot be reassigned or
removed through catalog membership APIs without a real move; move/delete are
not implemented. Physical project deletion is also rejected.

| Endpoint added or extended in B | Behavior |
| --- | --- |
| `POST /api/workspace/notebooks` | `{path, operation_id}`; creates a canonical physical notebook, then ensures verified project membership and conversation |
| `POST /api/workspace/notebooks/open` | Same body; explicitly associates/reopens an existing physical notebook |
| `GET /api/workspace/notebooks/{id}/location` | Verified relative source and project context; null for private catalog notebooks |
| `GET /api/workspace` | Existing response plus optional verified `project_id` on directory entries; private paths excluded |
| Existing notebook APIs | Same payloads/controller; bound IDs resolve their canonical physical document through the adapter |
| Existing project membership/delete | Reject filesystem-unsupported reassignment/deletion for physical bindings |

The physical write and application SQLite transaction are deliberately separate.
If the file is committed but membership/conversation initialization fails, the
browser retains the exact workspace request for replay. It must not create
another notebook. The completed physical step returns the same identity on replay.
The [persistence contract](notebook-persistence.md) and README describe this path.

### Validation and actual failures investigated

All frontend commands explicitly use Node 24.19.0. Heavy checks were serialized;
Chrome used one worker, zero retries, disposable roots and loopback-only services.

| Check | Result | Limit |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q` | Exit 0: **294 passed, 103 skipped**, final run 4.86s | Skipped real Gateway/containerd cases are not successful validation |
| Focused workspace/application/notebook checks during repair | Final focused run before two added recovery regressions: **134 passed** | Both additional regressions are included in the later 294-pass full default suite |
| `npm test -- --maxWorkers=1` | Exit 0: **86 tests, 16 files** | Includes updated dashboard/history/event assertions and verified physical direct-open close target |
| `npm run build` | Exit 0, final build passed | TypeScript plus production bundle |
| `npx playwright test --config playwright.workspace.config.ts --workers=1 --retries=0 --reporter=line` | Exit 0: **2 passed, 12.2s** on reviewed candidate | Real production frontend/API/workspace/local Gateway integration; scoped B suite |
| `npx playwright test --config playwright.dashboard.config.ts --workers=1 --retries=0 --reporter=line` | Exit 0: **2 passed, 5.4s** | Approved fixture composition regression plus production-root mount; no API service |
| `git diff --check` | Exit 0 | Whitespace/source review |
| Complete historical application Chrome suite | **Not run at B** | The earlier full-suite failures remain unresolved evidence for E |
| ARM64/containerd/real network infrastructure | Not run | No runtime-provider/network changes required |

Backend coverage includes physical project membership; same ID/revision/output/
conversation after restart; unchanged private documents; imported output retention
and distrust of copied IDs; stale revision rejection; outside-edit protection;
private path exclusion; symlink/hardlink/traversal/collision/reused-operation
rejection; directory replacement; journal interruption before and after effect;
first-read access-time changes; and completed receipt recovery after a physical
commit loses its acknowledgement. Receipts remain byte-for-byte private history.

Two browser journeys validate:

1. Home Files/Running/Files → real directory → refresh with path → reject an
   unsupported file → open/save text → import notebook without starting a kernel
   → persist conversation → decline unsaved-draft Home navigation → explicit save
   → real Python execution → block Home while receipt observation is held → settle
   → Home without stopping kernel → reopen same ID/document/revision/output and
   conversation → Close to source → Back/Forward → create a verified physical
   project via New → create its real notebook → verify disk bytes and project
   membership → narrow view → reopen same new ID. Exactly one execute request was
   observed; the live kernel remains present after Home/reopen.
2. Existing directory → New Folder → empty folder → New Text file → edit/save →
   verify real bytes in that folder → return and refresh without losing its path.

The pending-receipt browser check holds real GET responses behind an explicit
release signal. It synchronizes on actual controller states; it does not sleep,
fake a successful receipt, force clicks or retry execution.

Failures were diagnosed, not hidden:

- Initial backend checks found pre-publication creation replay returned 404; the
  same journaled identity can now be explicitly resumed with no-replace publication.
- Imported text MIME output can be a standard multiline array; its preservation
  assertion was corrected to accept that representation.
- The browser found `/browse` refresh returned server 404; SPA route registration
  fixed it, with backend and browser coverage.
- The first physical read incorrectly treated its own access-time update as an
  external edit. The consistency check now compares device/inode/size/mtime/ctime
  and has a first-read regression.
- The browser selector `Autosave` with exact matching missed the existing checked
  indicator in its accessible name. The test now matches the label including the
  indicator; editor menu behavior was preserved.
- Playwright evaluated its config in both runner and worker, generating two fixture
  roots. The worker now inherits the runner's fixture root; fixture seeding occurs
  once. The disk assertion consequently inspects the actual server workspace.
- Navigation unit fixtures were updated for the new workspace response shape;
  delayed explicit recent-open event coverage is retained without implementing C's
  Recent display. A test used Playwright's `exact` option in Testing Library and
  was corrected after TypeScript rejected it.
- A few npm invocations from the repository root failed to find `package.json`;
  the reported passing checks were run from `web/` with explicit Node 24.

Host diagnostic logs: `/tmp/europa-b-python-reviewed.log`,
`/tmp/europa-b-browser-2.log` through `-5.log`,
`/tmp/europa-b-browser-final.log`, `/tmp/europa-b-browser-reviewed.log`, and
`/tmp/europa-b-composition.log`. Earlier failed logs are not reclassified as passes.
No global timeout was increased; the new fixture config adds a bounded 10-second
action timeout for faster diagnosis. Original A screenshots were retained; later
static regression captures go to a separate subdirectory under B.

### Real integration screenshots

All are actual production UI/API views over disposable on-disk workspace data,
not intercepted listings. Desktop: 1440×900; narrow: 390×844.

| View | Evidence |
| --- | --- |
| Real populated ordinary directory | [Directory](screenshots/phase3h/dashboard-b/real-directory.png) |
| Imported notebook with saved execution output | [Notebook](screenshots/phase3h/dashboard-b/notebook-from-directory.png) |
| Returned Home, synchronized on loaded directory row | [Home](screenshots/phase3h/dashboard-b/returned-home.png) |
| Verified physical project containing its new notebook | [Project](screenshots/phase3h/dashboard-b/real-project-directory.png) |
| Real project at narrow width | [Narrow project](screenshots/phase3h/dashboard-b/real-project-mobile.png) |

The real directory, project, mobile, notebook and returned-Home captures were
opened for inspection. The notebook capture includes the existing leave-guard
notice from the deliberately blocked pending navigation; the controller footer
shows the subsequently settled receipt. This guard notice can remain visible
until a later editor action/reload, an inherited editor limitation retained in B.

### Limitations and review boundary

- Recent-open tracking still records successful visits in the existing backend.
  Its dashboard menu honestly indicates integration is pending. Running reports
  observation unavailable/Unknown; no kernel list, observation adapter or shutdown
  control was implemented. These belong to C and have not started.
- Physical notebook import currently requires the existing nbformat 4.5
  code/Markdown contract. No raw-cell/version conversion or conflict-recovery UI.
- External edits and changed directories are blocked; no silent adoption, move,
  rename of physical paths, overwrite recovery, bulk delete or upload. The existing
  Rename notebook action still changes its managed title, not its filename.
- The workspace remains a trusted single-owner filesystem. Hash/identity rechecks
  and atomic publication are not an OS-wide lock against a concurrent external
  writer racing the final check. Adversarial multi-writer stress was not validated.
- Listing pagination is preserved. Folders sort before files among loaded entries;
  global cross-page folder sorting is not a new backend contract in B.
- Empty physical projects are recognized through their verified descriptor/binding;
  the corresponding catalog record is initialized on first notebook association.
- Moving/restoring a workspace into different directory inodes requires a future
  explicit rebind contract. Copied metadata/descriptors cannot restore identity.
- The full historical Chrome suite and its known regressions have not been
  certified by these focused passes. E still requires the complete frozen-candidate
  regression and integrated milestone acceptance.

The temporary API on 18018 and Vite on 18173 were shut down by their test runners.
Process/listener inspection found no remaining owned listeners. Owner preview and
private documents/application state/runtime registry were not used for testing.

**Checkpoint B is ready for owner review. No completed-milestone commit was made.
Stop here; do not begin C or Phase 4 without further owner direction.**

## Checkpoint C — recent notebooks and running sessions, 2026-11-10

### Scope and starting state

Continued on `feature/phase3h-workspace-navigation`, HEAD `40386fb`, preserving
all uncommitted H1/A/B changes and evidence. B was explicitly approved for
continuation. No notebook editor/controller, runtime provisioning/destruction,
registry schema, Gateway protocol, admission policy or infrastructure was changed.
The lifecycle only gains shutdown of the adapter's own read-only observation
tasks before existing application teardown. No commit has been made.

### Recent-open integration

`GET /api/dashboard/recent?limit=20` projects the existing `recent_opens` records
in descending actual `opened_at` order with a stable ID tie-breaker. It returns
notebook ID, current title, verified location, availability and original timestamp.
It does not write history, reconcile the application catalog, create a
conversation, provision a runtime or infer recency from receipts. Existing explicit
`POST /api/notebooks/{id}/opened` remains the only navigation history writer.

Physical locations require the pinned root/parent, exact committed content and
the original verified project binding. `WorkspaceNotebookStore.dashboard_info`
does not reconcile pending file writes. Private notebooks use their managed
document and existing catalog membership; standalone private books say **Private
catalog**. No copied notebook metadata or descriptor grants a location/identity.

Unlike the older present-only recent endpoint, this dashboard projection retains
missing, moved, changed or inaccessible history rows as disabled **Unavailable
notebook / Unavailable** entries. It does not display an unverified former path or
silently map that ID to another file. Entries are deduplicated by the existing
primary key. Menu selection uses the existing notebook URL and guarded navigation,
not notebook creation/import. The dropdown refreshes on dashboard mount, explicit
visit-recorded events, opening the menu and manual Refresh. Failed recent reads
show unavailable rather than a fabricated empty history.

### Runtime observation contract

`GET /api/dashboard/sessions?limit=20&cursor=<notebook UUID>` returns one page of
managed catalog notebook observations, including stopped notebooks, plus an
optional next cursor. Deleted/unavailable documents remain honest Unknown rows
with reopening disabled. It does not scan/import arbitrary filesystem notebooks
or expose the default Gateway/warm pool as notebook sessions.

Returned fields are notebook ID/title, verified location/availability, `state`,
sanitized `reason`, `observed_at`, `expires_at`, and `last_activity`. No container
ID, runtime handle, generation, kernel ID, Gateway URL, token or receipt output is
returned. Last activity comes only from a valid timezone-aware Gateway kernel
`last_activity`, never a recent-open or saved-output timestamp. It is null when
not available. Reasons and original observation timestamps are available on row
hover; expired/failed verification cannot retain a live label.

| State | Required evidence |
| --- | --- |
| Starting | A live accepted NotebookService task is acquiring a runtime/kernel; or the currently owned handle/durable phase is provisioning; or an exact owned Gateway kernel reports `starting`. A creation acknowledgement is not proof of Idle. |
| Busy | Exact owned Gateway inventory reports `busy`; or a live accepted document execution still owns its current-generation running kernel/operation while settling. A historical running marker alone cannot establish Busy. |
| Idle | Current supervisor notebook/handle/client/generation agree; no ownership/recovery blocker or orphaned relevant operation/receipt; authenticated Gateway inventory exactly equals the owned kernel set; every kernel reports `idle`; no active kernel operation or accepted execution remains. An empty inventory is not Idle. |
| Stopped | Exact verified live Gateway inventory contains zero kernels; or a reconciled durable registry has no live assigned allocation; or retained local-provider teardown records show stopped/destroyed with no process; or no allocation or runtime-bearing receipt exists in the current owner session. |
| Unknown | Gateway failure/timeout; changed binding/generation/evidence during observation; unavailable document/location; unsupported kernel state; bounded receipt evidence exceeded; observation expired. After a local backend restart, old runtime-bearing receipts without authoritative local adoption/absence evidence remain Unknown. |
| Needs attention | Ownership/recovery blockers, reconciliation/deleting/ambiguous phases, unavailable binding/generation, unmatched Gateway ownership inventory, orphaned durable operations, or unresolved relevant execution receipts (`uncertain`, pending replay, or prepared/running without a live owner task). These are never relabeled Idle/Stopped merely because outputs are old or a kernel reports idle. |

The adapter deliberately avoids `supervisor.inspect`, provider `inspect`,
`status_for`, lifecycle `status`, and registry `activity()`: current implementations
can clean up failures, retain reservations, quarantine allocations or initialize
activity rows. Instead it reads existing supervisor ownership, registry SELECTs,
receipt metadata and authenticated `GatewayClient.list_kernels()` GETs. It never
calls `client_for`, `ensure`, kernel creation, interrupts, stops or teardown.

Bindings, generations, registry phases/operation counts and receipt-directory
version are rechecked after network observation. A changed snapshot returns
Unknown. This is a short-lived observation, not a promise that no lifecycle
transition can occur immediately afterward. Registry ownership/fencing and all
resource reservations remain under their existing lifecycle owner.

### Bounded refresh and responsiveness

- At most 20 notebook rows per request, with explicit next/previous page controls.
  All catalog IDs are paginated, so active sessions beyond the first page remain
  reachable; the adapter does not pretend this is an active-first sorted list.
- Up to four concurrent Gateway GETs globally per adapter; each has a one-second
  deadline and the page's network phase has a four-second deadline. Same-page
  concurrent waiters share a single observation task. At most four distinct pages
  may be in flight. Client cancellation does not spawn another same-page job.
- File/SQLite metadata is read off the event loop under existing lock order, one
  private receipt-directory pass per page. Decoded evidence is bounded to 100
  receipts per notebook and 32 MiB per page. Overflow/read failure yields Unknown,
  not invented absence. Metadata I/O is bounded in amount, but waiting for an
  existing storage lock can outlast the network deadline; the browser request
  retains its existing 15-second bound.
- Running fetches on activation/navigation return, page change and Refresh.
  It performs at most six further automatic refreshes, ten seconds after each
  completed request, while visible. No runtime polling occurs on Files, in an
  unmounted dashboard, or while the document is hidden. A paused message requests
  explicit Refresh once the automatic budget is exhausted.
- Every verified label expires after 15 seconds. Browser-side expiry converts it
  to Unknown and requests refresh; stale timestamps are never replaced with the
  current time. Transport failure preserves the notebook rows as Unknown and
  reports the failure. One backend row failure does not discard the other rows.
- The existing notebook view already reconciles execution receipts/session state.
  Returning Home mounts a fresh dashboard observation. C adds no execution
  polling/controller or second notebook persistence system. Files' runtime column
  remains Unknown when it has no independent current observation; recent history
  never supplies a running label.

The approved menus, tabs, file table and responsive layout are retained. Shutdown
buttons remain disabled; no lifecycle action was added or separately reviewed.

### Focused validation

Node 24.19.0, the repository CPython 3.13 `.venv`, one Chrome worker and zero
browser retries were used. Final checks were serialized and used disposable roots.

| Check | Actual result | Limits |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q` | **305 passed, 105 skipped**, exit 0, 6.14s | Default suite; opt-in integration skips are not passes |
| `.venv/bin/python -m pytest tests/test_dashboard.py --run-local-gateway -q` | **13 passed**, exit 0, 5.71s | Includes real authenticated local kernel states and complete backend lifespan restart; no containerd certification |
| Node 24 `npm --prefix web test -- --maxWorkers=1` | **90 tests / 17 files passed**, exit 0 | Includes freshness, manual-failure row retention, bounded polling and visit events |
| Node 24 `npm --prefix web run build` | Exit 0 | Production TypeScript/build |
| Node 24 `npx playwright test --config playwright.dashboard-c.config.ts --workers=1 --retries=0 --reporter=line` from `web/` | **1 passed**, exit 0, 11.1s | Integrated Recent/Running journey over real disk/API/Gateway data |
| B navigation/guard regression using `playwright.workspace.config.ts` | **2 passed**, exit 0, 11.8s | Scoped B journeys; does not replace the historical complete suite |
| `git diff --check` | Exit 0 | Final whitespace/source inspection |
| Full historical Chrome suite | **Not rerun** | Existing failures remain outstanding; C is not milestone acceptance |
| ARM64/containerd, CNI/firewall | Not run/changed | Controlled registry fixtures only; no new infrastructure |

Backend tests cover all six states; exact inventory ownership; generation races;
same-page coalescing; maximum four concurrent GETs; 20+3 pagination without
duplicates; per-row failure isolation; missing/moved recent entries; mixed recent
ordering/deduplication; no SQL/activity/manifest writes on observation; unresolved
durable operations; private credentials absent from responses; and old local
runtime history remaining Unknown after owner state is lost.

The real local test explicitly starts one kernel through existing infrastructure,
observes Idle, submits a receipt-backed execution gated on a disposable release
file, observes Busy, releases it, verifies applied output `42`, observes Idle,
simulates a failed Gateway read (Unknown), then uses existing owned teardown and
observes Stopped without changing the saved notebook. It checks runtime allocation
count is unchanged by listing. Condition polling waits on actual observations;
no fixed sleep is used as evidence of readiness or completion.

The restart test closes and reconstructs the complete backend lifespan: notebook
store, application SQLite, supervisor and provider, over the same disposable roots.
It verifies exact recent IDs/timestamps/order survive. This is backend lifecycle
restart evidence, not OS reboot or local-kernel process-adoption certification.

The Chrome journey opens physical and private notebooks, verifies recent order and
deduplication after reopening/refresh, captures the real menu, starts a kernel via
the existing test API, observes Idle/Busy/Idle on Running, injects a failed dashboard
HTTP refresh to verify Unknown rows remain, tears down through the existing test
API, observes Stopped, checks narrow layout and reopens the same notebook with saved
output and settled receipts. Dashboard shutdown remains disabled throughout.

### Actual failures investigated

- A read-only catalog membership join initially used ambiguous SQL column names.
  Explicit table qualifiers fixed the projection. Failed metadata verification now
  resets availability and location together, preventing partially verified rows.
- A controlled coalescing test waited on a Gateway entry signal that the preceding
  metadata failure prevented. The owned test run was interrupted, the SQL defect
  repaired, and the entry wait given a bounded condition timeout. That interrupted
  run remains failed evidence, not a passing result.
- Chrome showed explicit Refresh cleared all rows before a failed request could
  convert them to Unknown. Refresh now preserves the current page; switching page
  still clears it. A dedicated frontend regression verifies this behavior.
- A later real-kernel run observed legitimate `Starting` immediately after the
  create acknowledgement. The test now condition-polls authoritative Idle under
  the existing deadline, matching the contract; runtime code and global timeouts
  were not changed.
- Final source review added an after-GET ownership check: losing the execution
  owner without a terminal receipt must remain Needs attention even if Gateway
  reports idle. A controlled race regression verifies that condition. The final
  backend and C browser runs include this guard.

Host evidence: `/tmp/europa-c-backend-1.log`, `-2.log` (interrupted), `-3.log`,
`/tmp/europa-c-real-1.log`, `/tmp/europa-c-real-final.log` (failed Starting assertion),
`/tmp/europa-c-real-reviewed.log`, `/tmp/europa-c-python-final.log`,
`/tmp/europa-c-units-1.log`, `-2.log`, `/tmp/europa-c-build-1.log` through `-3.log`,
`/tmp/europa-c-browser-1.log` (failed manual-refresh case), `-2.log` (passed), and
`/tmp/europa-c-b-regression.log`. Final reviewed-candidate results are in
`/tmp/europa-c-real-final-reviewed.log`, `/tmp/europa-c-python-reviewed.log` and
`/tmp/europa-c-browser-reviewed.log`. No old complete-suite failure logs were replaced.

### Real screenshots and limitations

| View | Evidence |
| --- | --- |
| Persisted physical/private Recent entries | [Recent](screenshots/phase3h/dashboard-c/recent-real.png) |
| Live kernel observed Idle | [Idle](screenshots/phase3h/dashboard-c/running-idle-real.png) |
| Real accepted execution observed Busy | [Busy](screenshots/phase3h/dashboard-c/running-busy-real.png) |
| Simulated failed HTTP refresh over actual rows | [Unknown](screenshots/phase3h/dashboard-c/running-failed-refresh.png) |
| Verified teardown observed Stopped | [Stopped](screenshots/phase3h/dashboard-c/running-stopped-real.png) |
| Actual Running at 390px | [Narrow Running](screenshots/phase3h/dashboard-c/running-mobile-real.png) |

These captures were opened for inspection. Recent/Idle/Busy/Stopped are actual
backend/disk/Gateway data, not static fixtures or fabricated session responses.
Unknown intentionally simulates HTTP transport failure; Starting/Needs attention
also have controlled backend evidence, not claimed real containerd evidence.
Original A/B captures are retained; B regression captures are separate under C.

Large receipt histories beyond the evidence budget produce Unknown. Automatic
refresh deliberately stops after its budget, so prolonged open dashboards need
manual Refresh and can expire to Unknown. Running includes stopped/unavailable
managed catalog notebooks, paginated by ID; active-first filtering is not provided.
Missing history entries have a generic unavailable title because recent history
does not store a potentially stale title/path. No move/rebind, shutdown, quit,
login/logout, agent engine or infrastructure change is included.

The historical full-suite failures and full milestone acceptance remain open.
Temporary loopback test services were stopped by their owners. Final listener
and process inspection found no remaining test API on 18018, Vite on 18173 or
owned dashboard pytest process. The stopped owner preview was not restarted;
private owner documents and runtime registry were never used as test fixtures.

**Stop after Checkpoint C for owner review.**

## Checkpoints D+E — final integration validation (2026-11-10)

See [the final report](phase3h-final-validation.md) for the navigation diagram,
changed-file inventory, repairs, historical failure disposition, screenshot
review, accessibility findings, runtime evidence, and limitations. Final combined
checks: **331 backend passes / 79 Linux-dependent skips**, **94 frontend passes**,
production build passed, **56 Chrome passes / 1 ARM64-environment skip**, and
**4 Go model tests passed**. Chrome ran with one worker, zero retries and zero
failures on the frozen candidate; it includes the original suite plus A/B/C and
D+E journeys. Earlier failure reports above remain historical evidence.

Real project/standalone/private notebook navigation retains saved source, output,
revision, canonical conversation and verified kernel identity. Draft/CAS guards,
pending/uncertain receipts, exact mutation replay, recents persistence across an
actual backend restart, and read-only runtime observations were exercised.
Desktop/mobile screenshots are under `screenshots/phase3h/final/`; the final
report indexes the 20 requested states. No shutdown, Quit/Logout, provider rewrite,
agent implementation, or infrastructure reconfiguration is included.

Stop after D+E. The Linux/containerd and native-browser-zoom limitations are
explicit in the final report; skipped checks are not claimed as passes.
