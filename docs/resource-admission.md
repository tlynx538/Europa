# Step 2C: single-node resource admission

Implemented on `feature/resource-admission`, starting from Step 2B commit
`681dec0f89991dc7a06da3aa765476c81b3ca691`. The implementation commit is recorded
in Git history and the completion response. This milestone adds admission to the
existing containerd provider; GatewayClient, notebook storage, runtime handlers,
and private network configuration are unchanged. Local remains the development
fallback. Runc shares the host kernel and does not offer gVisor-equivalent isolation.
Experimental runsc and its diagnostic evidence remain preserved and deferred.

## Configuration and API

Selecting `EUROPA_RUNTIME_PROVIDER=containerd` enables admission in the production
FastAPI lifespan. Existing bridge, digest-pinned image, private CNI and explicit
network opt-in settings remain required. Rebuild the Go bridge with this source:
startup now requires its read-only `managed_inventory` operation.

The default `standard` profile is **0.5 CPU, 536870912 bytes (512 MiB), 128 PIDs**.
These are limits, not idle-memory estimates. Defaults do not assume a 2 GB host.
Configure complete profiles with JSON, for example:

```sh
export EUROPA_RESOURCE_PROFILES='{"standard":{"cpu_cores":0.5,"memory_bytes":536870912,"pids_limit":128},"compact":{"cpu_cores":0.25,"memory_bytes":402653184,"pids_limit":96}}'
export EUROPA_RESOURCE_DEFAULT_PROFILE=standard
```

CPU must be finite, between 0.001 and 1024; memory and PIDs must be positive
integers, at most signed 64-bit bytes / signed 32-bit PIDs. Booleans, incomplete
profiles, unknown fields, missing defaults and malformed configuration fail
validation. Profile IDs are nonblank strings of at most 64 characters. Profiles
are immutable for the process lifetime; resizing a live notebook is unsupported.

For compatibility, the complete legacy trio `EUROPA_CONTAINERD_CPU_CORES`,
`EUROPA_CONTAINERD_MEMORY_BYTES`, `EUROPA_CONTAINERD_PIDS_LIMIT` defines `standard`
when JSON profiles are absent. A partial legacy trio fails startup. JSON takes
precedence. Direct Python callers can supply complete `RuntimeSpec.resources`
instead of a profile; they cannot supply both.

| Environment variable | Default / meaning |
|---|---|
| `EUROPA_ADMISSION_MEMORY_RESERVE_BYTES` | Greater of 256 MiB and 10% effective total memory |
| `EUROPA_ADMISSION_CPU_RESERVE_CORES` | 10% effective CPUs |
| `EUROPA_ADMISSION_MEMORY_CAPACITY_BYTES` | Optional ceiling on memory remaining after reserve |
| `EUROPA_ADMISSION_CPU_CAPACITY_CORES` | Optional ceiling on CPUs remaining after reserve |
| `EUROPA_ADMISSION_LOCK` | Node-local lock in the temporary directory, keyed by canonical socket path and `europa` namespace |

Reserves and ceilings must be finite/nonnegative; ceilings can only reduce
detected capacity. A zero ceiling denies all corresponding requests. Use one
controller/worker and the same lock identity for a given socket/namespace. Do not
place the lock on a filesystem without reliable advisory locks or unlink it while
Europa runs. The empty lock file may remain after shutdown; no service is installed.

Existing notebook/kernel API behavior is preserved. Additional operations:

- `POST /api/runtime/provision?notebook_id=A&resource_profile=compact` selects a profile.
  Omitting it selects the default for a new notebook and reuses an existing one.
  A changed explicit profile/request requires destruction before reprovisioning.
- `GET /api/runtime?notebook_id=A` includes the reservation's profile, requested
  resources and phase, separately from observed readiness and enforcement.
- `GET /api/runtime/admission` exposes capacity/accounting metrics without tokens.
- `DELETE /api/runtime?notebook_id=A` also retries unpublished failed-start cleanup.

Insufficient/unknown capacity and invalid profile selection produce an explanatory
503; structurally invalid query parameters produce 422. There is no admission
queue. Local/external runtimes have no enforced admission guarantee; metrics are
disabled for the default local deployment and profile selection is rejected for
local/external runtimes. Externally owned Gateways are outside container accounting.
Europa still requires loopback deployment; this adds no end-user authentication.

## Capacity and admission algorithm

