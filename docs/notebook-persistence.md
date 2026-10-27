# Phase 3A: notebook persistence and cell management

Phase 3B now adds a separate [application persistence layer](conversational-notebooks.md)
without changing these notebook contracts. Its prerequisite [safety review](phase3b-safety-review.md)
defines workspace boundaries, conservative receipt retention and mandatory output
rendering protections. The future-work statements below describe the Phase 3A
snapshot; application projects/conversations/roadmaps/comments are implemented in 3B.

Implemented on `feature/notebook-persistence`, based on
`e3a4d4e85bceabc0364e2fa74bdab6dde2d3f89e`. The implementation commit is recorded in
Git history and the completion response. Runtime ownership/admission, private CNI,
prewarming and idle cleanup retain their existing implementations. No image,
runtime binary, daemon configuration, networking change or new service is needed.
The already locked `nbformat==5.11.1` dependency is now explicitly declared in
`requirements.in`; package versions and hashes remain unchanged.

## Storage and standard documents

Set `EUROPA_NOTEBOOK_ROOT` to a dedicated private persistent directory. The default
is `$XDG_DATA_HOME/europa/notebooks`, or `~/.local/share/europa/notebooks`. The final
directory must be user-owned with no group/other permissions; newly created
folders/files use 0700/0600. Choose a path without symlink components, including
ancestor directories. A nonblocking OS `.owner.lock` permits one control plane per
storage root. Multiple Uvicorn workers/shared-filesystem coordination remain
unsupported, consistent with the runtime registry's single-owner deployment.

Documents are ordinary UTF-8 JSON **nbformat 4.5** `.ipynb` files, created and
validated with Jupyter's nbformat library. Jupyter can read them directly. Source
may be stored as a string or standard multiline-string array. Code/markdown cells,
stable cell IDs, ordering, arbitrary standard notebook/cell metadata, execution
counts and standard outputs are preserved. Raw cells/older format conversion are
not implemented. Markdown attachments can be retained in a valid whole-document
save; this adds no file-upload endpoint.

Each generated notebook UUID has one `<notebook_id>.ipynb` file. Titles are metadata,
never filenames. Server metadata in `metadata.europa` contains `notebook_id`, `title`
and a monotonically increasing integer `revision`. Cell `metadata.europa` is
reserved for execution ID/status. Code cells use normal `outputs` and
`execution_count` fields; there are no chat cells or custom execution output types.
Standard MIME bundles remain standard nbformat data rather than flattened text.

The root also holds private `<notebook_id>.<execution_id>.execution.json` journal
receipts. These are operational sidecars, not a replacement notebook format. They
record notebook/cell IDs, expected document revision, source fingerprint, execution
ID, verified runtime/generation/kernel identity, Jupyter request/kernel-session IDs,
completion state and reduced standard outputs. No Gateway token, endpoint, lease
token or host storage path is written to documents or receipts by Europa.

All paths use generated validated hex IDs. A pinned directory descriptor and
`openat`-style operations keep file access inside the root. Symlinks are rejected
at every root component and file open; nonregular, public, foreign-owned or
multiply hardlinked files are rejected before content is read. The API does not
accept arbitrary filenames or expose directory browsing. Titles such as `../name`
remain harmless metadata. Files/documents/receipts are bounded to 16 MiB; execution
source is at most one million characters and the Gateway's existing output budget
still applies. Nonfinite JSON numbers, invalid Unicode, duplicate/missing cell IDs
and invalid notebook schemas are rejected.

## Notebook API

All paths below have prefix `/api`. Document responses are standard notebooks;
read their revision from `metadata.europa.revision`. IDs are opaque generated
identifiers. JSON request bodies reject unknown fields.

