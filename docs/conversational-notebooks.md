# Phase 3B: persistent conversational notebook foundation

Based on Phase 3A commit `a5f715d970f4ba7ab9497a51d675c7d0ae28c40c`;
branch `feature/conversational-notebooks`. Implementation commit is in Git history
and the completion report. Read the [mandatory safety review](phase3b-safety-review.md)
for dataset boundaries, receipt retention and the output rendering contract.
This milestone implements storage and REST only. No frontend, agents, providers,
MCP, dataset uploads, streaming infrastructure or runtime changes are introduced.
GatewayClient and runtime ownership/fencing storage are unchanged.

## Storage and migration

`EUROPA_APPLICATION_ROOT` selects a private application directory; default is
`$EUROPA_NOTEBOOK_ROOT/application` (using the normal notebook default when unset).
It must differ from the notebook and runtime registry roots. All path components
are opened without following symlinks. Directory is user-owned 0700; the DB,
SQLite WAL/SHM and owner lock are private 0600 regular files with no hardlinks.
One OS owner lock per application root prevents multiple control planes. This
remains a single-node, single-trusted-owner, loopback deployment without end-user
authentication. Private storage is not encryption at rest.

The new `application.sqlite3` uses WAL, foreign keys, FULL synchronous commits,
`BEGIN IMMEDIATE` and a short thread mutex. Lock order is notebook mutex, then
application mutex, then SQLite transaction. Worker threads keep file/DB work off
the event loop. There are no locks held through Gateway execution. SQLite's
ordinary committed/uncommitted WAL recovery handles process interruption.

Schema migration is transactional, `PRAGMA user_version: 0 -> 1`. Unknown versions
fail closed. Integrity and foreign-key checks run on open. Tables:

| Table | Purpose |
|---|---|
| `catalog` | Stable notebook IDs and observed file presence, including missing historical IDs |
| `records` | UUID identity, kind, project/notebook/parent FKs, global monotonic sequence, revision, timestamps, bounded typed JSON data |
| `memberships` | One current project per notebook; association/disassociation flag |
| `versions` | Immutable roadmap snapshots, primary key `(roadmap_id, revision)` |
| `operations` | Global operation ID, request fingerprint, original committed response |

Record kinds are project, conversation, message, roadmap, comment and activity.
Project/notebook/parent identity columns have SQL foreign keys. Task graphs,
message replies, cell/receipt references and their project scope are validated
inside the transaction; tasks are embedded in versioned plan JSON, not a separate
mutable task table. DB triggers prohibit updates to messages/events and deletion
of historical records, and prohibit changes/deletion of roadmap snapshots.
No cascading filesystem or runtime deletion exists.

At startup and before each application operation, existing `.ipynb` identities
are indexed without rewriting documents, outputs, metadata or receipts. Missing
files mark catalog entries absent; historical records are retained. The notebook
store's mutex serializes observation with notebook mutation in this process.
Catalog observations commit with the application transaction. A crash between a
notebook file write and application transaction leaves a discoverable standalone
notebook or missing historical reference. The next operation/startup reconciles
presence. **There is no claimed atomic transaction across SQLite and files.**
Application operations never create/delete/edit `.ipynb` files; membership is
entirely in SQLite. Do not edit/delete managed files externally while Europa runs.

Assign existing notebooks with the membership API below. New notebooks use the
unchanged `POST /api/notebooks` then the same association call. A disconnect
between those two requests leaves a safe standalone notebook. To change projects,
explicitly disassociate then associate; history keeps its original project scope.
An existing notebook ID and its outputs never change as a side effect.

Archive freezes content writes and can be reversed by an explicit revision-checked
project update. Delete is a permanent logical tombstone; historical data remains
readable and membership remains reserved until an explicit revision-checked
disassociation (permitted even for archived/deleted projects) releases it.
Neither operation deletes notebook files, receipts or datasets, nor stops an active
runtime. Notebook deletion keeps its Phase 3A document-only behavior and historical
application references become absent/orphaned. Explicit runtime DELETE remains
the sole manual runtime destruction API. Hard purge and receipt GC are deferred.

## API conventions

