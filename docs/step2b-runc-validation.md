# Step 2B runc validation — October 24, 2026

## Result

**Step 2B's runc validation completed on the existing ARM64 Lima VM.** The pinned
Gateway image built successfully. All 14 real runc provider scenarios passed,
including independent notebooks, kernel lifecycle, failure handling, cgroup
controls, and cleanup. Additional real FastAPI HTTP checks exercised the full
application-to-provider-to-container path and actual bounded resource enforcement.

Containerd now defaults to runc. Local remains the development default/fallback
when `EUROPA_RUNTIME_PROVIDER` is unset or `local`; this is not automatic failover
after a containerd failure. Experimental runsc code and its independent diagnostic
evidence are preserved and deferred. The handler mapping, provider protocol,
supervisor, and Gateway transport architecture remain intact. No Kata adapter,
new sandbox provider, Step 2C, MCP, prewarming, or allocation policy was added.

Runc containers share the host Linux kernel. These functional and resource-control
results do **not** establish equivalence to gVisor for untrusted-code isolation
or production authorization/security readiness.

Source base: `53564981924df5ad3a74f53b59eff0fde6e001a9`.
Changes were prepared on separate branch `validation/step2b-runc-arm64`;
existing history and earlier diagnostic files are preserved.

## Environment and explicit limits

| Component | Version/configuration |
|---|---|
| VM | Existing `europa-test`, 2 vCPUs, 1,950 MiB RAM, no swap |
| OS / architecture | Ubuntu 26.04.1 LTS, kernel 7.0.0-34-generic, aarch64 |
| containerd / nerdctl | 2.4.1 / 2.4.1 |
| runc | 1.5.2, native `io.containerd.runc.v2` handler |
| Builder | Existing active BuildKit ARM64 worker |
| API/test Python | Guest-local CPython 3.13.17 environment with unchanged application lock |
| Gateway image Python | Pinned 3.13.0 slim-bookworm base |
| Gateway / kernel | Jupyter Kernel Gateway 3.0.1 / ipykernel 6.30.1 |
| Go | Existing guest-user Go 1.27.3; pinned containerd SDK 1.7.28 |
| cgroups | Unified v2 |
| CPU / memory / PIDs per test container | 1 core / 402,653,184 bytes (384 MiB) / 128 |
| Container identity | UID/GID 10001 |

These limits were explicit test inputs appropriate to this VM, not automatic
resource presets. Tests were sequential apart from the intentional independent
notebooks case, which used at most two bounded containers after checking memory.
No heavy ML dependencies, privileged service installation, or VM resizing occurred.

## Image build

Executed from the guest-local repository copy:

```sh
sudo nerdctl --namespace europa build --platform linux/arm64 \
  -t europa-gateway:arm64 -f runtime-image/Dockerfile .
```

The Dockerfile, base digest, and `runtime-image/requirements.lock` were unchanged.
The previously verified pinned base manifest supports ARM64/v8. Hash-locked
dependency installation succeeded. The image runs Gateway, not Europa FastAPI.

| Build measurement | Observed |
|---|---|
| Wall time | 39.46 seconds |
| Compressed image size | 73.6 MiB |
| Platform | linux/arm64 |
| Resulting manifest digest | `sha256:d65c760f20eb90482c4cb12cea211e4fdca5ee32bd4968433a1ca1a36b623944` |

The built tag was registered in namespace `europa` under its actual digest and
the provider used this exact reference:

```text
docker.io/library/europa-gateway@sha256:d65c760f20eb90482c4cb12cea211e4fdca5ee32bd4968433a1ca1a36b623944
```

The image and ordinary BuildKit cache are retained. BuildKit's existing service
owns cache snapshots/views/leases in the default namespace; those are distinct
from per-runtime provider allocations and were not purged.

## Operator setup and code changes

The control plane ran unprivileged. Its configured executable was a temporary
guest-user wrapper invoking the native bridge through `sudo -n`, passing the
original JSON stdin/stdout. No socket permission change or root FastAPI process
was required. This is a controlled operator test arrangement; a user-owned bridge
with broad operator sudo access is not a production privilege boundary.

The existing private CNI profile was reused unchanged. In the commands below,
`$HOME` refers to the Linux guest user's home directory, not the macOS host home:

```sh
export EUROPA_RUNTIME_PROVIDER=containerd
export EUROPA_CONTAINERD_HANDLER=runc
export EUROPA_CONTAINERD_BRIDGE="$HOME/europa-validation/runc-validation/bridge-wrapper"
export EUROPA_CONTAINERD_IMAGE=docker.io/library/europa-gateway@sha256:d65c760f20eb90482c4cb12cea211e4fdca5ee32bd4968433a1ca1a36b623944
export EUROPA_CONTAINERD_CNI_CONFIG="$HOME/europa-validation/network/europa.conflist"
export EUROPA_CONTAINERD_CNI_BIN=/usr/local/libexec/cni
export EUROPA_CONTAINERD_CPU_CORES=1
export EUROPA_CONTAINERD_MEMORY_BYTES=402653184
export EUROPA_CONTAINERD_PIDS_LIMIT=128
export EUROPA_CONTAINERD_ALLOW_NETWORK=1
```