| Method/path | Request / result |
|---|---|
| `POST /notebooks` | `{ "title": "Example" }` (optional title); 201 standard notebook |
| `GET /notebooks` | List of notebook identity/title/revision metadata |
| `GET /notebooks/{id}` | Full saved standard notebook, with code and outputs |
| `PUT /notebooks/{id}` | `{ "revision": N, "document": {...} }`; validated replacement |
| `PATCH /notebooks/{id}` | `{ "revision": N, "title": "New title" }` |
| `DELETE /notebooks/{id}?revision=N` | 204; remove document, preserve execution receipts |
| `POST /notebooks/{id}/cells` | `{ "revision": N, "cell_type": "code", "source": "print(42)", "metadata": {}, "index": 0 }`; 201 updated notebook |
| `GET /notebooks/{id}/cells/{cell}` | `{ "revision": N, "cell": {...} }`, including saved outputs |
| `PATCH /notebooks/{id}/cells/{cell}` | `{ "revision": N, "source": "...", "metadata": {...} }`; metadata optional |
| `DELETE /notebooks/{id}/cells/{cell}?revision=N` | Updated notebook |
| `POST /notebooks/{id}/cells/{cell}/move` | `{ "revision": N, "index": 0 }`; final zero-based position |
| `POST /notebooks/{id}/cells/{cell}/type` | `{ "revision": N, "cell_type": "markdown" }` |
| `POST /notebooks/{id}/cells/{cell}/clear` | `{ "revision": N }`; clear code outputs/count |
| `POST /notebooks/{id}/cells/{cell}/execute` | `{ "revision": N, "kernel_id": "...", "timeout": 30 }`; 202 receipt; kernel ID optional |
| `GET /notebooks/{id}/executions/{execution}` | Durable receipt, result and application/conflict/uncertain state |

Insert defaults to a code cell appended at the end; source/metadata/index are
optional. Editing source requires an explicit source value. Changing cell type
preserves its ID/source/metadata, removes type-specific fields and starts code
outputs/count empty. Moves preserve IDs. Source/type/output edits invalidate the
cell's previous execution marker. Whole-document saves also invalidate markers
on changed cells. Notebook identity/title/revision are managed by the server;
renaming uses PATCH, not a forged metadata replacement.

A missing notebook/cell/execution returns 404; stale revisions/busy document execution
return 409; invalid format/input returns 422; oversized persisted data returns 413;
unsafe/unavailable storage returns 503. Storage I/O failure can still leave the
old or new complete revision committed: reopen/query after an ambiguous request,
rather than retrying an old revision blindly. This is optimistic concurrency, not
live collaboration or end-user authorization.

## Atomic writes and recovery

The store serializes short read/compare/write transactions with a thread mutex.
Filesystem I/O runs in worker threads, not while holding the event loop through
kernel execution. Saves require the exact current integer revision. Concurrent
writers starting from one revision produce one commit and one 409; they never
silently overwrite each other. There is no notebook lock held across provisioning,
Gateway I/O or Python execution.

Each file commit writes a private exclusive temporary file, flushes/fsyncs it,
atomically replaces the destination using the pinned root descriptor, then fsyncs
the directory. Interrupted temporary files are never opened as notebooks; startup
removes only validated private files matching Europa's temporary-file pattern.
Committed files contain either the old or new valid JSON document. Invalid files
are surfaced for operator repair, never replaced with an empty document.

Cross-file execution commits use a write-ahead receipt:

```text
validate revision/cell -> commit prepared receipt -> commit running cell marker
resolve verified runtime/kernel -> commit running receipt -> execute via Gateway
commit completed result receipt -> CAS notebook result -> commit applied receipt
```

The expected result revision is the revision after the running marker was saved.
Completion also verifies cell identity, source hash and exact execution marker.
**Any intervening document edit**, even moving a different cell, causes a conflict
instead of merging a delayed result into the newer document. The completed outputs
remain in the conflict receipt for retrieval; no newer source, outputs or cell
order is overwritten. Re-execution has a new ID and invalidates the old result.
Deleting a cell/notebook cannot cause completion to recreate it.

Startup replays a `completed` receipt only when its CAS checks still succeed.
If the document was committed but receipt acknowledgement was interrupted, matching
revision/marker/source/outputs/count make replay idempotent. A newer edit instead
leaves a conflict receipt. `prepared`/`running` leftovers become `uncertain`, never
successful merely because the control plane restarted. When the pending marker is
still unchanged, the cell is marked uncertain as well. The receipt is authoritative;
a marker in a concurrently edited document may retain its old running intent while
the separate receipt reports conflict. There is no automatic execution retry.

Accepted execution tasks belong to `NotebookService`. POST returns 202 after its
prepared receipt and pending document revision are durable. Closing an HTTP client
or cancelling its waiter cannot cancel the owned task. Only one document execution
per notebook is admitted at a time; duplicate submissions get 409. Kernel operations
also retain GatewayClient and Step 2F's durable operation protection. Admission,
transport, ownership or timeout failures are recorded conservatively as uncertain
with a sanitized failure class; they do not fabricate Jupyter success or output.
On graceful service shutdown, a bounded drain precedes task cancellation and runtime
shutdown. Runtime shutdown retains its own bounded drain, so both stages can add
to total shutdown time. Incomplete document outcomes stay uncertain; Step 2F retains
uncertain remote operations/reservations. Local-provider shutdown keeps its existing
process cleanup and limited adoption guarantees.