All paths below start with `/api`. Full machine-readable request and response
schemas are in `/openapi.json` and Swagger `/docs`. Request models reject unknown
fields. New application mutations require `operation_id` (1–128 ASCII letters,
digits, underscore or hyphen). Retry the exact request with the same ID to receive
the original committed response, including its original revision and anchor
observation; GET retrieves current state. Reusing an ID for a different request,
resource or server author returns 409. Operations and their replay records commit
in one transaction; there are no success replay records for rolled-back writes.
Operation history is retained without age-based pruning.

Mutable resources require the exact integer `revision`; messages/activity are
append-only. Record envelope:

```json
{"id":"UUID_HEX","project_id":"PROJECT_OR_NULL","notebook_id":null,
 "revision":1,"created_at":1790000000.0,"updated_at":1790000000.0,
 "sequence":1,"data":{"title":"Research","description":null,
 "status":"active","file_references":[],"notebooks":[]}}
```

IDs are server-generated UUID hex strings. Timestamps are UTC Unix seconds.
Sequence numbers are globally increasing committed record positions, not
necessarily contiguous within a project/conversation. Ordered list APIs accept
`after=0&limit=50` (maximum 100) and return `{"items":[...],"next_cursor":N}`.
When `next_cursor` is null, the current page is exhausted. Resume with `after=N`.
Version history uses roadmap revision as its cursor. Project/roadmap updates do
not change their original list position. Deleted/archived projects remain listed
with an explicit status for history.

Missing resource is 404; stale revision, reused operation or invalid/currently
unavailable membership is 409; malformed input/reference/graph is 422 (missing
notebook/cell/receipt can be 404); oversized application result is 413; unsupported
server-authored event on public ingress is 403. Unsafe storage is 503. Error bodies
use `detail`, with FastAPI validation detail arrays for schema errors. Application
responses contain no host paths, Gateway tokens, URLs or ownership registry data.
User-supplied text is content: do not place credentials or private scratchpads in
public summaries. No runtime diagnostics or outputs are copied into activity.

| Method/path | Contract |
|---|---|
| `POST /projects` | `operation_id,title,description?`; 201 record |
| `GET /projects`, `GET /projects/{id}` | Paginated projects / one record |
| `PATCH /projects/{id}` | `operation_id,revision,title,description?,status` (active/archived/deleted) |
| `DELETE /projects/{id}` | JSON `operation_id,revision`; 200 tombstone record |
| `GET /projects/{id}/notebooks` | Membership list with notebook ID and presence flag |
| `POST /projects/{id}/notebooks` | `operation_id,revision,notebook_id,associated` (default true); updated project |
| `POST /conversations` | `operation_id,project_id,notebook_id?,title?`; 201 |
| `GET /conversations?project_id=...`, `GET /conversations/{id}` | Paginated list / record |
| `POST /conversations/{id}/messages` | `operation_id,content,parent_id?,state?` plus optional links; 201 |
| `GET /conversations/{id}/messages` | Ordered paginated history |
| `POST /roadmaps` | `operation_id,project_id,notebook_id?,objective,tasks`; 201 |
| `GET /roadmaps?project_id=...`, `GET /roadmaps/{id}` | Paginated list / record |
| `PUT /roadmaps/{id}` | `operation_id,revision,objective,tasks`; new version |
| `GET /roadmaps/{id}/versions` | Immutable ordered snapshots, paginated by revision |
| `GET /roadmaps/{id}/tasks` | Ordered sidebar task list |
| `PUT /roadmaps/{id}/tasks/{task}` | `operation_id,revision,task`; atomic transition + new roadmap version |
| `POST /notebooks/{id}/comments` | `operation_id,project_id,notebook_id,cell_id,content` plus anchor/reply fields; 201 |
| `GET /notebooks/{id}/comments` | Paginated history, including missing cells/notebooks |
| `GET /comments/{id}` | Current comment/thread anchor observation |
| `PATCH /comments/{id}` | Root thread only: `operation_id,revision,resolved` |
| `POST /projects/{id}/activities` | `operation_id,project_id,summary,result?` plus links; public owner intervention only; 201 |
| `GET /projects/{id}/activities` | Ordered paginated activity; sequence suitable for future SSE cursors |

