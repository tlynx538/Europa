# Phase 4 validation

## Status

4A passed the supported host gate. Continue through 4D only after each independent
mandatory gate passes. Phase 4E/F are excluded.

## Git preparation

- Starting worktree clean on main `c5a8a603ddef020d42f802c15896174d11678287`.
- Verified validated commits `a6b2daa` and `7e7598b` are ancestors; main's tree
  equals the validated feature tip. Prior merge `c5a8a60` already contains Phase 3H;
  `git merge --ff-only feature/phase3h-workspace-navigation` reported up to date.
- Created local annotated tag `phase3h-complete` at that main commit.
- Created `feature/phase4-agentic-harness` from main.
- No reset, force-push, history rewrite, remote cleanup/publication or master branch.
- Preserved main and both linked worktree branches. No unmerged non-worktree local
  branches existed. Deleted only these ancestor-verified local branches:

| Deleted local branch | Retained commit (ancestor of main) |
| --- | --- |
| `feature/containerd-provider` | `53564981924df5ad3a74f53b59eff0fde6e001a9` |
| `feature/conversational-notebooks` | `2009a9e030042be1567de250c2adebcd40f0b273` |
| `feature/europa-web-ui` | `cb333250a826d0c75f03f0401428ab5c955dbbf8` |
| `feature/notebook-persistence` | `a5f715d970f4ba7ab9497a51d675c7d0ae28c40c` |
| `feature/phase3f-b-navigation` | `517185c5f133f7fcb3fa742bcd9a85d2e186190c` |
| `feature/phase3f-c-jupyter-ui` | `53aaee14171a985dc7887cc7a2e9a2f86c69771c` |
| `feature/phase3f-d-ui-recovery` | `53aaee14171a985dc7887cc7a2e9a2f86c69771c` |
| `feature/phase3g-notebook-ux` | `ad9a8fcbb9060708c4fa603967ad68cb521122a5` |
| `feature/phase3h-workspace-navigation` | `7e7598b7a370216870efa3fcdf85ea28a33fa7e0` |
| `feature/resource-admission` | `9a064c5e57e1558c3cb7a6cc14399228a1149729` |
| `feature/runtime-lifecycle` | `e3a4d4e85bceabc0364e2fa74bdab6dde2d3f89e` |
| `feature/runtime-prewarming` | `12e0ec7d02225c6b33dd8f0c82e0409a86fdd6e6` |
| `feature/runtime-provider` | `cd50ce8ebad6ddd674e24f46ccd4ce21c963ce57` |
| `feature/runtime-recovery` | `21dd42a5029e369b0210b92b40c8803561da22d0` |
| `feature/ui-refinement` | `c0a3f01d22a9c08100908e835166f965116a4791` |
| `migration/jupyter-kernel-gateway` | `9dd067308a50b985f85973c6ceb88643b2f69f26` |
| `validation/step2b-runc-arm64` | `681dec0f89991dc7a06da3aa765476c81b3ca691` |

All remote branches and worktrees were retained. Git preparation did not alter application files or owner data.

## Checkpoint 4A

Starting commit: `c5a8a603ddef020d42f802c15896174d11678287`.

Changed files: `api/src/agents/{schema,models,providers,store,tools,context,runtime,router}.py`,
application store and API lifespan wiring, `tests/test_agents_foundation.py`, migration
fixtures in `tests/test_application.py`, Chrome agent fixture/suite/configuration,
README, Phase 4 design reports and screenshots. Schema v7 extends the existing
application DB, permits workspace conversation/message rows and preserves existing
records/IDs/foreign keys/append-only rules. Old-version fixtures now reconstruct
actual old schema shapes; an initial 4-test fixture failure was resolved without
making production migration accept an inconsistent database.

`/api/agents/configuration`, `/conversation`, `/runs`, `/runs/{id}`, paginated
`/events`, exact approval decisions, `/cancel` and `/resume` provide the durable
client contract. Runs and user turns share a transaction; stable operation IDs
replay without duplicate messages. SQLite owner token/generation/expiry fences
worker commits. Only six scoped read tools are registered; approval enforcement
and effect interruption are tested with controlled journal fixtures. A notebook
scope grants its own cells/metadata and relevant project, with no directory grant.
No provider key or reasoning field is exposed; credential-bearing proposal IDs
and arguments are rejected. No production write or execution tool in 4A.

