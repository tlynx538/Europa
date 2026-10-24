# Phase 2B Linux ARM64 validation — October 24, 2026

This document preserves the earlier local/network checks and runsc build gate.
The user's updated runtime strategy supersedes that gate for runc: see
[completed Step 2B runc validation](step2b-runc-validation.md) for the actual
ARM64 Gateway build and real-provider results. Runsc remains deferred.

## Outcome

Stage B passed on the existing `europa-test` VM. The subsequently approved private
network is configured and its Europa-to-Gateway connectivity checks passed.
Stage C remains incomplete because runsc loses exit status for short-lived
processes. The native bridge works against the daemon for inventory.
The Gateway image was **not built** and actual ContainerdRuntimeProvider
integration remains **untested**. This is not acceptance of real Step 2B isolation.

The subsequent [exit-status investigation](gvisor-exit-status-investigation.md)
reproduced the problem without Europa and without containerd, localized the loss
to gVisor's separate create/start/wait lifecycle, and compared task events, raw
Wait RPC results, both TTY modes, and intended exit codes 0/7/42. No verified
provider mitigation or fixed runtime version was established; the build gate
remains closed. The approved private network was preserved.

Source: clean `main` at `53564981924df5ad3a74f53b59eff0fde6e001a9` (the
requested commit, although the supplied branch name was `feature/containerd-provider`).
Existing branches/commits and implementation were preserved. No code fixes applied.
The four requested project documents were read before validation.

The sections through “Evidence” below preserve the initial validation record.
The approved-network follow-up at the end records the current configuration,
regression results, gVisor diagnosis, and exact rollback.

## Environment and setup

The existing VM was stopped and was started without recreation or resource changes.

| Component | Observed version/configuration |
|---|---|
| OS / kernel / architecture | Ubuntu 26.04.1 LTS / 7.0.0-34-generic / aarch64 |
| VM | 2 CPUs, 1,950 MiB RAM, no swap, 11 GiB root filesystem |
| System Python | 3.14.4, no pip or application/test dependencies |
| Isolated test Python | CPython 3.13.17, managed guest-user installation |
| uv / Go | 0.13.5 / go1.27.3 linux/arm64; downloaded artifacts SHA256 verified |
| containerd client/server | 2.4.1; overlayfs available |
| runc | 1.5.2, OCI spec 1.3.0, libseccomp 2.6.0 |
| runsc / shim | release-20261019.0 / existing `/usr/bin/containerd-shim-runsc-v1` |
| nerdctl / CNI bridge | 2.4.1 / 1.9.1 |
| BuildKit | Existing service active, buildctl worker advertises linux/arm64 |
| cgroups | v2, systemd; mounted rw with nsdelegate |
| System containerd socket | root:root, mode 0660 |
| IPv4 forwarding | 0 throughout validation |

In this report, `$HOME` means the Linux guest user's home directory.
Work lives in `$HOME/europa-validation` on the guest root disk,
not its memory-backed `/tmp`. A `git archive HEAD` copy supplies `repo/`; its
`.venv` satisfies existing fixture checks without modifying the macOS `.venv`.
All Python dependencies came from the unchanged hash-locked `requirements.lock`;
102 installed packages passed `uv pip check`. The standard python3 kernelspec was
registered into this environment. No system package upgrades or service installs.
Go caches and tools are guest-local; compilation used GOMAXPROCS=2 and `-p 1`.

## Local application and tests

From the guest copy:

```sh
.venv/bin/python -m pytest --run-local-gateway -q -s -rs \
  --junitxml=test-results/linux-local.xml
.venv/bin/python scripts/benchmark_local_runtime.py
```

**46 passed, 28 skipped in 37.46 seconds; no failures/errors.** All skips were
the real container cases requiring `--run-containerd` and approved infrastructure.
This includes existing protocol, mock containerd, local Gateway, provider,
supervisor, FastAPI lifespan, cancellation, timeout, and crash-cleanup coverage.
All 14 Gateway fixtures were reaped, with zero surviving captured children and
zero forced cleanups in normal Gateway fixtures. The deliberate provider crash
test separately exercises forced cleanup.

A separate Uvicorn subprocess used the documented `main:app --app-dir api/src`
startup, explicit local provider, loopback, and a disposable port. Real HTTP
requests verified health; supervisor-managed Gateway; kernel creation; Python
execution; persistent value 42 via structured execute_result; HTML MIME output;
ValueError propagation; KeyboardInterrupt during execution; restart and subsequent
NameError for the old variable; kernel shutdown; and runtime destruction.
Startup to healthy HTTP took 1.618 seconds; the check took 4.717 seconds total.

