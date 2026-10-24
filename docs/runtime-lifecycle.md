# Step 2F: runtime leases and idle cleanup

Implemented on `feature/runtime-lifecycle`, based on
`12e0ec7d02225c6b33dd8f0c82e0409a86fdd6e6`. The implementation commit is in Git
history and the completion response. This extends the existing single-owner SQLite,
containerd+runc, admission, recovery and clean-pool implementation. No dependency,
runtime binary, image, system networking or containerd configuration is added.

## Policy and deployment

The production durable containerd deployment enables activity tracking and one
sequential cleanup worker. Local/external development providers retain their
existing behavior, without automatic cleanup, durable leases or process adoption.
Direct low-level provider/client access is a trusted operator API outside lifecycle
admission; production requests use the supervisor's activity-protected Gateway.

| Setting | Default | Meaning |
|---|---|---|
| `EUROPA_IDLE_CLEANUP` | `1` | `0` disables automatic cleanup, preserving activity accounting |
| `EUROPA_IDLE_TIMEOUT_SECONDS` | `1800` | Thirty minutes after last meaningful notebook operation completes |
| `EUROPA_IDLE_GRACE_SECONDS` | `30` | Additional grace after the first eligible idle observation; also bounded shutdown drain time |
| `EUROPA_IDLE_INTERVAL_SECONDS` | `30` | Interval between sequential sweeps |

Durations must be finite; timeout and interval are at least 0.01 seconds, grace is
nonnegative. The small values in tests are observation policies, not deployment
recommendations. Normal detection includes up to a sweep interval of scheduling
jitter, plus grace, verification and destruction time. Sequential slow cleanup or
host pressure can increase this delay. There is no real-time latency guarantee.

Meaningful operations are kernel creation, execution, restart, termination and
explicit interrupt. Both successful execution and an application error reply count
as completed work. Provisioning initializes activity; first access after upgrading
an old registry conservatively gives existing allocations a full timeout. Health,
status, kernel-info/list polling, HTTP connections and session reconnects do not
refresh activity. A rejected operation does not refresh it either. Operation journal
entries protect work from admission through its actual Gateway completion.

A browser disconnect has no implicit stop or lease-release semantics. Accepted
mutating operations belong to the control plane and are shielded from cancellation
of the HTTP waiter. An execution remains protected until the matching Jupyter
execute reply and idle notification arrive. Existing explicit execution timeouts,
output bounds, kernel interrupts and application error semantics remain applicable.
Disconnected responses are not retained for later delivery; this is not notebook
or execution-output persistence.

## Durable journal and concurrency

Schema version **3** adds three tables within the existing WAL/FULL SQLite database:

| Table | Persisted information |
|---|---|
| `activity` | Runtime identity, last meaningful activity, explicit stop request, first eligible idle candidate |
| `operations` | Random operation identity, runtime identity, generation, operation kind, start time |
| `leases` | SHA-256 token hash, runtime identity, generation, interactive/agent kind, bounded expiry |

Versions 1 and 2 upgrade transactionally, preserving allocations and their immutable
ownership labels. Version 3 requires its activity tables to remain present; missing
ones are corruption, not evidence that no execution exists. Database versions are
not downgrade-compatible. Preserve a coherent database/WAL/SHM backup as described
in [runtime recovery](runtime-recovery.md). Credentials, code and execution output
are not written to these tables. Operation records are deleted on verified normal
completion; unresolved records and allocation tombstones remain for operator review.

Under the existing per-notebook lock, the supervisor verifies the exact active
runtime, notebook binding and generation, commits its operation record, then creates
the owned task before releasing the lock. Cleanup holds that same lock through
eligibility checks and destruction. A newly accepted operation protects the runtime;
if cleanup has already committed deletion, an old Gateway reference cannot admit
work into it. Completion updates activity transactionally. Journal-write failure
before dispatch prevents Gateway side effects; failure after dispatch retains
uncertainty rather than claiming completion.

Automatic retirement requires all of the following:

1. A reconciled, assigned, active allocation with no ownership/recovery blocker.
2. No durable unfinished operation and no unexpired relevant session lease.
3. Timeout and grace elapsed without intervening completed activity.
4. An owned responsive Gateway whose kernel inventory exactly matches its known idle kernels,
   plus the existing live provider readiness check.
5. The existing verified destruction path: commit `deleting`, verify ownership,
   destroy resources, confirm empty task/container/snapshot/network state, commit
   `destroyed`, then release the admission reservation.

Unknown, busy, unready, unowned and reconciliation allocations never become idle
cleanup candidates. A timeout or transport error without a proven terminal result
can leave an unresolved operation even if a kernel termination was attempted. Its
reservation remains held. Diagnostics expose uncertain operation counts; explicit
operator review/destruction is required. Failed idle destruction remains `deleting`
and reserved for explicit retry or startup recovery, rather than a destructive
retry loop.