Back up the complete root, including receipts, with the writer quiesced or a coherent
filesystem snapshot. A standalone `.ipynb` is sufficient for standard Jupyter
viewing, but loses Europa's receipt/replay history if copied alone. Do not unlink
owner-lock files while a writer is alive. Do not concurrently edit storage files
outside Europa: filesystem editors do not participate in API revision checks.

## Kernel execution and output handling

Document identity is the supervisor's logical notebook identity. The service uses
that notebook's verified provider/client. A saved binding is reusable only when
runtime ID/generation match and GatewayClient verifies the kernel is owned by that
notebook. With no binding, a pending warm bootstrap kernel is delivered through the
existing client contract; a single existing owned kernel may be reused, otherwise a
new one is created. Multiple existing kernels require an explicit `kernel_id`.
Another notebook's kernel ID is rejected before code dispatch.

Gateway execution must produce its correlated shell reply and IOPub idle event.
Application `error` replies, including `KeyboardInterrupt`, are valid terminal
results and persist standard error outputs/counts; they are never labelled `ok`.
The reducer preserves stdout/stderr order, MIME data and metadata in `display_data`
and `execute_result`, traceback errors, and execution counts. `clear_output(wait)`
has deferred/immediate semantics. `update_display_data` updates matching displays
within the current execution and eligible previous cells. Cross-cell updates require
matching runtime/generation/kernel **and kernel session**, unchanged cell source and
its original applied execution marker. A restarted interpreter cannot update an old
session's displays. Transient display IDs are kept in private receipts, not inserted
as invalid nbformat output fields. Unmatched/unverified display IDs are ignored.