Linux detection reads `/proc/meminfo`, the process CPU affinity, unified cgroup
membership and mountinfo. It walks visible cgroup v2 ancestors, taking the smallest
memory maximum, remaining memory (`memory.max - memory.current`), CPU quota/period
and effective cpuset. Missing/inconsistent required controls, inaccessible files,
legacy cgroups, ambiguous mounts or known hidden ancestors fail closed. No sysctl,
cgroup, network, daemon or system configuration is written by detection.

For effective memory total `M`, observed available `F`, host reserve `H`, and full
notebook memory reservations `R`:

```text
allocatable memory = min(max(0, M - H), optional memory ceiling)
available memory   = max(0, min(allocatable memory - R, F - H - R))
allocatable CPU    = min(max(0, effective CPU - CPU reserve), optional CPU ceiling)
available CPU      = max(0, allocatable CPU - reserved CPU)
```

Reservations are full enforced limits, including PID limits for accounting.
Observed usage never replaces reservations. Subtracting full reservations from
observed free memory deliberately double-counts their resident usage; this can
reject an otherwise feasible request, but cannot admit extra memory based on idle
telemetry. There is no node-wide PID budget: PID limits are enforced per runtime.
CPU capacity means an allocatable quota budget, not predicted idle CPU or a
promise of dedicated cores. Non-Europa workloads can consume resources at any time;
the host reserve is a budget, not a kernel-enforced host partition.

An async controller lock covers resolution, fresh capacity detection, comparison
and insertion of the reservation. Detection runs in a worker thread. Different
notebooks can provision concurrently after their atomic reservations. Per-notebook
supervisor locks deduplicate requests and serialize provision/destruction. No
container side effect occurs before reservation and allocation-identity recording.

## Ownership, cancellation and enforcement

The supervisor assigns a UUID allocation identity before calling the provider.
All container/task/snapshot/network identities derive from it, including failures
before a handle is returned. The admitted limits flow into the existing OCI/cgroup
implementation. Activation requires a ready task, authenticated Gateway health,
and host cgroup controls matching the admitted CPU/memory/PID limits.

Reservations progress from `starting` to `active`, or `cleanup_pending`. Startup
errors, failed enforcement checks, cancellation and health failure trigger owned
container destruction. Cleanup is shielded from repeated caller cancellation;
the notebook lock and reservation remain held until destruction completes.
Destruction failure retains the reservation, identity and retry path. A crash or
unready observation never releases capacity by itself: stopped containers and
snapshots still require explicit destruction. Successful destruction releases
capacity; deletion of a kernel alone does not. Shutdown retries unpublished
allocations and surfaces cleanup errors instead of reporting success.

Accounting is in memory. A nonblocking OS file lock prevents cooperating duplicate
control planes. Startup also rejects nonempty managed container/task/snapshot/state
inventories, rather than assuming old allocations disappeared after a process
crash. It never adopts or deletes unknown allocations. After a control-plane crash,
an operator must inspect and reconcile retained resources before restarting;
there is no automatic crash recovery or persistent reservation journal in Step 2C.
Direct provider calls are a privileged low-level interface, outside supervisor
admission; production API calls go through the controller.

Metrics distinguish `observed`, `host_reserve`, `allocatable`, `reserved`, and
`available_admission`. They include reservation count, last-observed active runtime
count, controller reservation rejections and cleanup failure attempts. Active count
changes on activation, failed-health observation or destruction; no background
monitor has been added. Cleanup counters count attempts, including retries. Profile
conflicts on an already provisioned notebook are supervisor errors and do not
increment the controller's reservation-rejection counter. Unknown capacity reports
null admission capacity while retaining reservation amounts and counters.

## Validation

Tests run sequentially inside the existing ARM64 Lima VM with the already built,
digest-pinned Gateway image and containerd+runc. No image rebuild, new infrastructure,
network change, runtime installation or system configuration change was needed.
The existing 2 vCPU / 1950 MiB VM has no swap. New bounded container tests use
384 MiB profiles with 0.5 CPU/128 PIDs or 0.25 CPU/96 PIDs; ceilings keep concurrent
admission at 0.5 CPU / 768 MiB. Existing Step 2B tests retain their 1 CPU/384 MiB
profile. They run sequentially and do not overlap the resource-exhaustion probes.

Reproduction, after setting the existing approved containerd test environment:

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc)' -q \
  --junitxml=test-results/admission-suite.xml
