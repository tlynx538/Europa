# Step 2D: durable single-node runtime ownership

Step 2F adds [durable activity leases and idle cleanup](runtime-lifecycle.md) to assigned
containerd runtimes. Unassigned warm resources retain their separate pool policy.

Step 2E extends this baseline with [clean runtime prewarming](runtime-prewarming.md),
transactional schema-version-2 migration and a separate immutable allocation label
versus mutable notebook binding. The validation and Step 2D boundaries below remain
the historical starting point; current pool lifecycle and operation are in that report.

Implemented on `feature/runtime-recovery`, based on
`9a064c5e57e1558c3cb7a6cc14399228a1149729`. The implementation commit is recorded in
Git history and the completion response. SQLite from Python's standard library is
the only added storage mechanism; no service or infrastructure was installed.

## Deployment and ownership boundary

The production containerd deployment automatically opens a durable registry before
accepting provisioning requests. Set `EUROPA_RUNTIME_REGISTRY` to an absolute file
path in a private directory on persistent local storage. The default is
`$XDG_STATE_HOME/europa/runtime.sqlite3`, or
`~/.local/state/europa/runtime.sqlite3`. The final directory must be owned by the
control-plane user and mode 0700; the database must be a regular user-owned file
with mode 0600. Newly created directories/files have those permissions. Final
symlinks and public database files are rejected. Preserve the database, WAL and SHM
as a coherent SQLite database; do not copy only the main file from a running writer.
The database stores a SHA-256 credential fingerprint, never the plaintext Gateway
token. Treat all ownership metadata as private operational state.

One control-plane process owns the registry at a time, enforced by a nonblocking
OS file lock next to the database. The existing namespace lock additionally
prevents cooperating controllers using different registry files from claiming the
same canonical containerd socket / `europa` namespace. All instances for that
namespace must use the same namespace-lock configuration. Multiple Uvicorn workers
and distributed/shared-filesystem coordination remain unsupported. A process crash
releases its OS locks; a second process cannot simply ignore surviving allocations.
Never unlink coordination files while helpers or controllers may still be running.

The registry is bound to the canonical socket, explicit `europa` namespace, CNI
configuration/bin paths and runtime-state root. A changed binding fails startup;
it is not an implicit migration. The namespace is dedicated to Europa. Inventory
queries never visit another containerd namespace. Unknown allocations inside this
explicit namespace block new admissions and are not automatically adopted/deleted.
Legacy Step 2C containers without registry-owner labels require operator review.
There is no automatic import of old, unjournaled containers. Quiesce legacy
controllers/helpers before upgrading, and do not run unfenced privileged provider
operations alongside the durable control plane.

Notebook identity remains the existing logical `notebook_id`. The registry's stable
installation-owner UUID and exact notebook/runtime/generation labels prevent
recovery from matching a different notebook or installation merely by an ID/IP.
The API still has no end-user authentication: this is one trusted owner domain,
restricted to loopback, not a multi-user authorization service. Do not expose it
to mutually untrusted users or treat arbitrary notebook identifiers as proof of
user identity. Containerd and the privileged bridge remain trusted operator APIs.

## Durable schema and transaction boundaries

SQLite uses WAL, `synchronous=FULL`, immediate write transactions, a bounded busy
timeout and an integrity check on open. Initial directory entries are fsynced.
A unique partial index allows only one non-destroyed allocation per notebook.

| Table | Durable fields |
|---|---|
| `metadata` | Schema version, deployment scope, stable owner UUID, control-plane session epoch |
| `allocations` | Runtime/container UUID, notebook ID, owner UUID, provider, optional profile ID, CPU/memory/PID reservation, image digest, runtime handler, generation, credential hash, lifecycle phase, reason, created/updated timestamps |

Lifecycle transitions:

```text
admission reservation -> committed provisioning record -> container creation
provisioning -> committed next generation + credential hash -> task start
provisioning -> verified limits + Gateway/kernel readiness -> committed active
active/provisioning -> committed deleting -> confirmed destruction -> committed destroyed
uncertain ownership/readiness -> committed reconciliation (reservation retained)
```

Generation advances before a task starts. Adoption and deletion also verify that
container metadata references its own canonical rootfs snapshot, never another
allocation's snapshot. Container metadata records the generation
with its process spec; private runtime state records owner/notebook/generation before
CNI attachment. Both container and snapshot carry ownership labels. Activation
requires matching ownership, generation, image/handler, admitted limits, authenticated
Gateway readiness and recoverable kernel inventory. CPU quota comparisons account
for the existing integer-microsecond OCI rounding; reservations keep full precision.

Reservations are restored from all non-destroyed rows before capacity detection or
inventory, even if they exceed today's budget or refer to removed profile names.
A journal-write failure before creation prevents every runtime side effect. A crash
mid-transaction leaves either the old committed state or the new committed state.
Destruction commits `deleting` before closing clients or issuing destructive calls.
It commits `destroyed` only after SDK destruction and an empty per-ID task/container/
snapshot/network-state inventory. Only then does admission regain capacity.
Repeated destruction is idempotent; failed confirmation retains the reservation.
Destroyed rows are tombstones, not live reservations. No history pruning is added.