The implementation follows the [standard notebook format](https://nbformat.readthedocs.io/en/latest/format_description.html)
and [Jupyter messaging contract](https://jupyter-client.readthedocs.io/en/stable/messaging.html).
Only the correlated messages collected through execution completion are saved.
Background output/display changes after idle, widget comm state, stdin prompts,
streaming updates and crash-resumable channel replay are outside this milestone.
Rich output is stored, not rendered or trusted/signed by Europa; future renderers
must apply an explicit sanitization/trust policy.

Deleting a runtime or automatically retiring an idle allocation leaves notebook
files and receipts intact. Reopening does not provision a kernel. A new interpreter
starts clean; saving code/outputs does **not** checkpoint Python variables, files in
`/workspace`, threads or arbitrary process state. A surviving verified live kernel
can retain its variables and be re-associated with its original notebook, as already
supported by Step 2D; busy/uncertain recovery remains conservative.

Deleting a document does not implicitly stop a running runtime/execution. Runtime
stop/interrupt/delete retain their existing explicit APIs and idle policy; a deleted
document's delayed results become a receipt conflict. This keeps file deletion from
silently interrupting accepted work. Receipts/tombstones have no pruning policy in
3A, so history grows and listing/scanning cost is proportional to stored files.
This is node-local persistence: deleting or losing the VM disk loses documents
unless they have been backed up separately.

## Project boundary and Phase 3B recommendations

Notebook IDs, stable cell IDs, document revisions and execution IDs are available
for future plans/tasks/comments/conversation references. No conversational or agent
records are inserted into the notebook format. Runtime images, working-directory
mount prohibitions and environment filtering remain unchanged; the host notebook
root is never mounted into a container or exposed as its workspace.

A future project layer should have its own verified project IDs and persistent file
root, separate from notebook document storage. Review explicit per-project mounts,
read-only dataset inputs, writable-output ownership, path validation, resource
accounting and recovery before extending the provider's current mount boundary.
Do not mount the notebook root, arbitrary host paths or all projects into a runtime.
Uploaded datasets, project management and workspace-file persistence are not added.

Before Phase 3B, review these API/revision/result contracts with the intended client,
define receipt retention/storage quotas and output trust, and specify the project
boundary. Crash-resumable execution/output delivery would need a separate durable
protocol; do not infer it from saved `.ipynb` files. No Coordinator/Coding Agent,
MCP, chat, Roadmap sidebar, frontend, multi-user authorization, distributed storage
or collaborative editing is implemented. Runc retains its shared-kernel isolation
limitation; experimental runsc support/evidence is preserved.

## Validation

Tests use the existing ARM64 Lima VM, cached digest-pinned image and containerd+runc.
New real probes use the existing 0.25 CPU / 384 MiB / 128 PID profile with 0.5 CPU /
768 MiB ceilings: at most two containers, sequential test workloads. No image rebuild,
package/runtime replacement, configuration write or network change is required.

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc) and not test_real_cold_warm_benchmark' -q
.venv/bin/python -m pytest tests/test_notebooks.py tests/test_notebook_integration.py \
  --run-containerd -q
```

The daemon-restart regression retains its separate
`EUROPA_TEST_CONTAINERD_RESTART=1` opt-in. The normal selector excludes only the
14 deferred real-runsc cases and separately validated long warm/cold benchmark.
New unit tests use controlled events and injected write failures. Real crash tests
use bounded checkpoints; active execution synchronizes on Jupyter `execute_input`.
No timing-dependent workaround, arbitrary workload sleep or output-only success is
used. Jupyter `ok` replies plus expected saved output verify successful Python;
intentional error/interrupt replies must preserve their actual error status.

Validation on October 24, 2026, on the existing 2 vCPU / 1950 MiB ARM64 VM:

| Run | Actual result |
|---|---|
| Broad Python regressions, including real local/runc, enforcement, SIGKILL, prewarming, idle cleanup and actual daemon restart | **267 passed, 15 deselected**, 516.55 seconds |
| Final focused notebook/identity/output checks, all prior unit/protocol tests and eight real notebook probes | **196 passed**, 87.68 seconds |
| Final host unit/protocol checks | **188 passed**, 2.22 seconds |
| Native Linux Go bridge | **6 passed**, including fencing/ownership/network validation |

The broad run precedes final kernel-selection/session guards and six additional
cases; the final focused run verifies those refinements. The final implementation
has **40 new notebook unit cases and eight new real probes**. No final failures or
infrastructure skips remain in these selections. No failed/application-error or
output-only execution was counted as a successful Python result.

Unit tests verify standard `.ipynb` interoperability, CRUD/cell ordering/types,
metadata, atomic replace failure, exclusive ownership, traversal/symlink/hardlink
rejection, malformed JSON/schema rejection, concurrent revision conflicts, delayed
result conflicts, cancelled callers, temporary-write recovery and replay on both
sides of the document/receipt commit. Output tests cover MIME/JSON data, stdout,
stderr, immediate/deferred clear, traceback errors, updates across cells and rejection
of updates from a restarted kernel session. Initial test development corrected an
owner-lock harness path that itself hit the intended symlink guard.

Real tests execute and save Python, reopen with code/outputs intact after explicit
runtime destruction, start a clean replacement interpreter, and verify execution
counts reset on kernel restart. Rich display updates modify their original cell;
stdout/stderr, clear-output semantics, execute results, ValueError traceback and
KeyboardInterrupt are persisted in standard format. Closing the HTTP client after
202 leaves work busy until explicit interrupt. Actual idle cleanup preserves the
saved document and releases its reservation. Another notebook's kernel ID is
rejected before code dispatch.

SIGKILL checkpoints verify three boundaries: applied results, completed results
before cell commit, and a running execution. Applied/completed results reopen and
reuse the original verified runtime and kernel, including its Python value `42`.
A running execution's receipt/cell become uncertain while the runtime remains in
reconciliation with its full reservation, until explicit verified cleanup. Runtime
state recovery is conditional, not Python checkpointing.

Final independent audit: no Europa tasks/containers, managed snapshots, runtime
state directories, allocated private IPAM addresses, Gateway/kernel/control-plane
processes or SDK helpers. Real fixture disposal verifies namespace emptiness and
reservation release after destruction. Cached images and permanent coordination
files are preserved. Containerd/system-CNI configuration archive hashes, the private
CNI profile hash, routes and firewall rules match the saved Step 2B baseline.
`europa0` remains 10.77.0.1/24 and IPv4 forwarding remains **0**. No network rollback
is required. Final available memory was approximately **1497 MiB**, and free root
disk **5.35 GiB**; these are final snapshots, not peak resource measurements.

Evidence includes `final-suite.{log,xml}`, `focused-final.{log,xml}`,
`integration.{log,xml}`, `go-final.log`, `final-audit.json` and host `unit-host.log`.
Only diagnostic logs/audits are copied to host evidence; private document/receipt
and runtime-registry test directories remain outside Git.

Ignored host evidence is under `test-results/notebook-validation/evidence/`; guest
logs are under `~/europa-validation/notebook-validation/`. Raw private notebooks,
execution receipts, journals, tokens and temporary test state are excluded from Git.
This milestone stops at Step 3A.