| Command | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest tests/test_agents_foundation.py tests/test_application.py -q` | 0 | 86 passed before the final extra secret-ID test |
| `.venv/bin/python -m pytest --run-local-gateway -q -ra` | 0 | 357 passed, 79 Linux/containerd skips, 44.10 s on final 4A |
| `npm test -- --maxWorkers=1` (Node 24, `web/`) | 0 | 94 passed |
| `npm run build` (`web/`) | 0 | production build passed |
| `npx playwright test --workers=1 --retries=0 --reporter=line,json` | 0 | 57 passed, 1 ARM64 skip, 2.7 min |
| `npx playwright test --project=agents --workers=1 --retries=0 --reporter=line` | 0 | final secret-ID hardening affected-browser rerun: 1 passed, 12.9 s |

The complete Chrome run includes all Phase 3H suites and the 4A browser-origin
Markov study request. The last backend-only hardening rejected credential-bearing
provider call IDs and hid key values from configuration repr; the full backend and
relevant browser scenario passed again. Frontend/build were unchanged. Captures:
[desktop Home](screenshots/phase4/4a/home-desktop.png) and
[mobile after restart](screenshots/phase4/4a/home-mobile-after-restart.png) were
inspected. They show the preserved dashboard, not a fabricated chat UI. The
browser test verifies actual user/assistant records, read-tool events, idempotent
replay, cross-origin rejection and identical history after owned backend restart.

Fake provider and loopback transport fixtures only; no paid or external provider
smoke. Tool calls are single per response and public events are polled; native
provider streaming and general exactly-once effects are not claimed. Full logs:
`/private/tmp/europa-phase4-validation/` (initial application/agent fixture logs
also under `/tmp/europa-phase4-*.log`). Test listeners were stopped; owner data and
preview were untouched. Mandatory 4A gates pass; proceed to 4B.

## Checkpoint 4B

Starting commit `1534cc0` (validated 4A).

The Home drawer uses the canonical workspace conversation and bounded polling.
Closing it or reloading the page leaves queued work and approvals on the server.
The request operation ID stays in the browser until the send is acknowledged so
a lost response can be retried with the same arguments. The Files and Running
views retain their existing routes and behavior.

New API reads are `/api/agents/conversations/{id}/messages` (cursor-paginated)
and `/api/agents/conversations/{id}/handoffs` (bounded). Agent run creation now
accepts `mode=coordinate` for the Home workspace capability. Schema v8 adds
the handoff record with the source and destination conversation IDs, exact
approved plan and digest, project and notebook IDs, and creation provenance.
The handoff is linked to the destination's canonical conversation. Planning
messages stay in the workspace conversation.

The coordinator's only mutation requires an exact owner approval for a direct
child project in the granted directory and one named notebook. A verified
existing project can be targeted by its registered ID. Project creation and
notebook association reuse the existing workspace journals. Physical binding is
verified before writing, and a previously archived catalog record prevents
creation. The integration test exposed that a newly registered directory has a
physical binding before it has an application catalog row. The shared notebook
association now creates the catalog record through the existing bind operation.

Changed files include `api/src/agents/` (Home tool, grants, context, runtime,
schema, router and fake provider), `api/src/application/store.py` (migration),
`api/src/workspace/{notebooks,router,service}.py` (shared association),
`web/src/{PhysicalWorkspace,HomeCoordinator,AgentActivity}.tsx`, the agent API,
feed and styles, and the Python and Chrome integration tests.

The backend tests verify exact approval and denial, duplicate decisions,
collision protection, canonical physical identity and handoff, and a lost tool
acknowledgement after actual notebook creation. The last case records uncertainty
and preserves the notebook mapping. A project may exist even when its notebook
could not be created. The tool records partial success where it can verify the
result. Recovery of an uncertain multi-resource effect requires inspection.

The Chrome journey sends the forecast objective, reloads while awaiting approval,
approves creation, and opens the saved notebook. After a real backend restart it
checks the Files and Running tabs and reopens the same notebook and conversation
from the persisted Home history. Inspected captures show the
[desktop approval drawer](screenshots/phase4/4b/home-approval-desktop.png) and
[mobile conversation after restart](screenshots/phase4/4b/home-handoff-mobile.png).

| Command | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest tests/test_agents_home.py tests/test_agents_foundation.py tests/test_application.py tests/test_workspace_notebooks.py -q` | 0 | 100 passed |
| `.venv/bin/python -m pytest --run-local-gateway -q -ra` | 0 | 361 passed, 79 Linux/containerd skips |
| `npm test -- --maxWorkers=1` (Node 24, `web/`) | 0 | 94 passed across 17 files |
| `npm run build` (`web/`) | 0 | production build passed |
| `npm run test:browser -- --project=agents --grep '4B:' --workers=1 --retries=0` | 0 | 1 passed |
| `npx playwright test --workers=1 --retries=0 --reporter=line,json` (first full run) | 1 | 55 passed, 3 failed, 1 ARM64 skip |

