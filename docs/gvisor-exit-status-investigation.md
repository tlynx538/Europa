# Step 2B: gVisor exit-status investigation

Strategy update: the user subsequently deferred runsc and authorized independent
[runc validation](step2b-runc-validation.md). The findings and evidence below are
preserved; their build-gate recommendation applies to runsc, not current runc work.

Investigated October 24, 2026, on the existing Lima `europa-test` VM. Read the
Phase 2B validation report and its saved failure evidence before testing.

## Finding

The status is lost in gVisor's **separate create/start/wait lifecycle**, below
containerd and Europa. A short-lived application exits with its correct status,
but its sandbox closes the control socket before the separate `runsc wait`
process can obtain that status. The separate waiter cannot reap the sandbox as
its own child, so its fallback fails. The runsc shim converts that wait failure
to its internal-error status **128**, then reports it as the task's exit status.
Containerd's event, Wait RPC, task status, and clients all propagate that value.

This is supported by the matching release source, timestamped logs, independent
SDK observations, and reproduction **without containerd**. It is not caused by
Europa's SDK bridge. No product-code correction or code-fix branch was necessary.
No supported fixed version or safe provider mitigation was verified. No Gateway
image was built. This work stops at diagnosis.

## Environment and preserved boundaries

| Component | Observed |
|---|---|
| OS / architecture | Ubuntu 26.04.1 LTS / Linux aarch64 |
| Kernel | 7.0.0-34-generic |
| containerd daemon and ctr | 2.4.1 |
| runc | 1.5.2 |
| runsc / installed Debian package | release-20261019.0 / 20261019.0 |
| runsc shim | 2.1.5+unknown, built against containerd/v2 2.1.5 with Go 1.27.2 |
| Alpine | Existing cached `docker.io/library/alpine:3.21`, ARM64 |
| Diagnostic SDK | Existing containerd 1.7.28 / API 1.8.0 module pins |

All tests ran sequentially. Europa and Jupyter were not involved. Containerd
tests used isolated default OCI network namespaces without CNI, host networking,
or port publishing. Direct runsc controls used `--network=none`.

The existing `europa0` bridge, `10.77.0.1/24` address, private CNI profile, routes,
firewall rules, system CNI files, and containerd configuration were preserved.
`net.ipv4.ip_forward` remained **0**. No runtime installation/replacement, daemon
restart, system configuration write, package upgrade, or resource change occurred.
Per-test shim logging options were supplied only through `--runtime-config-path`.

## Minimal reproduction

Run these commands inside the existing VM (`limactl shell europa-test`). They
use the already cached image and require no network configuration:

```bash
sudo ctr -n default run --runtime io.containerd.runsc.v1 --rm \
  docker.io/library/alpine:3.21 europa-exit-minimal-42 sh -c 'exit 42'
observed=$?
printf 'expected=42 observed=%s\n' "$observed"
```

Observed CLI status: **128**. This command was independently verified without any
per-test debug configuration. Do not run it under `set -e` if the following status
print is desired. `--rm` removes the disposable task/container/snapshot.

Compare with runc:

```bash
sudo ctr -n default run --runtime io.containerd.runc.v2 --rm \
  docker.io/library/alpine:3.21 europa-exit-runc-42 sh -c 'exit 42'
observed=$?
printf 'expected=42 observed=%s\n' "$observed"
```

Runc's observed status was **42**. Replace `42` with `0` or `7`, using a distinct
disposable ID. Add `--tty` before the image when running from a real terminal;
the same immediate-exit status loss occurred with a TTY.

For detailed shim evidence, use the existing per-case logging config, for example:

```bash
diag="$HOME/europa-validation/exit-investigation/europa-exit-cli-runsc-1-0-42"
sudo ctr -n default run --runtime io.containerd.runsc.v1 \
  --runtime-config-path "$diag/shim.toml" --rm --tty \
  docker.io/library/alpine:3.21 europa-exit-cli-runsc-1-0-42 sh -c 'exit 42'
```

The harness recorded every actual command in `results.json`. It used util-linux
`script --quiet --return` to supply a real PTY and preserve the child CLI status
for TTY cases. No printed output was used as a success criterion.

## Complete runc/runsc comparison

The CLI matrix tested both handlers, both TTY modes, three exit codes, and two
durations: **24 cases**. Each table entry lists results for expected `0 / 7 / 42`.