Optional links are `task_id,message_id,notebook_id,cell_id,document_revision,
execution_receipt_id`. Scoped conversations enforce their notebook scope. New
references must identify a current member notebook and existing cell/receipt;
revision cannot refer to a future document. Historical task references remain
available in immutable versions after changed requirements. Existing task links
remain historical when their cells disappear; changing links requires fresh
validation. Messages and events are immutable even after notebook disassociation.

REST messages/comments always have server-assigned `author_role=user`; public
activity always has `actor=user` and `event_type=user_intervention`. Client-supplied
author/role/actor fields are rejected. Private Python storage methods can accept
server-controlled `coordinator`, `coding_agent`, or `system/tool` authors. This is
an extension boundary, not an authentication scheme or implemented agent service.
Supported internal event types include task_created, plan_approved, task_started,
cell_added, cell_updated, execution_requested, execution_succeeded,
execution_failed, coordinator_review_requested, task_blocked and task_completed.
Their actor is selected by trusted server code. Summaries are bounded plain text
with HTML tags/control characters removed; rendering still must escape them.
No conversation/comment/event text is ever executable code, and these routes
cannot provision a runtime. Store public activity/outcomes, never hidden reasoning.

## Roadmap and sidebar contract

A roadmap update is a full versioned plan, or an atomic replacement of one task.
The task list defines display order. IDs are unique across a project's roadmap histories;
`parent_id` gives subtasks. Dependencies and parents must exist in the same plan;
self-links and cycles in the combined parent/dependency graph are rejected.
Changing requirements preserves all earlier plans and their task IDs/evidence.
Tasks cannot reference another project's messages or receipts. Receipt links
verify cell identity where provided.

Task statuses: pending, in_progress, blocked, needs_input, completed, cancelled.
Blocked/needs_input require explanations. Every task needs nonblank acceptance
criteria. Completion requires explicit `accepted=true` and nonempty evidence;
the server records `accepted_by=user`. This is owner review, not a Coding Agent
self-declaration. Evidence has a required human-readable summary and optional
message/receipt IDs. Execution evidence must be an applied successful receipt
for the linked notebook/cell. Failed, uncertain and conflict receipts do not prove
completion. Dependencies must already be completed, and all children completed
or cancelled, before a parent is accepted. Future Coordinator acceptance must
use a separate trusted review service; it is not implemented here.

Progress = completed accepted weight / all non-cancelled weight * 100. Each task
**and subtask** has weight 1 unless explicitly configured. Parent acceptance counts
as work alongside child acceptance; a parent still awaiting review prevents 100%.
Task progress uses that task and its descendants. Empty/all-cancelled plans have
0%, and all values remain within 0–100. No arbitrary progress input is accepted.

Responses contain `data.progress`, `overall_display` (label and right-positioned
percentage) and each task's `progress` and `display` flags. Completed tasks have
compact/checkmark=true; pending tasks are compact; only in_progress has
show_progress=true and compact=false. Blocked/needs_input show their explanation.
Task links identify cells, messages and executions when selected. No UI is built.
Snapshots preserve raw plan data/evidence; presentation progress is derived in
current roadmap responses, not stored as agent claims.

## Comment anchors and untrusted rendering

Code ranges are half-open Python Unicode character offsets `[start,end)` into
normalized cell source, not byte or line offsets. Creating a root comment captures
the current document revision and SHA256 source fingerprint. A supplied revision
or fingerprint must match. Moving a cell keeps its ID/fingerprint and anchor
`current`; changing source flags `outdated`; deleting the cell/notebook flags
`orphaned`. Original offsets/fingerprint remain intact. Replies retain the original
thread anchor, including after deletion. Resolution updates the root thread with
revision checking; reply responses inherit its current resolution. Comment content
is retained without edit/delete endpoints. Classification supports question,
suggestion, change_request and stop_request; it does not trigger execution or
interrupt anything.

All notebook outputs and user text remain untrusted data. Standard MIME bundles
are preserved, including HTML/SVG/JavaScript and external references. A renderer
must escape text, sanitize or isolate active markup, disable JavaScript and block
automatic external fetches as specified in the [rendering contract](phase3b-safety-review.md).
No renderer is included, and backend schema validation is not sanitization.