The first full run exposed three existing Running regressions in the mixed
recents, physical project and multiple-kernel journeys. All three failed on
an Idle assertion. The captured session responses returned HTTP 200 and
`Starting` with reason `Verified Gateway kernel reports starting`. Faster
dashboard polling left two of three focused scenarios failing. The installed
Jupyter Server updates this field from a separate IOPub activity subscriber.
Gateway's shell could answer a readiness request before that subscriber saw
status. The Gateway client now confirms the same kernel reports Idle before
acknowledging readiness. If it still reports Starting, the client requests
kernel info again within the existing readiness deadline. This runs no Python
and leaves the dashboard's read-only observation contract and refresh schedule
intact. The protocol regression verifies the correlated info requests and
Gateway state transition.

Failure logs and traces are in
`/private/tmp/europa-phase4-validation/4b-browser.log`,
`4b-failed-traces/`, `4b-starting-target.log`, and
`4b-fast-observation-traces/` under the same validation directory.

| Final readiness candidate | Exit | Result |
| --- | --- | --- |
| `npx playwright test --workers=1 --retries=0 --grep 'persisted mixed recents\|complete saved-state and verified kernel journey: project\|multiple real notebooks retain'` | 0 | 3 passed |
| `.venv/bin/python -m pytest --run-local-gateway -q -ra` | 0 | 362 passed, 79 Linux/containerd skips |
| `npx playwright test --workers=1 --retries=0 --reporter=line,json` | 0 | 58 passed, 1 ARM64 skip, 3.1 min |

Final logs are `4b-ready-target.log`, `4b-python-final.log`,
`4b-browser-final.log` and `4b-browser-final.json` in the validation directory.
The final runtime change affects only authenticated kernel readiness in
`api/src/kernel/gateway_client.py` and its protocol coverage. The previously
validated frontend production build and unit-test candidate are identical.

## Checkpoint 4C

Starting commit `3f7413b` (validated 4B).

The existing notebook composer has a separate Ask Coordinator action. It
stores the user's message in the same canonical notebook conversation, with
an optional same-scope reply or cell reference. Run status, the structured
proposal and the reply appear in the existing canvas. The existing roadmap
panel renders the committed plan. Browser reconnect reuses the canonical
conversation and the resumable agent run and event APIs.

The `/api/agents/runs` create body accepts optional `parent_id`, `cell_id` and
`document_revision`. Omitted values preserve prior idempotency fingerprints.
Notebook coordinate mode grants `roadmap.plan` only to a notebook with active
project membership. No database schema change. The new registered tool
`save_notebook_plan` validates bounded task IDs, criteria and dependency order.
Approval covers the complete proposal, notebook revision, and existing roadmap
ID and revision when applicable. ApplicationStore commits the approved plan
with its existing version and operation journal. A revision conflict rejects
the proposal, and the tool preserves tasks with recorded progress. New tasks
are pending and carry no invented evidence.

Changed files: agent context, ingress models, planning tool, fake provider,
runtime, store and policy; notebook conversation composer, Workspace, agent
activity and feed, application record types and styles; focused Python and Chrome
tests and screenshots. Planning on a notebook without project membership remains
discussion-only. The initial plan editor accepts one notebook-specific roadmap
at a time and up to twelve tasks per proposal. Existing project-wide roadmaps
are readable within scope and retain their existing owner controls.

The focused tests verify the canonical message and cell reference, visible
authorized context, persisted task dependencies, revision conflicts, and
cross-notebook isolation. The browser journey captures a pending plan, restarts
the disposable backend, approves it, and reopens the saved conversation and
roadmap. Inspected captures include the
[desktop proposal](screenshots/phase4/4c/notebook-approval-desktop.png) and
[mobile conversation](screenshots/phase4/4c/notebook-plan-mobile.png).
The [mobile roadmap](screenshots/phase4/4c/notebook-roadmap-mobile.png) shows the
four persisted tasks still upcoming after approval.

| Command | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest tests/test_agents_planning.py tests/test_agents_home.py tests/test_agents_foundation.py tests/test_application.py -q` | 0 | 94 passed |
| `.venv/bin/python -m pytest tests/test_agents_planning.py -q` | 0 | 3 passed, including the final output-context assertion |
| `.venv/bin/python -m pytest --run-local-gateway -q -ra` | 0 | 365 passed, 79 Linux/containerd skips |
| `npm test -- --maxWorkers=1` (Node 24, `web/`) | 0 | 94 passed across 17 files |
| `npm run build` (`web/`) | 0 | production build passed |
| `npx playwright test --project=agents --grep '4C:' --workers=1 --retries=0` | 0 | 1 passed |
| `npx playwright test --workers=1 --retries=0 --reporter=line,json` | 0 | 59 passed, 1 ARM64 skip, 3.5 min |

An initial isolation test supplied a stale project revision when creating the
second membership. The fixture now uses the current project revision. No
revision check changed. Logs and the Chrome report are under
`/private/tmp/europa-phase4-validation/4c-*`.