Necessary source changes:

- `ContainerdConfig.handler` and the FastAPI environment fallback now select runc.
- FastAPI and the real test fixture accept `EUROPA_CONTAINERD_CNI_BIN`, preserving
  `/opt/cni/bin` as the existing default. This VM uses `/usr/local/libexec/cni`;
  no system symlink or CNI installation was needed.
- Added `scripts/benchmark_containerd_runtime.py`, a sequential three-sample
  benchmark using the actual provider and explicit operator configuration.
- README and provider documentation now distinguish current verified runc results
  from historical mock-only results and deferred runsc work.

No Go bridge, runtime lifecycle, sandbox restrictions, image pins, or Gateway
transport fixes were needed. The existing abstraction remains available for
future reviewed handler integration; no new handler was implemented.

## Actual test counts

| Validation | Result |
|---|---|
| Initial single creation preflight | 1 passed, 27 deselected; 2.02 s |
| Real runc cases except independent notebooks | 13 passed, 15 deselected; 31.26 s |
| Real runc independent notebooks case | 1 passed, 27 deselected; 14.36 s |
| Local/protocol/mock regression suite | 46 passed, 28 skipped; 34.44 s |
| Native bridge regression tests | 3 passed; package time 0.005 s |
| Additional real HTTP / enforcement harness | Passed after correcting a harness quoting error |
| Sequential benchmark | Three fresh runtimes, all cleaned |

The initial creation case was repeated in the 13-case run and is not an extra
unique test. Across separate invocations, **60 unique Python test cases passed**:
46 regression cases plus 14 real runc cases. The 28 skips in the regression-only
invocation are the container module when `--run-containerd` is absent; its 14
runc cases were separately executed above. All 14 real runsc cases remain
deferred/deselected. Their mock-backed tests still run in the regression suite.

Commands for the real cases, from the guest repository with the environment above:

```sh
.venv/bin/python -m pytest tests/test_containerd_integration.py --run-containerd \
  -k 'runc and not independent' -q -s -rs
.venv/bin/python -m pytest tests/test_containerd_integration.py --run-containerd \
  -k 'runc and independent' -q -s -rs
```

Existing coverage passed for provisioning, authenticated Gateway readiness, kernel
creation, execution and state persistence, structured MIME/results and Python
errors, restart/state reset, correlated interruption, shutdown, forced teardown,
crash detection, notebook separation, cgroup-control inspection, and inventory
cleanup. No application/container failures occurred in these repository tests.

## Real FastAPI and resource-enforcement checks

The additional harness ran the production `create_app()` through an actual
loopback Uvicorn HTTP listener inside Linux. `EUROPA_GATEWAY_URL` was absent.
It deliberately removed `EUROPA_CONTAINERD_HANDLER` to verify that runc is now the
default. The path was FastAPI → supervisor → ContainerdRuntimeProvider → SDK
bridge → containerd → runc → Gateway → ipykernel, without an external attachment.

Startup to healthy HTTP took **1.738 seconds**. Runtime status identified the
containerd provider and reported verified limits. The actual OCI spec had
`io.containerd.runc.v2`, UID/GID 10001, read-only root, no new privileges, empty
capabilities, and seccomp. The kernel also observed UID/GID 10001 and did not
inherit `KG_AUTH_TOKEN`. The OCI environment was inspected only in memory;
credentials were not written into reports.

HTTP checks passed health, creation, execution, persistent value 42, structured
`execute_result`, HTML MIME output, ValueError propagation, interrupt after a
correlated execute-input barrier, restart/state reset, and shutdown. Deliberate
backend task loss caused health to return **503**; runtime deletion and Uvicorn
lifespan cleanup succeeded, leaving an empty inventory. Uvicorn shutdown took
123 ms in that sample.

Host cgroup files were independently read, not inferred solely from OCI requests:

```text
cpu.max       = 100000 100000
memory.max    = 402653184
pids.max      = 128
memory.oom.group = 0
```

Bounded enforcement probes through Europa's execution API observed:

| Probe | Actual result |
|---|---|
| Two short CPU-bound Python children under the one-core quota | Both completed; `cpu.stat nr_throttled` increased by **15** |
| Up to 160 small native sleep children, all terminated/reaped in finally | Creation denied after **114** children with **EAGAIN (11)**; `pids.events max` increased by **1** |
| Disposable child requesting a 512 MiB bytearray in the 384 MiB cgroup | Child terminated by **SIGKILL (-9)**; cgroup `oom_kill` increased by **1** |
| Recovery after these probes | Gateway and existing kernel stayed usable; value 42 persisted |

The memory event was an intentional container-cgroup OOM, not a host memory
shortage. The largest disposable child was killed; no VM allocation was changed.
These probes verify basic CPU throttling, PID denial, and memory OOM behavior,
not every possible resource-exhaustion or hostile-workload scenario.