On startup, all reservations are restored before inventory. A surviving assigned
allocation with any unresolved operation is quarantined, even if a later kernel
poll happens to report idle. Its capacity is retained and new admissions are blocked;
a process restart is not evidence that remote work finished. Complete absence still
permits release, and owned interrupted `deleting` records resume cleanup using
Step 2D's fenced inventory. Other surviving notebooks retain the normal verified
kernel recovery behavior.

Graceful shutdown stops the cleanup worker and waits up to the configured grace
for owned operations. On expiry it detaches local execution tasks/transports while
suppressing the client's usual abort/delete action. Remote kernels and unresolved
journal entries survive; next startup requires reconciliation. It never releases
capacity merely because local tasks were cancelled. The namespace operation fence,
registry/namespace single-writer locks and exclusive control-plane model are unchanged.

## Session leases and explicit stop

`POST /api/runtime/lease?notebook_id=A&kind=interactive&ttl=120` creates a lease for
an existing assigned runtime. `kind=agent` exercises the same future-compatible
ownership contract; no agent service or orchestration is implemented. TTL must be
positive and at most 300 seconds. At most 32 unexpired leases per runtime are allowed.
The response contains `lease_token`, kind and `expires_at`. Keep the token private.

Expiry is the earlier of request time plus TTL and last meaningful activity plus
300 seconds. Creating another session cannot extend that activity boundary. There
is no automatic heartbeat renewal or browser-presence exemption. To release early,
use `DELETE /api/runtime/lease?notebook_id=A` with `X-Europa-Lease: TOKEN`; bearer
material stays out of URLs. Release is idempotent and scoped to the exact runtime
and generation. Releasing/disconnecting a session does not request immediate stop;
normal timeout and grace still apply. Persisted lease expiries survive restart.

`GET /api/runtime/activity` reports policy, live local operation count and sanitized
per-runtime activity, outstanding execution/operation counts, uncertain operation
counts, interactive/agent lease counts, stop request and idle-candidate timestamps.
It exposes no token/hash, code or Gateway endpoint. An outstanding execution count
includes unresolved executions conservatively; it is not proof that their code is
still running. Existing admission, pool and recovery APIs retain their contracts.

`DELETE /api/runtime?notebook_id=A` durably records stop intent. If local work is
active, it returns **409**, leaves that work running and rejects subsequent new
mutations; explicit interrupt remains available. With automatic cleanup enabled,
completion permits verified retirement after grace. Otherwise retry DELETE after
work finishes. With no local active operation, DELETE remains an immediate explicit
owned teardown, including uncertain allocations admitted for operator cleanup.
An explicit operator stop can override session leases. It cannot override an
ownership mismatch or unavailable authoritative ownership inventory.

## Warm pool and limits

Unassigned warm rows are excluded from ordinary notebook activity and idle sweeps.
They remain under the existing target/template/cleanliness policy. Assigned
containers are always destroyed, never scrubbed or returned to the clean pool.
Confirmed destruction requests asynchronous replenishment through normal full
resource admission; active notebooks are neither resized nor stopped to refill it.
A pending stop or uncertain operation never lends capacity to the pool.

Persisted wall-clock timestamps require a reasonably stable host clock. Backward
jumps delay cleanup; forward jumps can advance expiry, although live operations and
busy kernels still protect execution. No distributed clock or lease coordinator is
introduced. Arbitrary background threads/subprocesses after Jupyter reports idle
are not inferred to be active notebook executions; use bounded sessions and an
appropriate idle policy, or disable cleanup for that workflow. No background
execution adoption, host-reboot recovery certification, database pruning, end-user
authentication or durable response delivery is added. This remains one trusted
owner domain on loopback. Runc shares the host kernel; it is not equivalent to
gVisor isolation for untrusted code. Experimental runsc support/evidence remains.

## Validation and measurements

Validation uses the existing ARM64 Lima VM and cached digest-pinned Gateway image.
New real probes use 0.25 CPU, 384 MiB and 128 PIDs per runtime with 0.5 CPU / 768 MiB
admission ceilings. No image rebuild, image pull, runtime upgrade or networking
change is needed. Existing CPU/memory/PID enforcement and SIGKILL tests remain.

Reproduce with the existing approved test environment:

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc) and not test_real_cold_warm_benchmark' -q
.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_lifecycle_integration.py \
  --run-containerd -q