The ad hoc HTTP harness initially asserted process exit 0 and therefore ended
with an AssertionError **after all HTTP checks passed**. Its observed Uvicorn
return code was -15 following SIGTERM. The log confirms application shutdown
completed and the server finished; captured descendants and a later independent
process scan were empty. This harness assertion is not a failing repository test.

| Single local benchmark measurement | Result |
|---|---:|
| Provision through Gateway readiness | 662.97 ms |
| Subprocess spawn | 1.15 ms |
| Kernel readiness | 256.27 ms |
| First execution | 91.51 ms |
| Warm executions | 90.87 / 99.12 / 90.54 ms |
| Idle Gateway + kernel summed RSS | 159,129,600 bytes, two processes |
| Shutdown and cleanup | 112.55 ms |
| Surviving owned processes | 0 |

These are single samples. They do not establish runtime overhead or distributions.

## Containerd integration preflight

Native `go test -p 1 -v ./...`: **3 passed**, test package time 0.007 seconds.
Native build produced a statically linked ELF ARM aarch64 bridge. Download/build
wall time was not instrumented separately.

The bridge uses the pinned containerd 1.7.28 SDK over gRPC at
`/run/containerd/containerd.sock`, namespace `europa`. A real inventory JSON
request failed as the guest user, then succeeded with `sudo -n`, returning zero
tasks/containers/snapshot/network state. This verifies limited SDK/daemon
interoperability, not create/start compatibility with containerd 2.4.1.

FastAPI chooses providers with `EUROPA_RUNTIME_PROVIDER`; handler selection maps
`runc` to `io.containerd.runc.v2` and `runsc` to `io.containerd.runsc.v1`.
`EUROPA_CONTAINERD_IMAGE` must include an actual sha256 digest. CPU, memory, and
PID limits are explicit required inputs. The bridge sets non-root UID/GID 10001,
read-only root, bounded tmpfs, no capabilities, no-new-privileges, and seccomp.
No isolation restriction was removed or bypassed.

Python executes the bridge without a shell; it cannot currently add `sudo`
arguments. A controlled test can select a temporary bridge wrapper executing
`sudo -n /absolute/path/to/bridge`, with stdin/stdout unchanged, and explicitly
configure the existing CNI binary directory `/usr/local/libexec/cni`. No socket
permission change or root FastAPI process is needed. This arrangement was
identified but not yet used for provisioning. It is a trusted operator test
arrangement, not a production privilege boundary: the current user-owned binary
and general bridge JSON interface must not be delegated to untrusted callers.

The provider attaches CNI to the task network namespace before starting Gateway,
then connects from host-side Europa directly to private container IP port 8888.
The default CNI binary directory `/opt/cni/bin` is absent; existing binaries are
in `/usr/local/libexec/cni`, which the backend constructor can configure.

Existing nerdctl CNI uses masquerading, a default route, and portmap; the bridge's
restricted validator rejects it. No Europa private profile exists. Merely using
the repository example with `isGateway:true` would let the CNI bridge plugin
enable forwarding. Therefore applying that example unchanged is not authorized.

### Native runtime smoke results

Disposable Alpine used the existing image, no CNI or published ports:

| Handler/check | Observed result |
|---|---|
| runc `uname -m` | `aarch64`, exit 0; 0.25 s wall time |
| runsc `uname -m`, no TTY | No output, exit 128; 5.25 s wall time |
| runsc `dmesg`, no TTY | No output, exit 128 |
| runsc `uname -m`, TTY | Printed `aarch64`, but exit 128 |

Daemon logs report: `sandbox no longer running and its exit status is unavailable`
while waiting for runsc, followed by cleanup of the shim. Thus the prior
successful output does not reproduce as a successful lifecycle result in this
session. Exact cause is unresolved; no containerd/runsc configuration was changed.
No kernel OOM entries were found in the checked interval.

## Image and real provider results

The existing pinned Python base manifest was fetched from Docker Registry and
its content SHA256 verified against
`0de818129b26ed8f46fd772f540c80e277b67a28229531a1ba0fdacfaed19bcb`.
It contains Linux ARM64/v8. Pins and locks were unchanged.

Gateway image build: **not attempted**, because Stage C has not passed.
BuildKit is already available; no installation is currently needed.
Actual runc ContainerdRuntimeProvider tests: **not run**.
Actual runsc ContainerdRuntimeProvider tests: **not run**.
All 28 existing real container tests remain skipped, not validated successes.
Multi-container testing, container cgroup enforcement, Gateway isolation, and
provider failure cleanup on real containers remain unverified.

## Resources and cleanup

