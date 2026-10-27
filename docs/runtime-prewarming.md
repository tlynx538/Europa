# Step 2E: clean runtime prewarming

Step 2F adds [durable activity leases and idle cleanup](runtime-lifecycle.md) to assigned
containerd runtimes. Unassigned warm resources retain their separate pool policy.

Implemented on `feature/runtime-prewarming`, based on
`21dd42a5029e369b0210b92b40c8803561da22d0`. The implementation commit is recorded in
Git history and the completion response. The existing containerd+runc provider,
private CNI, resource profiles, admission controller and fenced SQLite ownership
remain the foundation. No service, runtime version, image rebuild, system-network
change or new Python dependency is required.

## Configuration and pool behavior

| Setting | Default | Meaning |
|---|---|---|
| `EUROPA_WARM_TARGET` | `1` | One ready unassigned Gateway; only integer 0 or 1 is supported |
| `EUROPA_WARM_PROFILE` | Existing default resource-profile ID | Complete profile used by the unassigned Gateway |
| `EUROPA_RUNTIME_REGISTRY` | Existing private persistent registry | Same registry holds both assigned and unassigned resources |

The normal built-in profile remains **0.5 CPU, 512 MiB, 128 PIDs**. Actual limits,
host reserves, capacity ceilings and conservative available-memory arithmetic are
unchanged. Measured idle memory is telemetry and never replaces a reservation or
reduces an enforced limit. Local/external development modes do not prewarm. Clean
prewarming is validated only with runc; experimental handlers require target 0.
The handler abstraction and runsc support remain present and deferred. Runc shares
the host kernel; prewarming adds no untrusted-code isolation guarantee.

A single asynchronous worker makes at most one unassigned runtime. Creation first
passes the existing atomic admission controller, then commits the allocation before
any provider side effect. Foreground provisioning takes priority over a background
reservation that is still being evaluated. Already admitted notebooks are never
stopped, resized or recycled to refill the pool. Background creation still consumes
CPU/disk resources; admission quotas are a capacity budget, not a performance SLA.

Creation runs once at startup and is requested after assignment, new provisioning
or destruction. Requests coalesce into one worker. There is no retry spin, periodic
poller, predictive scheduler or provisioning queue. If capacity is insufficient,
the worker records the reason and stops until another triggering event. A pool
slot in uncertain ownership also remains fully accounted for. Two active notebooks
can leave no capacity for a replacement, so the ready target is best effort.

Target 0 disables claims and creation and drains verified unassigned warm resources
at startup before cold admission. Changing the configured pool profile/image makes
old verified unassigned templates ineligible; they are destroyed before replacement.
No assigned runtime is eligible for this cleanup. For a differently configured
notebook, an unavailable/ineligible warm slot falls back to cold provisioning,
which must independently pass admission; an incompatible warm reservation can
therefore consume capacity needed by another profile. No new eviction policy is added.

## Clean means unassigned and never used

A pool runtime has its container and authenticated Kernel Gateway started, **zero
Python kernels**, no notebook binding and no attached user workspace. The provider
still forbids host working-directory mounts and arbitrary environment overrides.
The pinned image and existing OCI restrictions remain unchanged. Readiness requires:

- Durable unassigned ownership and the expected runtime/generation/credential hash.
- Matching provider, pinned image, handler, profile ID and full resource settings.
- Actual host cgroup CPU/memory/PID controls and a responsive authenticated Gateway.
- An empty `/api/kernels` response and an empty `/workspace` tmpfs directory.

Workspace inspection reads at most one directory entry through the verified task's
root; it does not execute Python or read workspace content. The pool never launches
ipykernel as preparation. Gateway credentials remain private to the control plane.
These checks verify an exclusively owned, never-assigned runtime; they are not a
sanitization procedure for used containers. Operator injection of workloads into
unassigned Gateways is outside the supported deployment model. Contaminated or
failed unassigned containers are destroyed after ownership checks, not scrubbed
and returned to service.

## Durable states and atomic assignment