```

The existing containerd-daemon restart test retains its explicit
`EUROPA_TEST_CONTAINERD_RESTART=1` opt-in. Deferred real runsc tests and the separately
validated long prewarming benchmark are excluded from the normal regression run.
Unit tests use injected clocks and asyncio synchronization events; real active-work
probes synchronize on Jupyter `execute_input`, then use explicit interrupt or owned
operator teardown. Small
poll intervals only observe bounded checkpoints and completion, never conceal a
lifecycle race. Real successful execution requires both an `ok` reply and expected
output; the intentional interrupt asserts `KeyboardInterrupt` rather than success.

Validation on October 24, 2026, on the existing 2 vCPU / 1950 MiB ARM64 VM:

| Run | Actual result |
|---|---|
| Broad Python regressions, including local/runc, enforcement, SIGKILL, warm pool and actual daemon restart | **220 passed, 15 deselected**, 471.06 seconds |
| Final focused activity/ownership checks, all prior unit/protocol tests and nine real lifecycle probes | **157 passed**, 100.82 seconds |
| Final host unit/protocol checks | **148 passed**, 2.59 seconds |
| Native Linux Go bridge checks | **6 passed**, including fencing, ownership/generation and private-network validation |

The broad run excludes the 14 deferred real runsc cases and the separately validated
long warm/cold benchmark. Subsequent focused runs verify the final refinements and
additional cases; the final source has 25 new lifecycle unit cases and nine real
probes. There are no final failures or infrastructure skips in these selections.
The daemon-restart regression used its existing explicit opt-in; configuration and
runtime binaries were preserved. Initial unit checks caught a duplicate diagnostics
field; it was corrected before real validation. No failed or output-only workload
was counted as successful execution.

Real tests verify that an accepted execution remains busy after its caller is
cancelled, simulated idle expiry cannot retire it, stop returns 409, and explicit
interrupt completes with `KeyboardInterrupt`. Both SIGKILL and graceful drain leave
the original actual kernel busy; its journal and full reservation remain until
explicit owned cleanup. Crash checkpoints before SDK destruction and after actual
destruction but before journal completion reconcile without leaking capacity.
Session protection/expiry, clean-pool preservation, actual Python execution,
replenishment and verified reservation release pass. Existing enforcement tests
still exercise real CPU, memory and PID limits. Unit races use controlled clocks
and events, including both execution-first and cleanup-first lock orderings.

Three periodic-worker samples used a **0.5 s timeout, 0.1 s grace and 0.05 s interval**
with the same 0.25 CPU / 384 MiB / 128 PID profile. Each executed Python successfully
before idle measurement. These short policies exercise detection, not recommended
development defaults.

| Measurement | Median | Observed range |
|---|---|---|
| First eligible detection after idle deadline | 46.11 ms | 43.56–46.63 ms |
| Observed additional grace | 107.68 ms | 102.76–107.86 ms |
| Destruction through reservation release, after eligibility/readiness checks | 1.305 s | 1.033–1.737 s |
| Admission release after committed, confirmed destruction | 9.62 microseconds | 4.63–21.46 microseconds |
| Observer start through completed retirement | 2.005 s | 1.729–2.405 s |

The release measurement is in-process admission bookkeeping after authoritative
absence and the `destroyed` commit; it excludes the preceding SDK and SQLite work.
The destruction measurement includes client/kernel cleanup and absence confirmation.
Three descriptive samples do not establish p95 or a latency SLA. Real scheduling
jitter can exceed the configured interval.

Observed idle Gateway-plus-kernel cgroup `memory.current` was approximately
**99.1–99.3 MiB**, including charged cache. The full **384 MiB** reservation remained
held until destruction; actual host limit verification passed. This is a snapshot
following successful execution, not steady-state RSS or peak usage. The tests never
substitute measured idle usage for full resource admission.

Final independent audit: no Europa tasks/containers, managed snapshots, runtime
state directories, allocated private IPAM addresses, Gateway/kernel processes,
control-plane test processes or SDK helpers. Successful test disposal confirms zero
held reservations after confirmed destruction. Cached images and permanent
coordination files remain. Configuration archive hashes for containerd and system
CNI, the private CNI profile hash, routes and firewall rules match the saved Step 2B
baseline. `europa0` remains 10.77.0.1/24 and IPv4 forwarding remains **0**. No network
rollback is needed. Final available VM memory was **1479 MiB** and free root disk
**5.37 GiB**; these are final observations, not measured peaks.

Evidence includes `final-suite.{log,xml}`, `ownership-final.{log,xml}`,
`focused-final.{log,xml}`, `go-final.log`, `final-audit.json` and three
`periodic-idle-*.json` samples. `execution-protection.json` records the deliberate
interrupt and verified release. Earlier successful focused logs are retained; the
measurements reported above use the final ownership-guard run.

Ignored evidence is retained under `test-results/lifecycle-validation/evidence/`,
with guest logs under `~/europa-validation/lifecycle-validation/`. Credentials,
journals, raw runtime specs and temporary test data are excluded from Git.
No notebook persistence, MCP, AI orchestration, distributed scheduler, frontend or
new sandbox provider is included. This milestone stops at Step 2F.