| Checkpoint | Available RAM | Free root disk |
|---|---:|---:|
| Initial boot/preflight | 1,567 MiB | 7.3 GiB |
| After isolated Python dependencies | 1,490 MiB | 7.0 GiB |
| After local suite and benchmark | 1,537 MiB | 6.5 GiB |
| During later checks/build activity | 1,223 MiB | 6.2 GiB |
| Final | 1,546 MiB | 6.2 GiB |

No critical resource shortage observed. Toolchains, environments, and caches are
retained for continuation; they account for approximately 1.1 GiB disk growth.
Final independent scan found no Kernel Gateway, ipykernel_launcher, or Uvicorn
processes. Final default namespace tasks/containers and Europa namespace
containers/snapshots were empty; no Europa-named mounts were found. No non-test
images/data were deleted. The existing VM remains running for continuation.

## Proposed continuation and approval boundary

First diagnose runsc exit/wait behavior with bounded disposable tests and the
existing shim logs. Configuration, package replacement, and resource changes
would require separate approval if diagnosis demonstrates their necessity.

For provider networking, approve this specific temporary guest change:

1. Create bridge `europa0`, assign `10.77.0.1/24`, and bring it up, after a fresh
   subnet/interface conflict check.
2. Use the prepared `proposed-europa.conflist` with `isGateway:false`, host-local
   `10.77.0.0/24`, explicit gateway `10.77.0.1`, no routes and no masquerading.
   The manually assigned bridge address supplies host-to-Gateway reachability
   while avoiding the plugin's gateway forwarding toggle. Verify this behavior
   on the installed plugin; stop if it changes forwarding unexpectedly.
3. Leave global IPv4 forwarding at 0. Do not publish ports or modify the existing
   nerdctl network, containerd config, or socket permissions. Verify guest routes
   and forwarding before/after. Remove only this test bridge and its test IPAM
   allocations after provider cleanup.

The candidate profile has been written only into validation artifacts, not applied.
The networking approval is required by section 10 of the pasted user request.
After both preflight blockers are resolved, build the existing pinned ARM64 image,
record its actual digest, and run provider tests sequentially runc then runsc.
Select explicit resource limits and check memory before the independent-notebooks
case. Exercise FastAPI with the real provider in addition to the direct-provider
suite. Stop at Step 2B; no Step 2C, warming, allocation policy, or MCP work.

## Evidence

Host artifacts are under `test-results/linux-arm64-validation/` (Git ignored):

- `artifacts/pytest-local.log`, `go-tests.log`, `dependency-install.log`.
- `artifacts/http-local.json`, `uvicorn.log`, `local-benchmark.log`.
- `artifacts/containerd-runsc.log`, `base-manifest.json`.
- `artifacts/proposed-europa.conflist` (not applied).
- `repo/test-results/linux-local.xml`, `gateway-timings.json`,
  `runtime-timings.json`, `step2b-local-benchmark.json`.

The guest source mount is read-only; evidence was copied to the host through the
Lima connection. Existing source and artifacts on the mounted project were not
used for guest environments or caches.

## Approved-network follow-up — October 24, 2026

### Scope and current result

The user explicitly approved the temporary `europa0` bridge and `10.77.0.0/24`
network, with global IPv4 forwarding kept at 0. That work is complete. No further
infrastructure, daemon changes, runtime replacement, or Step 2C work was introduced.
Stage D remains gated by the unresolved runsc lifecycle correctness failure;
therefore the image is still unbuilt and neither handler's real Gateway-container
provider suite has run. Working private networking alone does not validate the
ContainerdRuntimeProvider or gVisor isolation.

### Conflict checks and original-state backups

Before mutation, checks found no `europa0` interface, no overlapping IPv4 routes,
no existing CNI profile using the bridge/network name or subnet, and no matching
host-local IPAM directory. Routes in all tables and every existing CNI JSON
configuration were checked. Container task inventories were empty.

Guest backup directory:
`$HOME/europa-validation/network/before/`.
It contains JSON addresses, links, routes, and policy rules; namespace listing;
IPv4/IPv6 forwarding values; full nft rules; CNI file inventory and hashes; and
tar backups of `/etc/cni/net.d` and `/etc/containerd`. No existing configuration
file needed modification. The backups were saved before creating the bridge.

### Complete network-change record

Persistent commands executed, with UTC timestamps in `network/changes.json`:

```sh
sudo -n ip link add europa0 type bridge
sudo -n ip address add 10.77.0.1/24 dev europa0
sudo -n ip link set europa0 up
```