SQLite schema version 2 adds an immutable `allocation_key`, a `kind` (`warm` or
`assigned`), nullable `notebook_id`, and an optional pending `initial_kernel_id`.
Version 1 upgrades inside one immediate transaction. All old rows remain assigned,
with their original notebook label copied to `allocation_key`; they are never
imported into the clean pool. Failed migration rolls back the original table,
index and version. Review/back up a running SQLite database coherently as described
in [runtime recovery](runtime-recovery.md), rather than copying only its main file.
A version-2 database is not a downgrade-compatible version-1 database.

For new warm rows, `allocation_key` is the runtime UUID, `notebook_id` is NULL and
`kind` is warm. The existing `europa.notebook_id` container/snapshot/state label is
now an **immutable allocation label**, equal to this key; for direct cold/legacy
rows it remains their original notebook ID. Notebook assignment is authoritative
in SQLite, separately bound to that exact runtime/owner/generation. No relabel RPC
or credential rotation is required for a claim: the task is unchanged, the
credential was never exposed to a previous notebook, and its fingerprint and
generation are reverified. Labels must be interpreted with the registry, not as
an independent mutable user-authorization mechanism.

```text
warm provisioning -> verified ready (no notebook, no kernel)
ready -> committed claimed + notebook binding -> kernel startup -> committed active
failed/contaminated warm -> committed deleting -> verified destroyed -> release
uncertain claim/cleanup -> reconciliation, full reservation retained
```

The OS namespace/registry locks, WAL/FULL synchronization and helper session fence
from Step 2D remain mandatory. Every bridge operation participates in the same
scope lock/epoch protocol. No resources are inferred absent while old helpers can
still mutate the namespace. No process other than the single owner should issue
unfenced privileged provider operations against this namespace.

Each notebook keeps its existing supervisor lock. A small pool lock serializes
candidate verification and claim. The admission lock covers the synchronous SQLite
claim transaction and in-memory reservation transfer with no intervening await.
SQLite's conditional ready-to-claimed update and unique live-notebook index ensure
exactly one binding. There is no extra capacity grant: the warm reservation moves
from an internal allocation key to the final notebook at its full original size.
The durable commit precedes all notebook/kernel exposure.

Only after that commit does the existing GatewayClient create standard ipykernel.
After readiness succeeds, its ID is persisted and activation commits. The existing
`create_kernel`/REST start contract delivers that initial kernel once, after a new
kernel-info handshake and identity check, rather than creating a second kernel.
Pending initial delivery survives a control-plane restart. Successful delivery or
explicit kernel deletion clears the pending marker transactionally. A dead initial
kernel is never reported as ready. Cold provisioning retains its original behavior.

Startup's compatibility default Gateway is a special Gateway-only request: if it
uses a recovered warm container, no unsolicited Python kernel is launched. The
first explicit kernel start then creates it through the normal client contract.
The default Gateway and every other assigned runtime remain permanently assigned;
even kernel shutdown, an empty workspace or notebook deletion cannot return their
containers to the pool. Destruction removes the container and releases capacity.

## Cancellation and restart recovery

Cancellation before the claim commit leaves the clean slot unassigned. After a
claim, cancelled/failed activation destroys the assigned allocation with shielded,
verified cleanup. It never puts the container back in the clean pool. Failed
cleanup retains the complete reservation, identity and reconciliation state.

Startup restores **all** non-destroyed reservations before inventory or admission.
For unassigned rows it verifies ownership, generation, private credentials, limits,
Gateway readiness, zero kernels and empty workspace again. A sufficiently completed
warm provisioning record can become ready after verification. Incomplete or
contaminated warm resources are destroyed only through verified ownership checks;
uncertain ownership/failed cleanup blocks new admissions and requires review.
Unverified durable ready rows are not counted as available pool slots.

An interrupted `claimed` row remains assigned to its recorded notebook, with its
full reservation held in reconciliation. It is not automatically activated or
reused, even if a kernel was created just before the crash. Explicit verified
teardown is available. A completed active claim recovers using Step 2D's normal
identity/Gateway/idle-kernel checks, including its pending initial kernel delivery.
Warm and assigned generations and credential fingerprints stay associated with
the same task. Graceful shutdown disconnects clients, preserves ready warm/active
runtimes in SQLite, and cancels/settles an unfinished refill safely.