## Quiescing orphaned helpers

SIGKILL can leave an SDK helper executing after its parent disappears. Each durable
bridge operation therefore takes a process-shared exclusive operation lock scoped
to the canonical socket and dedicated namespace. The root-owned private lock/fence
file lives at `/tmp/europa-runtime-operations-<scope hash>.lock`; the bridge validates
that exact derived path and rejects symlinks, unsafe file ownership and permissions.
It holds this lock through completion of the SDK operation.

Startup creates and commits a fresh session epoch. Its first inventory operation
waits for the operation lock, writes/fsyncs the new epoch, and inventories while
holding the lock. An old helper already executing finishes before this inventory.
A queued helper from the old control plane fails its epoch check before any SDK
side effect. An operation-lock timeout does not become evidence of absence: startup
retains reservations and blocks new admission. This is fencing on one node, not a
distributed lease. Operations have the existing bounded SDK deadlines.

## Startup reconciliation

Before provisioning is exposed:

1. Acquire namespace and registry ownership locks; validate the journal and scope.
2. Restore every durable reservation without applying the new-admission budget.
3. Fence prior SDK sessions, then read complete container, task, snapshot and runtime
   state inventories from the dedicated namespace. Invalid/incomplete/unavailable
   inventory blocks all new provisioning, retains records and serves diagnostics.
4. Block new admissions if any unrecorded allocation exists. Verify exact owner,
   notebook and runtime labels on known containers/snapshots. Configuration mismatch
   or ambiguous ownership also blocks admission and forbids automatic destruction.
5. If complete inventory proves all resources for a record absent, commit its
   destruction and release its reservation. An in-memory disappearance is insufficient.
6. Resume an interrupted `deleting` record only after ownership checks; verify actual
   absence before release. Unverifiable cleanup remains reserved for explicit retry.
7. For an active or sufficiently completed provisioning record, verify generation,
   credential fingerprint, approved private endpoint, enforced controls and Gateway
   health. Reconnect only kernels reported idle with valid IDs and successful standard
   Jupyter kernel-info handshakes. Recover their original IDs and kernel process state.
8. Put incomplete, crashed, busy or unready allocations in reconciliation with their
   full reservations retained. Unverifiable identity/controls/readiness also block
   global admission until cleanup/review and a fresh startup inventory. Explicit verified destruction is available; ownership
   mismatch requires operator repair/review rather than automatic deletion.

No kernel is deleted, interrupted or force-marked successful to make recovery pass.
GatewayClient gains only explicit `recover_kernels()` and `disconnect()` methods;
existing kernel methods and destructive `close()` retain their contracts. Recovery
uses official REST/WebSocket APIs. Kernel state survives because the original
process survives; there is no notebook storage or checkpoint mechanism. Execution
tracker history and interrupted request/output delivery are not recovered.

Graceful durable shutdown disconnects clients and leaves containers/reservations
owned in SQLite. Explicit `DELETE /api/runtime?notebook_id=...` performs durable
teardown. Local and external compatibility modes keep their existing process/client
cleanup and have no safe process-adoption guarantee. An explicitly configured
external Gateway selects that development compatibility mode, outside durable
containerd ownership and resource admission.

## Diagnostics and operator recovery

`GET /api/runtime/recovery` returns reconciliation completion, global admission
blockers, per-notebook operator states and sanitized durable identities/phases.
`GET /api/runtime/admission` retains Step 2C accounting and includes recovery status.
An unknown workload may have no reliable reservation size: reported arithmetic
capacity is not permission to provision while `new_admissions_blocked` is true.
Verified surviving notebooks may remain usable while unrelated unknown resources
in the dedicated namespace block new provisioning. Unrecoverable notebooks return
503, with their reason available in recovery diagnostics.

No background reconciliation loop or external-service retry system is introduced.
If containerd is unavailable or fencing times out, restore the service and restart
the control plane to retry startup reconciliation. After fixing an inconsistent
allocation, restart to revalidate inventory. Do not delete the journal to clear a
blocker; do not edit rows to free capacity while resources remain. For a verified
allocation in reconciliation, explicit DELETE retries owned cleanup. A mismatched
owner/generation is never repaired by relabeling automatically.

Runtime state under `/run/europa` must survive control-plane restarts. Host/VM reboot,
lost CNI state, restoring an old database backup or manually replacing runtime
metadata can prevent safe adoption; conservative reconciliation is the fallback.
Automatic adoption across those cases is not certified. SQLite needs reliable local
locking/fsync and writable disk. Corrupt/incompatible journals fail closed at startup.
Per-notebook locks, tombstones and failure counters have no new retention/pruning
policy; admission counters remain process-local telemetry, not a durable audit log.

## Validation