| Handler | TTY | Application | Expected | Observed CLI |
|---|---|---|---|---|
| runc | No | Immediate exit | 0 / 7 / 42 | 0 / 7 / 42 |
| runc | Yes | Immediate exit | 0 / 7 / 42 | 0 / 7 / 42 |
| runc | No | Two seconds, then exit | 0 / 7 / 42 | 0 / 7 / 42 |
| runc | Yes | Two seconds, then exit | 0 / 7 / 42 | 0 / 7 / 42 |
| runsc | No | Immediate exit | 0 / 7 / 42 | 128 / 128 / 128 |
| runsc | Yes | Immediate exit | 0 / 7 / 42 | 128 / 128 / 128 |
| runsc | No | Two seconds, then exit | 0 / 7 / 42 | 0 / 7 / 42 |
| runsc | Yes | Two seconds, then exit | 0 / 7 / 42 | 0 / 7 / 42 |

The longer-running diagnostic command was `sh -c 'sleep 2; exit CODE'`.
This deliberately varied lifetime to characterize the race; it is **not** a
recommended mitigation. No workload sleeps were added to Europa or any image.
These single samples establish a duration-dependent failure, not a safe minimum
lifetime or a guarantee for arbitrary long-running workloads.

TTY changes startup timing but does not fix status retention. Immediate runsc
CLI cases took about 5.19–5.53 seconds without TTY and 0.11–0.16 seconds with TTY.
The matching `runsccmd` source has a five-second `exec.Cmd.WaitDelay` to close
pipes inherited by grandchildren. This explains a relevant I/O timing mechanism;
the exact inherited descriptors responsible for this VM's non-TTY delay were
not traced. That delay is separate from the confirmed fast-exit status loss.

## Events, SDK wait, raw RPC, and CLI are distinct observations

A standalone diagnostic Go program uses the existing pinned SDK, creates a
minimal Alpine task with `cio.NullIO`, and calls **Task.Wait before Task.Start**.
It then reads the SDK result, independently queries the daemon's raw task Wait
RPC and Task.Status, and records the task-delete result. It imports no Europa
code. This supplies another **12 cases**, without per-container debug options.

| Handler / lifetime | SDK wait | Raw daemon Wait RPC | Task.Status / Delete |
|---|---|---|---|
| runc / immediate | 0 / 7 / 42 | 0 / 7 / 42 | 0 / 7 / 42 |
| runc / two seconds | 0 / 7 / 42 | 0 / 7 / 42 | 0 / 7 / 42 |
| runsc / immediate | 128 / 128 / 128 | 128 / 128 / 128 | 128 / 128 / 128 |
| runsc / two seconds | 0 / 7 / 42 | 0 / 7 / 42 | 0 / 7 / 42 |

All SDK cases ended in `stopped`, with no transport/cleanup errors. In the failing
cases, the raw daemon Wait RPC itself successfully returns the **wrong status**;
this is not an SDK error being mapped to 128 by Europa. The diagnostic executable
returns 0 when evidence collection completes; its own exit code is explicitly
separate from the application/task status and is not counted as application success.

An independent daemon event subscriber ran throughout the 36-case matrix. All
18 runsc `/tasks/exit` events were captured and agreed with the corresponding
CLI/SDK statuses. All six SDK runc exit events also agreed with their statuses.
Example for the immediate runsc `exit 42` TTY case:

```json
{
  "topic": "/tasks/exit",
  "namespace": "default",
  "timestamp": "2026-10-24T16:35:08.987144717Z",
  "event": {
    "container_id": "europa-exit-cli-runsc-1-0-42",
    "id": "europa-exit-cli-runsc-1-0-42",
    "pid": 12227,
    "exit_status": 128,
    "exited_at": {"seconds": 1792859708, "nanos": 987098301}
  }
}
```

Example independent SDK case, immediate `exit 42`:

```json
{
  "wait_registered_before_start": true,
  "sdk_wait_exit_status": 128,
  "raw_wait_response": {
    "exit_status": 128,
    "exited_at": {"seconds": 1792859737, "nanos": 847959375}
  },
  "task_status_after_wait": {"Status": "stopped", "ExitStatus": 128},
  "delete_exit_status": 128
}
```

Capture limitation: four runc CLI exit events were absent from this subscription
even though their CLI results were correct. They are recorded as **missing**, not
inferred events or runtime failures. There were no subscriber decode errors.
This investigation did not determine whether those missing events reflect
publication or delivery timing. It does not affect the observed incorrect runsc
events, raw Wait responses, or all-case SDK comparison.

## Exact point of failure

For `europa-exit-cli-runsc-1-0-42`, in America/Chicago time on October 24:

| Time (CDT) | Evidence |
|---|---|
| 11:35:08.975460 | Shim's `runsc start` command returned 0 |
| 11:35:08.976417 | Sandbox boot log recorded application exit status **42** |
| 11:35:08.981996 | Separate `runsc wait` attempted the sandbox control connection |
| 11:35:08.982008 | Connection refused; waiter attempted process-status fallback |
| 11:35:08.982014 | Waiter reported that the dead sandbox's exit status was unavailable |
| 11:35:08.982235 | Shim observed `runsc wait` command exit **128** |
| 11:35:08.987079 | Shim logged `Setting status: 128` |
| 11:35:08.987144 | Daemon emitted `/tasks/exit` with **128** |
| 11:35:08.987236 | Shim's task Wait handler returned **128** |

This is an application **42 → unavailable status → synthetic 128** conversion.
The application was not observed exiting 128, and expected output was irrelevant.
The selected shim logs report zero OOM/OOMKill counters. Containerd's service log
also records failed runsc waits and shim cleanup for the failing immediate SDK
cases; the per-container logs supply the more detailed timing above.

The matching `release-20261019.0` code establishes the mechanism:

1. [`proc.Init.start`](https://github.com/google/gvisor/blob/release-20261019.0/pkg/shim/v1/proc/init.go)
   completes `runtime.Start` before launching a goroutine for `runtime.Wait`.
2. [`runsccmd.Runsc.Wait`](https://github.com/google/gvisor/blob/release-20261019.0/pkg/shim/v1/runsccmd/runsc.go)
   executes a separate `runsc wait` command. A nonzero command status becomes an
   error rather than an application exit result.
3. [`Sandbox.Wait`](https://github.com/google/gvisor/blob/release-20261019.0/runsc/sandbox/sandbox.go)
   first calls the sandbox control RPC. If that fails after exit, it attempts
   fallback recovery, but returns an error when this object does not own the
   sandbox as a child. Its `child` field is process-local and not serialized.
4. The shim's wait goroutine handles that error by assigning `InternalErrorCode`
   (128), then sends the resulting exit record to its event/status machinery.

Registering the **containerd client** wait before start does not establish the
separate **runsc-to-sandbox** wait before application exit. The former therefore
cannot repair the latter race.

## Control without containerd

Using the same installed runsc and cached Alpine rootfs, direct controls observed:

| Direct runsc lifecycle | Expected application status | Observed |
|---|---|---|
| Atomic `runsc run` | 0 / 7 / 42 | CLI 0 / 7 / 42 |
| Separate `create`, `start`, then `wait` | 0 / 7 / 42 | create=0, start=0, wait CLI=128 for all three |

Each split waiter printed the unavailable-sandbox-status error. Commands and
debug logs are in `direct-results.json` and `direct-split-results.json`. The
successful atomic `run` retains the creator/parent relationship needed for its
fallback. The split lifecycle reloads state in another process and lacks that
relationship. This also rules out a containerd-2.4-specific transformation as
the origin of this status loss, although it does not certify general version
compatibility.

Direct split tests used real regular-file stdout/stderr and `/dev/null` stdin.
An initial control using Python capture pipes timed out during `create` because
descendants retained the pipe descriptors; its create log recorded exit 0 and
the application had not started. That **harness setup failure** is excluded from
the result table. Changing diagnostic I/O to real files avoids waiting for pipe
EOF; it does not change application timing, and the split status failure remains.
OCI rootfs extraction and temporary image-mount setup attempts are not counted
as workload results. All associated mounts, test snapshot views, and leases were
removed. No direct container remains.

`runsc run` working is diagnostic evidence, **not** a verified replacement for
the existing containerd shim or a permitted bypass of Europa's provider.

## Upstream findings and supported-fix limits

- [gVisor #3276](https://github.com/google/gvisor/issues/3276) describes the same
  class of short-lived-container wait failure and synthetic shim status 128.
  It was closed in August 2022 after related fixes, including
  [c47f8af](https://github.com/google/gvisor/commit/c47f8afe2334c1395a2acd9ebf5191aa4554907c),
  [62b0e84](https://github.com/google/gvisor/commit/62b0e845b7301da7d0c75eb812e9cd75ade05b74),
  and [1e472a8](https://github.com/google/gvisor/commit/1e472a85729b723c0d737d5e1c68c875a158d6a6).
  Those address OOM-adjustment races, stopped-container events, and stopped-state
  handling. The historical closure is not proof of a fix for this installed
  release's split-wait reproduction.
- [gVisor #12198](https://github.com/google/gvisor/issues/12198) remains open and
  reports non-CRI TTY/output problems on containerd 1.7.27 and 2.0.6. A
  [contributor's analysis](https://github.com/google/gvisor/issues/12198#issuecomment-4188833555)
  discusses I/O inheritance and false status 128 for fast exits, and explicitly
  describes its fork workaround as unsuitable for production. Its additional
  deadlock/SIGPIPE claims were not independently established here. They should
  not be treated as the explanation for an `exit 42` program writing no output.
- [Official setup documentation](https://gvisor.dev/docs/user_guide/containerd/quick_start/)
  supports the native runsc shim handler and discusses containerd 2.x
  configuration. It supplies no tested certification of this exact
  containerd 2.4.1 / runsc 20261019.0 / ARM64 / kernel 7.0 combination.
- Targeted upstream issue/commit searches found no verified released correction
  or supported version pin that can be claimed to resolve this reproduction.
  No alternate version was installed or tested. Other reported versions are
  reports, not independently verified combinations. The locally verified
  containerd 2.4.1 + runc 1.5.2 path passed these diagnostic exit-code tests.

The failure mechanism is established for this machine. Remaining uncertainty is
which maintained upstream change/version fixes durable split-lifecycle status
retention, whether it is architecture-sensitive, and whether the separate
non-TTY I/O timing issue needs an additional correction. No AMD64 comparison was
performed. No SDK-bridge patch can reconstruct an application status already
lost by the runtime. Status 128 must continue to be treated as a real failure;
its numeric value alone cannot distinguish a legitimate application 128 from
this synthetic internal failure.

## Recommended next action

Review a report against upstream gVisor #3276/#12198 using this minimal reproducer,
timestamped boot/wait/shim logs, and direct split-lifecycle control. No issue or
message was posted to others. Ask upstream for a maintained correction that
retains exit status after the control socket closes; do not substitute an
unreviewed fork, assume an upgrade fixes it, or force success status.

Before replacing any installed binaries, obtain approval for a specific proposed
runtime/shim version or patch. Independently rerun immediate 0/7/42, both TTY
modes, raw task Wait/events, normal and forced lifecycle cleanup, and broader
runtime compatibility checks. A correction must work without adding workload
delays. Keep the Gateway build gated until that correction or an equally safe
provider mitigation is verified. Do not begin Step 2C.

## Evidence, timings, and cleanup

In this report, `$HOME` means the Linux guest user's home directory.
Host evidence: `test-results/gvisor-exit-investigation/`.
The `exit-investigation/` subdirectory mirrors guest diagnostics from
`$HOME/europa-validation/exit-investigation/`.

- `investigate.py`, `exit_probe.go`: reproducible CLI and independent SDK harnesses.
- `exit-investigation/results.json`: all 36 matrix cases and exact commands.
- `exit-investigation/events.jsonl`: raw decoded daemon event envelopes.
- Per-case `shim.log`, `gvisor.boot.log`, `gvisor.wait.log` and other runtime logs.
- `exit-investigation/containerd-journal.log`: daemon logs for the matrix interval.
- `exit-investigation/direct-results.json`, `direct-split-results.json`:
  direct-runsc controls; final bundle configs and debug logs are retained.
- `exit-investigation/minimal-reproduction.json`: no-debug minimal CLI command.
- `upstream-research.json` and `exit-investigation/upstream/`: issue/comment
  snapshots and exact matching-release source.
- `exit-investigation/before.json`, `after.json`, `final-audit.json`: preserved
  networking/configuration and cleanup evidence.

The 36 matrix case command durations sum to **102.36 seconds**, excluding observer
polling and cleanup overhead. Atomic direct runs took approximately 37–104 ms;
corrected split controls took approximately 64–112 ms and still failed wait with
128. These are diagnostic samples, not performance recommendations.

Initial available RAM was 1,575 MiB; final available RAM was 1,558 MiB. Free root
disk remained approximately 6.2 GiB. Bounded rootfs extraction and existing caches
were sufficient; no parallel container load or broad installation was needed.

Final inventories: no tasks or containers in default/Europa, no test snapshot
views or leases, no direct runsc containers, and no diagnostic namespace/rootfs
mounts. The existing committed Alpine image snapshot was preserved. The approved
private bridge/profile and all system configuration/network checks remain intact.
Only diagnostic files, logs, and extracted disposable rootfs data are retained
in the guest user workspace; no services or workloads remain running for this
investigation.