## Diagnostics and operation

`GET /api/runtime/pool` reports the target, profile, verified eligible ready count,
unassigned reservations, worker status, last deferred/failure reason and sanitized
runtime/phase/resource entries. Readiness is the last verified state, not a live
health-poll result; every actual claim rechecks readiness and cleanliness. `/api/runtime/admission` includes this pool status
alongside total reservations and recovery blockers. Ready warm reservations are
excluded from active-notebook count; claimed/starting reservations remain occupied.
`/api/runtime/recovery` distinguishes warm versus assigned records and includes
NULL notebook IDs for unassigned containers. No credential or endpoint is published
by pool metrics. Logical notebook IDs still are not end-user authentication.

To disable and reclaim the pool, configure target 0 and restart the control plane.
To tear down a notebook use the existing explicit DELETE runtime API. Fix ownership
or service availability and restart to retry blocked reconciliation. Do not delete
journal rows or relabel unknown resources to obtain capacity. A corrupt, mismatched
or ambiguously recorded runtime is never automatically attached. Full host/VM-reboot
and lost-runtime-state recovery remain uncertified, as in Step 2D.

## Actual validation and measurements

Tests use the existing ARM64 Lima VM, cached digest-pinned image, containerd+runc
and unchanged private network. New real cases use 0.25 CPU, 402653184 bytes (384 MiB),
128 PIDs, with 0.5 CPU / 768 MiB ceilings so at most two such containers are admitted.
Workloads run sequentially; no image pull/build, package upgrade or infrastructure
installation is needed. Existing CPU/memory/PID exhaustion and SIGKILL regressions
remain part of validation.

Run real tests with the existing approved environment:

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc) and not test_real_cold_warm_benchmark' -q
EUROPA_PREWARM_BENCHMARK_SAMPLES=20 .venv/bin/python -m pytest \
  tests/test_prewarming_integration.py -k benchmark --run-containerd -q