The kernel consequently added the connected `10.77.0.0/24` route, local
`10.77.0.1` and broadcast `10.77.0.255` routes, plus the bridge's automatic IPv6
link-local address and link-local/multicast routes. All added routes belong only
to `europa0`; no pre-existing route changed. The bridge is administratively UP;
with no veth attached after testing it reports linkdown/NO-CARRIER.

New profile, outside the system CNI directory so other services do not discover it:
`$HOME/europa-validation/network/europa.conflist`, mode 0600. The JSON below uses
`<guest-home>` as a portable placeholder for the absolute guest home used during
validation; CNI does not expand placeholders or `$HOME` inside JSON:

```json
{
  "cniVersion": "1.0.0",
  "name": "europa-validation",
  "plugins": [{
    "type": "bridge",
    "bridge": "europa0",
    "isGateway": false,
    "ipMasq": false,
    "ipam": {
      "type": "host-local",
      "dataDir": "<guest-home>/europa-validation/network/ipam",
      "ranges": [[{"subnet": "10.77.0.0/24", "gateway": "10.77.0.1"}]]
    }
  }]
}
```

Only the existing bridge, host-local, and loopback CNI binaries were used, from
`/usr/local/libexec/cni`. The manually assigned host bridge IP and
`isGateway:false` avoid the bridge plugin's forwarding toggle. There is no route
list, default route, NAT, firewall plugin, portmap, public listener, or port
publication in this setup. No global sysctl was changed.

Transient operations created namespace `europa-validation-probe`, configured its
loopback and one CNI veth on `europa0`, and removed them with CNI DEL followed by
`ip netns delete`. The namespace was created/deleted four times while developing
and running the probe. The first harness attempt omitted its CNI environment and
made no CNI allocation. The second allocated `10.77.0.2` but its Gateway failed
because sudo had supplied HOME=/root. That allocation and namespace were cleaned.
Explicit guest-user HOME/TMPDIR/Jupyter directories corrected the harness.
The two successful probes allocated `10.77.0.3` and `10.77.0.4`, respectively;
both allocations were released. The final IPAM directory contains only `lock`
and `last_reserved_ip.0`, with no address reservation files.

Only the bridge/profile and empty test IPAM bookkeeping remain for continuation.
No namespace, veth, Gateway, kernel, container, task, or Europa snapshot remains.
The bridge is not configured to persist across VM reboot.

### Connectivity, access boundaries, and regressions

A disposable namespace ran an authenticated Kernel Gateway under the guest user,
bound specifically to its private IP, using the unchanged locked Python stack.
Europa's actual FastAPI app and RuntimeSupervisor attached to that endpoint;
ASGI requests exercised health, kernel creation, structured Python execution
(`value=42; print(value)`), and kernel shutdown over the real private HTTP/WebSocket
connection. Authenticated access returned 200 and unauthenticated access 401.
This network probe used external-Gateway attachment, **not** a containerd runtime;
it is not counted as a real-provider integration result.

The final probe completed in 1.359 seconds, including teardown. Its namespace
had only the connected subnet route. Route lookups for `1.1.1.1` and VM uplink
gateway `192.168.5.2` both failed as unreachable. It had no external/default route.
Global forwarding was 0 before, during, and after all probes. Firewall rules
remained byte-for-byte identical to the baseline. Original CNI file hashes and
the containerd configuration tar contents were unchanged. All original routes
were retained; added routes were limited to the bridge described above.

After configuration, existing application tests were rerun sequentially:

```sh
.venv/bin/python -m pytest --run-local-gateway -q -s -rs \
  --junitxml=test-results/linux-after-network.xml
```

**46 passed, 28 skipped in 36.16 seconds.** The skips are still the real-container
cases; there were no repository test failures/errors. Native bridge tests were
rerun with `go test -p 1 -count=1 -v ./...`: **3 passed**, including
`TestPrivateNetworkProfile` (package time 0.005 seconds). These are all existing
network-profile tests in the repository; no unrun external network suite is
claimed. Final scans found no surviving application processes or disposable
tasks/containers in either default or Europa namespaces, no Europa snapshots,
and no Europa test mounts.

Follow-up initial resources: 1,566 MiB available RAM, 6.2 GiB free disk.
After regressions: 1,503 MiB available RAM, 6.2 GiB free disk.
Final: 1,556 MiB available RAM, 6.2 GiB free disk. No resource increase or package
installation was needed.

### Separate gVisor lifecycle diagnosis

Installed Debian package `runsc` is `20261019.0`; runsc reports
`release-20261019.0`. The shim reports `2.1.5+unknown`, built with Go 1.27.2
against containerd/v2 2.1.5. No binary or package was replaced.