One initial HTTP diagnostic attempt failed because a nested CPU-program string
was incorrectly escaped in the harness. The application correctly returned a
Python syntax error; the harness then tried to parse nonexistent stdout JSON.
Its finally cleanup produced an empty inventory. That attempt is preserved as
`http-and-limits-first.*`; the corrected full harness passed. No product fix or
weakened assertion was used to hide it.

## Private network preservation and cleanup

The real provider assigned private Gateway addresses on the existing
`10.77.0.0/24` bridge (the final HTTP sample used `10.77.0.25:8888`). Kernel route
inspection showed only the connected subnet and no default route. A socket
connection toward `1.1.1.1:80` failed with **ENETUNREACH (101)**. No NAT, forwarding,
external route, public port, additional plugin, or network was introduced.

Final comparisons against the saved pre-test baseline verified:

- Global IPv4 forwarding remained **0**.
- Routing tables and nft rules were unchanged.
- Original system CNI files, containerd configuration, and the Europa private
  profile had identical hashes/content.
- `europa0` retained `10.77.0.1/24`, with no attached test veth interfaces.
- No application processes, tasks, containers, per-runtime rootfs snapshots,
  namespace mounts, or `/run/europa` runtime directories remained.
- IPAM retained only its lock/last-reserved bookkeeping, with zero live IP
  allocations. Namespace listing was empty.

The Gateway image, ordinary image layers/build caches, test environments, logs,
and the approved private network are retained for continuation. No non-test
images/data were deleted. Existing network rollback instructions remain in
`docs/phase2b-linux-arm64-validation.md` and its guarded guest rollback script.

## Benchmarks

Ran `scripts/benchmark_containerd_runtime.py` with three sequential fresh runtime
and kernel allocations. All three image observations reported **cached=true**.
Here, cold startup means a **new container/Gateway/kernel from the cached image**;
an uncached registry pull was not measured. No image cache was deleted to force
one. Build time above is a separate measurement.

| Phase | Samples | Median |
|---|---|---:|
| Fresh container through Gateway readiness | 1106 / 827 / 834 ms | **834 ms** |
| Kernel readiness | 378 / 201 / 199 ms | **201 ms** |
| First execution | 51.5 / 48.3 / 48.1 ms | **48.3 ms** |
| Warm execution | Nine samples, 47.9–56.1 ms | **50.9 ms** |
| Idle Gateway + kernel cgroup memory | 104,431,616 / 104,230,912 / 104,411,136 bytes | **99.6 MiB** |
| Kernel REST cleanup | 115 / 118 / 117 ms | **117 ms** |
| Runtime destruction plus inventory audit | 215 / 161 / 201 ms | **201 ms** |

Combined fresh container + kernel readiness was approximately **1.034 seconds
median**. Combined kernel cleanup + runtime cleanup/audit was **318 ms median**.
Container cgroup memory and prior local summed process RSS use different
accounting, so no local/runc/gVisor overhead ratio is claimed. These small samples
are measurements, not performance guarantees or a resource allocation policy.

## Host resources

| Checkpoint | Available RAM | Free root disk |
|---|---:|---:|
| Before image build | 1,572 MiB | 6.2 GiB |
| During dependency installation/build | 1,431 MiB | 5.8 GiB |
| After build | 1,546 MiB | 5.5 GiB |
| After single-container cases | 1,510 MiB | 5.5 GiB |
| After independent case and benchmarks | 1,450 MiB | 5.5 GiB |
| Final post-cleanup sample | 1,528 MiB | 5.5 GiB |

Measurements are checkpoint samples, not continuous peak tracking. Resource
pressure was contained by the explicit limits; there was no critical host resource
shortage. Approximately 0.7 GiB root-disk growth includes image layers and builder
cache, not surviving notebook containers.

## Artifacts and next boundary

Guest evidence: `$HOME/europa-validation/runc-validation/`.
Host mirror: `test-results/runc-validation/evidence/runc-validation/` (Git ignored).

- `image-build.log`: actual pinned ARM64 build and timing.
- `creation.*`, `runc-single.*`, `runc-independent.*`: JUnit/log evidence.
- `python-regression.*`, `go-regression.log`: existing regression results.
- `benchmark.json`, `benchmark.log`, provider timing JSON: measured samples.
- `http-and-limits.json`, `http-and-limits.log`, `validate_http_and_limits.py`:
  actual HTTP, network, OCI and exhaustion checks; first-attempt artifacts retained.
- `before.json`, `final-audit.json`: network/configuration and independent cleanup
  checks. No Gateway credentials are included.
- `test-env.sh`, `bridge-wrapper`: controlled reproducible guest test setup.

The original application branch and earlier diagnostic evidence remain available.
Step 2B's runc validation is complete. Defer runsc until its lifecycle problem is
corrected and independently retested. Any Step 2C, alternative handler/provider,
production privilege design, authorization, or stronger untrusted-code isolation
requires separate scope/review.