Use the existing approved ARM64 VM test environment and cached digest-pinned Gateway
image. The normal real suite never restarts the daemon unless the separate
`EUROPA_TEST_CONTAINERD_RESTART=1` opt-in is present:

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc)' -q \
  --junitxml=test-results/recovery-suite.xml
cd containerd-bridge
go test -p 1 -count=1 -v ./...
```

The additional daemon test runs `sudo -n systemctl restart containerd` in the existing
VM. It changes no daemon configuration and deletes no unrelated workloads. New
crash workloads are bounded to two 384 MiB / 0.25 CPU / 128 PID notebooks, with
0.5 CPU / 768 MiB admission ceilings. All tests run sequentially. Existing local,
Gateway, admission, runc enforcement and mock runsc regression coverage remains.
Real runsc remains deferred due to its independently reproduced upstream bug;
runc continues to share the host kernel and is not equivalent untrusted-code isolation.

Validation on October 24, 2026, in the existing 2 vCPU / 1950 MiB ARM64 VM:

| Run | Result |
|---|---|
| Combined Python suite, including actual daemon restart | **152 passed, 14 deselected**, 202.92 seconds |
| Final focused real recovery + admission/enforcement after snapshot identity guard | **16 passed, 1 deselected**, 108.77 seconds; already-verified daemon restart excluded |
| Final unit/protocol regressions | **98 passed**, on Linux and macOS |
| Native Linux Go tests | **6 passed**: limits, request/network validation, owner/generation checks, session fencing and snapshot identity |

The combined run includes all existing Python/local/runc/admission regressions,
21 new registry/recovery unit cases, seven kernel recovery protocol cases and ten
new real recovery cases. The only 14 combined-run deselections are the deferred
real runsc workloads; mock runsc coverage passes. No final failures or infrastructure
skips remain in the validated selection.

Real SIGKILL tests verify two surviving containers keep their exact runtime IDs,
original kernel IDs and separate kernel variable values; reconstructed reservations
are 0.5 CPU, 805306368 bytes and 256 total PIDs. Excess admission is rejected.
A second process is denied registry ownership while the disposable original is
alive; its lock becomes available after SIGKILL. A request carrying the original
session epoch cannot destroy a recovered container. Tests also kill the control
plane after container creation, after task start, before destruction, and after SDK
destruction but before journal completion. Missing runtime and crashed task cases
verify release only after absence, or held reservations and explicit cleanup.
An intentionally unrecorded test container is neither adopted nor destroyed by
the new controller; its original test owner cleans it afterward.

Graceful production lifespan restart preserves and reuses the original kernel and
its Python value. The separately approved test actually invokes
`sudo -n systemctl restart containerd`; it verifies the surviving task retains the
same PID and generation 1, then executes against the original kernel and its saved
value. The service-restart assertion passes in the combined suite. No containerd
configuration file or runtime binary was installed/replaced.

Initial reattachment testing exposed the existing client-only kernel ownership
cache; verified kernel recovery/disconnect corrected that gap. During fence
validation, a test's simulated external deletion incorrectly omitted its durable
session fence and was rejected. That left one known test allocation and caused
three later nonempty-inventory failures. It was recovered and destroyed through
its exact private journal; the simulation was corrected, and the full run above
passed. These failures were not counted as successful application execution.

Existing real CPU/memory/PID exhaustion tests pass with the durable changes. The
final focused run also independently verifies the 512 MiB built-in profile, profile
selection, invalid requests, metrics, verified limits and real kernel execution.
No workload sleeps or exit-status overrides were introduced to obtain success.

Final audit: no Europa tasks/containers, managed snapshots, runtime state directories,
allocated private IPAM addresses, Gateway/kernel/control-plane or SDK helper
processes. Existing image caches are preserved. The configuration-directory archive
hashes for containerd/system CNI and the private CNI profile hash match the saved
Step 2B baseline; routes and firewall rules match too. `europa0` stays 10.77.0.1/24
and IPv4 forwarding stays 0. Only namespace `europa` is queried for runtime inventory.
No networking rollback or configuration restoration is required.

The VM retained about 1.5 GiB available memory and 5.4 GiB free root disk after
validation. These are final observations, not peak memory measurements. Bounded
resource probes and the existing cached image were sufficient; no parallel container
load, package/runtime upgrade or image rebuild was required.

Ignored host evidence: `test-results/recovery-validation/evidence/`, including
`recovery-validation/final-suite.{log,xml}`, `focused-final.{log,xml}`,
`go-final.log`, `unit-final.log`, `final-audit.json` and
`test-results/recovery-evidence.json`. The latter records sanitized runtime/kernel
identity, reservation and recovery observations; the daemon restart's same-PID and
same-kernel checks are enforced by its successful combined-suite test case.
Earlier failure logs remain as diagnostic evidence. Journals/tokens, machine paths,
raw container specs and temporary test state are excluded from Git.
Guest evidence remains in `~/europa-validation/recovery-validation/`.

No prewarming, eviction, MCP, distributed scheduling, new provider or notebook
persistence is included. This milestone stops at Step 2D.