```

The inherited daemon-restart test still requires its separate
`EUROPA_TEST_CONTAINERD_RESTART=1` opt-in. No daemon/system configuration changes
are made by the pool. The benchmark count is bounded to 1–20; its ordinary test
default is 5. Regressions and the longer benchmark are run separately to avoid
repeating the measured workload unnecessarily.

The final execution-verified suite, including the 20-pair benchmark and all 14 new
real lifecycle cases, passed in **452.02 seconds**. Probes require both an `ok`
Jupyter execution reply and expected output. Both paths use the same existing
cached image, resource profile and VM. Each pair runs cold then warm. A warm Gateway
is replenished and measured before the warm request; asynchronous replacement is
suppressed during that paired request comparison. Replenishment and its full memory
reservation are accounted for separately. Results are warm-hit measurements, not
pool-exhaustion latency or concurrent-production-load estimates.

| Measurement | Cold median / empirical p95 | Warm median / empirical p95 |
|---|---|---|
| Container-to-Gateway ready / ready-container durable claim | 3.901 / 4.614 s | 0.029 / 0.045 s |
| Actual kernel startup | 1.683 / 2.274 s | 1.629 / 1.774 s |
| First successful execution after kernel startup | 0.150 / 0.239 s | 0.144 / 0.194 s |
| Runtime request through first successful execution | 5.759 / 6.785 s | 1.854 / 2.064 s |
| Verified container destruction, including client/kernel cleanup | 1.373 / 1.911 s | 1.417 / 1.732 s |

Clean warm Gateway cgroup memory was **58.73 MiB median**, **58.83 MiB empirical p95**
(range approximately 58.65–58.84 MiB). This is `memory.current` immediately after
ready/empty-kernel verification, including charged file cache, not a steady-state
RSS estimate or safe limit recommendation. Its complete 384 MiB reservation remained
held. Replacement creation cost **3.940 s median / 4.482 s empirical p95**.

On this VM and workload, a warm hit reduced measured median request-to-result time
by about **68%**. It moved container/Gateway startup off that request's critical path;
first-execution times were similar. Kernel startup still costs real time.
These are 20 descriptive samples per path and nearest-rank empirical percentiles
(19th sorted value), with fixed paired ordering and shared host caches. They are
not a production p95 guarantee, evidence for predictive scheduling, or proof of
improvement under concurrent replenishment. Pool misses retain cold-start latency
and may be rejected by admission. No output-only or exit-status-128 success criterion
is used: probes assert actual execution results and verified teardown.

Validation on October 24, 2026, on the existing ARM64 VM:

| Run | Actual result |
|---|---|
| Combined regressions, including existing local/runc/limit/SIGKILL/daemon-restart tests | **189 passed, 15 deselected**, 332.53 seconds |
| Final unit/protocol checks, including final ambiguous-state and rejection-accounting cases | **123 passed** on Linux and macOS |
| Final focused clean assignment, HTTP and warm/claim restart checks | **8 passed, 7 deselected**, 78.07 seconds |
| Final new real suite with explicit execution success and 20 paired benchmark samples | **15 passed**, 452.02 seconds |
| Native Linux Go tests | **6 passed** |

The combined run excludes only 14 deferred real-runsc cases and the separately run
long benchmark. Existing runc benchmarking remains included. Mock runsc cases pass.
Two final pure registry/accounting cases added after that combined run pass in the
123-case final unit run. No final failures or infrastructure skips remain in the
validated selections. The earlier standalone lifecycle run also passed all 14 new
real cases in 150.59 seconds; performance numbers above use the final verified run.

New unit coverage includes transactional v1 migration/rollback, exclusive claims,
concurrent duplicate/distinct notebooks, exact template matching, zero target and
startup drain, full reservations/insufficient capacity, failed warm cleanup,
cancellation at replenishment and assignment, recovery of clean and ambiguous
rows, and initial-kernel delivery/readiness. Early unit testing caught missing
retry authorization for a retained failed warm cleanup; the known allocation's
verified retry path was corrected. No real integration failure was treated as
success.

Real probes verify an empty Gateway/workspace before assignment, two independent
notebooks with no shared variable/file state, actual enforced limits, bounded
replenishment, simultaneous claims plus cold fallback, active execution after a
capacity rejection, and destruction of kernel/workspace-contaminated or crashed
warm containers. Cancellation occurs after a real kernel exists; cleanup confirms
all resources gone before release. SIGKILL checkpoints cover ready, created,
started, claiming, claiming-after-kernel and active-before-initial-delivery states.
Clean ready/started containers recover for use; partial creation is destroyed;
interrupted claims retain assigned ownership/reservations in reconciliation;
completed active claims recover the original kernel and its value.

Final independent audit: no Europa tasks/containers, managed snapshots, runtime
state directories, allocated private IPAM addresses, test control-plane/SDK helper
processes, Gateways or kernels. Test disposal asserts no held reservations after
confirmed destruction. Existing image caches and permanent coordination files are
retained. Routes, firewall rules, containerd/system-CNI configuration archive hashes
and the private CNI profile hash match the saved baseline. `europa0` remains
10.77.0.1/24; IPv4 forwarding remains 0. No network rollback is needed.

Final available VM memory was 1514 MiB and free root disk was
5.38 GiB. These are final observations, not peak usage measurements.

Ignored evidence: `test-results/prewarming-validation/evidence/`, including
`prewarming-validation/final-suite.{log,xml}`, `verified-execution-final.{log,xml}`,
`focused-final.{log,xml}`, `unit-final.log`, `go.log`, `final-audit.json`, and
`test-results/prewarming-benchmark.json`, `prewarming-clean-assignment.json`,
`admission-enforcement.json`. The preliminary benchmark is retained for diagnostics;
only the execution-verified final measurements are reported above. Guest evidence
remains under `~/europa-validation/prewarming-validation/`. Raw credentials,
journals, machine-specific files and temporary test state are excluded from Git.

No assigned-runtime recycling, predictive/distributed scheduling, idle eviction,
MCP, notebook persistence, frontend or new sandbox provider is implemented.
This milestone stops at Step 2E.