cd containerd-bridge
go test -p 1 -count=1 -v ./...
```

The selector defers only the 14 real runsc workload cases. Mock runsc regression
coverage remains included. Missing infrastructure is a skip, never successful
validation. New tests cover profile validation, effective Linux ancestor limits,
reserves/ceilings, unreliable capacity, concurrency/deduplication, independent
notebooks, startup/health/enforcement failure, repeated cancellation, live failed
teardown, crash retention, release/retry and single-controller exclusion. Real tests
exercise both supervisor and production FastAPI provisioning/metrics paths.

Final combined validation: **114 passed, 14 deselected in 133.44 seconds**.
The 14 deselections are the deferred real runsc scenarios. This includes all 46
preexisting Python regressions, 14 preexisting real runc cases, 47 new admission
unit cases and seven new real admission cases. Go: **3 passed**. A final API
query-validation refinement was then verified with the strengthened real HTTP
case: **1 passed, 6 deselected in 20.23 seconds**. That case also independently
launched the built-in 0.5 CPU/512 MiB/128 PID profile with legacy settings removed,
verified matching enforced limits, and executed a real kernel. No final failures
or infrastructure skips remain in the validated selection.

| Real bounded probe | Observation |
|---|---|
| CPU control | `cpu.max = 50000 100000`; 11 additional throttled periods |
| Memory control | `memory.max = 402653184`; a child requesting 512 MiB exited -9; `oom_kill` increased by 1 |
| PID control | `pids.max = 128`; 114 small sleep children spawned before EAGAIN (errno 11); PID max event increased |
| Survival | Gateway and kernel executed `print(42)` after all exhaustion probes |
| Failed teardown | Actual task still running; reservation held; second admission rejected; explicit retry cleaned up |
| Startup failure/cancellation | Actual started task destroyed; reservation released; empty managed inventory |
| Concurrency | One of two 0.5 CPU notebooks admitted at a 0.5 CPU ceiling; two 0.25 CPU notebooks admitted independently |

The memory test targets the notebook cgroup, not the VM. The OOM-killed process
was a disposable child; no success is inferred from printed output alone.
Observed effective capacity during enforcement was 2 CPUs / 2,045,476,864 bytes;
available memory was 1,399,820,288 bytes. The configured test reserve was the
built-in 256 MiB and 0.2 CPU. Final available memory was 1,600,393,216 bytes
(about 1526 MiB); root disk free was 5,828,562,944 bytes (about 5.43 GiB). These
are snapshots, not measured peaks or notebook capacity recommendations.

Final audit: no default/Europa tasks or containers, no managed snapshots or
runtime state directories, no allocated IPAM addresses, and no Gateway/ipykernel
workload processes. Existing image caches were retained. Routes, firewall rules,
containerd configuration directory archive hash, system CNI directory archive hash
and private CNI profile hash matched the saved Step 2B baseline. `europa0` remained
10.77.0.1/24 and IPv4 forwarding remained 0. No network rollback is required.

Ignored host evidence is in `test-results/admission-validation/evidence/`:
`admission-validation/final-suite.{log,xml}`, `http-final.{log,xml}`,
`final-audit.json`, and `test-results/admission-enforcement.json`. Corresponding
guest diagnostics remain under `~/europa-validation/admission-validation/`.
Evidence and machine-specific artifacts are intentionally excluded from Git;
committed tests reproduce the checks using operator-supplied configuration.

## Limits and Step 2D recommendations

Supported deployment is a single Linux node with a visible unified cgroup hierarchy,
working controllers, full host capacity visibility and one cooperating Europa
controller. Hidden cgroup-namespace ancestors cannot universally be discovered from
inside a nested container: do not deploy the control plane in an unverified nested
cgroup namespace. This is not a general cross-platform capacity detector. Configured
capacity ceilings do not replace reliable detection or bypass a detection failure.

No durable reconciliation, admission queue, resize, distributed scheduler, idle
eviction, warm pool, notebook persistence or MCP integration is implemented.
In-memory per-notebook locks and destroyed-provider records last for the controller
lifetime; large deployment scale needs a separate retention policy.

For Step 2D, review durable allocation/reconciliation ownership and recovery tests,
then define supported deployment/cgroup boundaries and reserve sizing under measured
host pressure. Keep those proposals separate from this milestone. Evaluate gVisor
only after an independently verified upstream lifecycle correction; retain runc's
shared-kernel isolation limitation in deployment decisions.