Per-test logging used `ctr --runtime-config-path` pointing at a new guest-user
`gvisor-debug/shim.toml`; the system containerd configuration and service were
untouched. It enabled only shim/runsc debug files for disposable tests. Logging
did not resolve the failure.

| Disposable runsc command | Expected exit | Observed exit |
|---|---:|---:|
| `sh -c 'exit 0'`, null I/O | 0 | 128 |
| `sh -c 'exit 7'`, null I/O | 7 | 128 |
| `sh -c 'echo ready; exit 0'`, FIFO I/O | 0 | 128 |
| `sh -c 'sleep 1; echo ready; exit 7'`, FIFO I/O | 7 | 7 |
| `sh -c 'sleep 1; exit 7'`, null I/O | 7 | 7 |
| Immediate `exit 7`, debug logging + null I/O | 7 | 128 |

This reproduces status loss independently of Europa, CNI, Gateway, and TTY output.
Immediate failures took approximately 5.14–5.74 seconds including create/start;
the recorded delayed FIFO case took 6.14 seconds. A delayed successful status is
diagnostic evidence, not a workaround or acceptance criterion.

For the failing debug case, the sandbox boot log records application exit status
7 at 11:21:52.436805 CDT. The separate `runsc wait` command starts connecting at
11:21:52.445911, receives connection refused, cannot recover the dead sandbox's
exit status, and returns 128. The shim then publishes 128 as the container's exit
status. Its OOM counters are all zero. This is a real loss of lifecycle information.

The matching [release shim source](https://github.com/google/gvisor/blob/release-20261019.0/pkg/shim/v1/proc/init.go)
calls `runtime.Start` before launching a goroutine calling `runtime.Wait`.
The source file was saved and SHA256 recorded as
`3dd762eac6815be3873cdb10e3a4590f5f742682a0eda79fedbd1d1cacd95c34`.
The logs and this ordering support a fast-exit start/wait race diagnosis.
A corrective runtime/shim change has **not** been implemented or validated.
No sleeps were added to workloads to hide the failure, and missing statuses
were not treated as successful exits. Review a gVisor lifecycle correction before
changing/replacing privileged runtime components, then rerun both immediate 0/7
and normal/forced lifecycle cases before advancing the build gate.

### Exact rollback

The bridge is currently retained for continuation. All probe workloads are already
stopped, CNI DEL has run, and there are no allocated addresses or attached ports.
The prepared rollback script is syntax-checked but **has not been applied**.
From macOS, execute:

```sh
limactl shell europa-test -- sh -lc \
  'python3 "$HOME/europa-validation/network/rollback.py" --apply'
```

It verifies the profile SHA256, checks for active bridge ports/namespaces or
unknown IPAM reservations, verifies the expected bridge address and forwarding=0,
then performs these scoped removals:

```sh
sudo -n ip link delete europa0 type bridge
sudo -n rm -- "$HOME/europa-validation/network/ipam/europa-validation/lock"
sudo -n rm -- "$HOME/europa-validation/network/ipam/europa-validation/last_reserved_ip.0"
sudo -n rmdir -- "$HOME/europa-validation/network/ipam/europa-validation"
sudo -n rmdir -- "$HOME/europa-validation/network/ipam"
rm -- "$HOME/europa-validation/network/europa.conflist"
```

Deleting the bridge removes its private address and all bridge-owned IPv4/IPv6
routes. No config restoration or forwarding sysctl write is necessary, since
existing files/sysctls were never modified. The script verifies forwarding and
the saved firewall rules after removal and retains backups, logs, probe working
directories, and test environments. If later runtime tests are running, destroy
them through their owning provider before attempting rollback; its guards will
stop rather than delete an active network. Do not blindly restore the tar backups
over a subsequently changed system configuration.

### Follow-up evidence

Host: `test-results/linux-arm64-validation/network-followup/`.

- `network/before/`: original networking/configuration backups and file hashes.
- `network/changes.json`, `europa.conflist`, `final/verification.json`:
  commands, candidate actually used, and verified state delta.
- `network/rollback.py`: guarded exact rollback implementation.
- `artifacts/network-gateway.json`, `network-gateway.log`: final connectivity run.
- `artifacts/pytest-after-network.log`, `go-after-network.log` and
  `repo/test-results/linux-after-network.xml`: regression evidence.
- `artifacts/runsc-exit-matrix.json`, `gvisor-debug/`: separate gVisor diagnostics,
  matching release source, per-test shim options and logs.

The reproducible host probe harness is
`test-results/linux-arm64-validation/network_probe.py`. Its initial harness errors
were corrected without application source changes. No code-fix branch was needed;
that validation run did not change the original branch or commit. The report is
retained with the subsequent Step 2B runc changes.