## Example workflow

The following bodies use placeholders for returned IDs and revisions. Each
operation ID must be unique except when retrying the exact same operation.

```sh
curl -s http://127.0.0.1:8000/api/projects -H 'Content-Type: application/json' \
  -d '{"operation_id":"project-1","title":"Sales analysis","description":"Explore regional trends"}'
curl -s http://127.0.0.1:8000/api/notebooks -H 'Content-Type: application/json' \
  -d '{"title":"Sales"}'
curl -s http://127.0.0.1:8000/api/projects/PROJECT/notebooks -H 'Content-Type: application/json' \
  -d '{"operation_id":"associate-1","revision":1,"notebook_id":"NOTEBOOK"}'
curl -s http://127.0.0.1:8000/api/conversations -H 'Content-Type: application/json' \
  -d '{"operation_id":"conversation-1","project_id":"PROJECT","notebook_id":"NOTEBOOK"}'
curl -s http://127.0.0.1:8000/api/conversations/CONVERSATION/messages -H 'Content-Type: application/json' \
  -d '{"operation_id":"message-1","content":"Compare regional totals","state":"published"}'
curl -s http://127.0.0.1:8000/api/roadmaps -H 'Content-Type: application/json' \
  -d '{"operation_id":"plan-1","project_id":"PROJECT","notebook_id":"NOTEBOOK","objective":"Compare regional totals","tasks":[{"id":"aggregate","title":"Aggregate totals","acceptance_criteria":["Owner verifies totals"]}]}'
curl -s http://127.0.0.1:8000/api/notebooks/NOTEBOOK/cells -H 'Content-Type: application/json' \
  -d '{"revision":1,"source":"print(6*7)"}'
curl -s http://127.0.0.1:8000/api/notebooks/NOTEBOOK/comments -H 'Content-Type: application/json' \
  -d '{"operation_id":"comment-1","project_id":"PROJECT","notebook_id":"NOTEBOOK","cell_id":"CELL","content":"Verify the aggregate","classification":"question"}'
curl -s -X PUT http://127.0.0.1:8000/api/roadmaps/ROADMAP/tasks/aggregate -H 'Content-Type: application/json' \
  -d '{"operation_id":"review-1","revision":1,"task":{"id":"aggregate","title":"Aggregate totals","acceptance_criteria":["Owner verifies totals"],"status":"completed","accepted":true,"evidence":[{"summary":"Owner inspected the saved totals"}],"links":{"notebook_id":"NOTEBOOK","cell_id":"CELL","message_id":"MESSAGE"}}}'
curl -s http://127.0.0.1:8000/api/projects/PROJECT/activities -H 'Content-Type: application/json' \
  -d '{"operation_id":"intervention-1","project_id":"PROJECT","task_id":"aggregate","summary":"Owner reviewed totals","result":"succeeded"}'
curl -s 'http://127.0.0.1:8000/api/conversations/CONVERSATION/messages?after=0&limit=20'
curl -s -X DELETE http://127.0.0.1:8000/api/projects/PROJECT -H 'Content-Type: application/json' \
  -d '{"operation_id":"delete-1","revision":2}'
```

No Python executes in this workflow unless the separate existing notebook execute
endpoint is explicitly called. Application records do not act as execution queues.

## Limits and next milestone

Histories, operation responses and receipts currently grow without aggregate disk
quotas or GC. Application responses are capped at 2 MiB per mutation; messages and
comments at 32,000 characters; summaries at 2,000; plans at 256 tasks with bounded
criteria/evidence. Catalog reconciliation scans notebook documents each operation,
which favors correctness over large-library throughput. No multiworker/distributed
coordination, external file editing, authentication, hard purge, automated review,
streaming or upload service is supported. Keep the SQLite directory and notebooks
in the same owner deployment and back them up together while stopped.

Recommended next milestone: owner-facing notebook/sidebar UI against these APIs,
with sanitized output rendering, explicit review controls and stale-anchor UX.
Keep agent orchestration separate until trusted authorship, Coordinator acceptance
and scoped dataset transfer have their own reviewed contracts.
