# Europa: FastAPI + Jupyter Kernel Gateway

Europa exposes an application API backed by **Jupyter Kernel Gateway 3.0.1**, using
the documented `kernel_gateway.jupyter_websocket` personality and standard
`ipykernel`. Gateway owns kernel processes and the Jupyter-to-WebSocket bridge.
The Gateway client uses only HTTP and WebSocket APIs; it has no Jupyter process
manager, ZeroMQ reader, or custom kernel wrapper. Runtime providers own either
development subprocesses or containerd tasks through a separate provisioning layer.

Validation covers **trusted local development**, mocked containerd behavior, and
real containerd+runc execution on an ARM64 Lima VM. See the
[runc validation report](docs/step2b-runc-validation.md) for measured limits,
cleanup, and benchmarks. Runc containers share the host Linux kernel; they are
not equivalent to gVisor for untrusted-code isolation. A virtual environment and
temporary working directory do not isolate hostile Python from the host.
Europa and local Gateways bind to loopback; container Gateways are reachable only
through the approved private CNI network. Gateway authentication is mandatory.
Europa has no end-user authentication and must remain on loopback. The containerd
adapter is opt-in and requires approved Linux infrastructure. Runc is its default
OCI handler; experimental runsc is deferred due to its independently reproduced
[upstream exit-status problem](docs/gvisor-exit-status-investigation.md).
Clean containerd+runc prewarming and durable idle cleanup are configurable.
Standard notebook persistence and cell execution are available.
Projects, conversations, versioned roadmaps, cell comments and activity histories
are persisted through the [Phase 3B application APIs](docs/conversational-notebooks.md).
MCP is not implemented. An incomplete notebook UI preview is available in `web/`;
it does not yet implement the full project, roadmap, or discussion workflow.

### Notebook UI preview

Run the loopback API as described below, then in another terminal run:

```sh
cd web
npm ci
npm run dev
```

Open the loopback URL printed by Vite. The development server proxies `/api` to
`127.0.0.1:8000`; do not expose either server to a network. This preview supports
notebook creation, cell editing with explicit saves, ordering and execution. Reload
the saved notebook to see applied results. It deliberately renders only escaped text
and JSON; other MIME types remain saved but are not displayed. It is not the completed
Phase 3C interface: project creation, roadmaps, comments, discussions, autosave,
interrupts, rich output, responsive drawers, full reconnect/conflict recovery and
browser/integration test coverage are still outstanding. Python variables are live
kernel state and are not part of the saved document.

## Reproducible environment

Use CPython 3.13 (validated with 3.13.0). `requirements.in` lists direct pins and
`requirements.lock` locks transitives and artifact hashes with platform markers.
It includes development/test dependencies. Installation does not launch services.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m ipykernel install --prefix .venv --name python3 --display-name 'Python 3 (Europa)'
```

With uv: `uv venv --python 3.13 .venv`, then
`uv pip sync --python .venv/bin/python --require-hashes requirements.lock`.
Refresh the lock deliberately with
`uv pip compile requirements.in --python-version 3.13 --universal --generate-hashes -o requirements.lock`.

## Launch provider-managed local development

With `EUROPA_GATEWAY_URL` unset, one command starts FastAPI and provisions its
default authenticated local Gateway runtime through `LocalRuntimeProvider`:

```sh
.venv/bin/python -m uvicorn main:app --app-dir api/src --host 127.0.0.1 --port 8000
```

The provider allocates a loopback port, generates a private token, and uses a
temporary home/work/config directory. FastAPI lifespan deletes kernels and
destroys owned runtimes on shutdown. No manual Gateway launch is needed in this
mode. Local CPU/memory requests are metadata only; no resource limits are enforced.

All existing kernel routes accept an optional `notebook_id` query parameter,
defaulting to `default`. A different identifier provisions a separate Gateway and
client. This is a logical ownership association, not notebook document storage or
user authorization. The existing kernel IDs, responses, MIME data, interruption,
restart, and shutdown contracts remain unchanged. HTTP outputs remain buffered
JSON, as in Step 1B; no new HTTP streaming API was added.

Additional runtime operations:

- `POST /api/runtime/provision?notebook_id=A`: ensure one ready runtime for A.
- `GET /api/runtime?notebook_id=A`: observed status without authentication material.
- `DELETE /api/runtime?notebook_id=A`: close its client and destroy the owned runtime.

Concurrent provisioning for the same notebook is serialized; different notebooks
can provision independently. A dead/unready runtime returns 503 instead of a stale
healthy handle. Destroy it before explicitly provisioning a replacement. There is
no automatic restart, warming, eviction, scheduler, or distributed coordination.

See [RuntimeProvider contracts and Step 2A results](docs/runtime-provider.md).

## Connect an externally launched Gateway (compatibility mode)

Configure a token in the environment, not command-line arguments. These commands
generate a new development token; keep the same environment in the Europa terminal.
Do not commit tokens or print them to logs.

```sh
export KG_AUTH_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export KG_PORT=8888
export EUROPA_GATEWAY_URL="http://127.0.0.1:${KG_PORT}"
export EUROPA_GATEWAY_TOKEN="$KG_AUTH_TOKEN"
export TRAITLETS_APPLICATION_RAISE_CONFIG_FILE_ERROR=1
.venv/bin/python -m kernel_gateway --config=gateway/jupyter_kernel_gateway_config.py
```

In a second terminal with the configured Europa environment:

```sh
.venv/bin/python -m uvicorn main:app --app-dir api/src --host 127.0.0.1 --port 8000
```

Gateway config fixes loopback binding, disables port fallback and pre-spawning,
allows only the venv's `python3` kernelspec, and limits kernels to four. Creation
sends `env={}` to activate Gateway environment filtering; only PATH and configured
HOME/TMPDIR/locale variables reach kernels. This does not restrict filesystem or
network access. Stop Europa gracefully before Gateway so its lifespan can delete
owned kernels. Gateway shutdown also owns kernel cleanup.

The optional Dockerfile is a development build definition, not an orchestrator or
an isolation guarantee. It has not been built in this milestone.

## Application API

| Method | Europa path | Behavior |
|---|---|---|
| POST | `/api/kernel/start?kernel_name=python3` | Create a kernel; return Gateway model after readiness |
| GET | `/api/kernel/info?kernel_id=ID` | Inspect Gateway model |
| POST | `/api/kernel/execute?kernel_id=ID&timeout=30` | JSON body `{"code":"print(42)"}`; structured result |
| POST | `/api/kernel/interrupt?kernel_id=ID` | Official interrupt; available during execution; 204 |
| POST | `/api/kernel/restart?kernel_id=ID` | Official restart; await readiness; return model |
| POST | `/api/kernel/shutdown?kernel_id=ID` | Official termination; 204 |
| GET | `/api/health` | Authenticated Gateway availability and execution metadata |

Gateway requests use `Authorization: token ...`. REST calls are
`POST /api/kernels`, `GET /api/kernels/{id}`, `POST /api/kernels/{id}/interrupt`,
`POST /api/kernels/{id}/restart`, and `DELETE /api/kernels/{id}`.
Channels use `/api/kernels/{id}/channels`.

Each execution opens a standard JSON WebSocket connection, verifies readiness with
a correlated `kernel_info_reply`, sends `execute_request` with `allow_stdin=false`,
and preserves matching Jupyter messages. Completion requires **both** matching
shell `execute_reply` and IOPub `status: idle`, in either order. MIME bundles,
stdout/stderr, errors, and display events remain in `messages`. Results contain
`execution_id`, `kernel_id`, `status`, `reply`, and `messages`. Python errors return
`status="error"`; transport failures use HTTP errors. Optional binary WebSocket
subprotocols are not requested; unexpected binary frames fail explicitly.

There is no scheduler or queue. A second execution, restart, or shutdown during
an active operation returns 409; interrupt remains available. Timeout, cancellation,
channel failure, and output-budget exhaustion request Gateway DELETE; state is
lost. Failed deletion retains ownership for teardown retry. If Gateway is
unavailable, Europa cannot prove remote termination. Limits: 1,000,000 code
characters, 0 < timeout <= 300 seconds, 4 MiB matching message data per execution,
and 50 completed metadata records.

### Breaking changes

- Kernel IDs are explicit; start creates a new kernel instead of returning a singleton.
- Execution returns structured JSON, replacing plain-text HTTP streaming.
- Restart returns after readiness, replacing an unobservable background task.
- Shutdown returns 204; host PIDs/custom wrapper statistics are no longer API contracts.
- `/api/kernel/install-packages` returns 501; installation is outside this milestone.
- Local/external ownership is per application instance. Durable containerd ownership
  uses a private single-writer registry; multiple workers are unsupported.

## Tests

Protocol regression tests launch no processes or kernels:

```sh
.venv/bin/python -m pytest tests/test_gateway_protocol.py -q
```

Real local integration tests require explicit opt-in and the repository `.venv`:

```sh
.venv/bin/python -m pytest --run-local-gateway -q -s --junitxml=test-results/integration.xml
```

Each integration test uses the production LocalRuntimeProvider to launch an
authenticated loopback Gateway on an available port in a disposable environment.
Tests use only trusted,
bounded repository source, never external workloads or package-installation tests.
Fixtures delete kernels through REST, verify an empty kernel list, stop/reap
Gateway, check captured descendant process identities, and fail if forced cleanup
is required in normal shutdown. Runtime-provider crash cleanup can force-stop
captured descendants by process identity; Jupyter starts kernels in separate
process groups. This is generic runtime teardown, not custom kernel control.

`test-results/` contains actual JUnit results and startup/cleanup timing records,
including `runtime-timings.json` for the Step 2A provider tests.
Docker is not required under the approved local test mode. Without
`--run-local-gateway`, integration cases are explicitly skipped; those skips do
not count as validation. The local process-group harness requires POSIX.

## Jupyter Server compatibility

Kernel Gateway is the **only launched kernel backend**. Jupyter Server is a library
dependency for kernel handlers; no second Jupyter Server service is launched.
Kernel REST and channels overlap, but this client is validated against the locked
Gateway stack, not every Jupyter Server feature.

Gateway does not supply notebook document editing/storage. Contents API,
checkpoints, file-save hooks, and Server extension lifecycle require a separately
evaluated Jupyter Server backend or document service. Do not launch both merely
to duplicate kernel operations. A backend change requires tests for authentication,
environment creation, WebSocket protocols, and ownership semantics.

References:

- [Gateway personality](https://jupyter-kernel-gateway.readthedocs.io/en/latest/websocket-mode.html)
- [Gateway 3.0.1 kernel handlers](https://github.com/jupyter-server/kernel_gateway/blob/v3.0.1/kernel_gateway/services/kernels/handlers.py)
- [Jupyter messaging](https://jupyter-client.readthedocs.io/en/stable/messaging.html)
- [Jupyter Server REST and Contents APIs](https://jupyter-server.readthedocs.io/en/stable/developers/rest-api.html)
- [WebSocket protocols](https://jupyter-server.readthedocs.io/en/stable/developers/websocket-protocols.html)

Step 2B adds an opt-in containerd provider using the supported Go SDK and native
`runc`/`runsc` handlers. Local development remains the default. See
[containerd provider review](docs/containerd-provider.md) for configuration, the
digest-pinned Gateway image, actual tests and benchmarks, and Linux prerequisites.
Real runc tests run inside Linux using `--run-containerd` and explicit operator
configuration; `-k runc` selects the 14 runc scenarios while runsc is deferred.
Local development remains the fallback when `EUROPA_RUNTIME_PROVIDER` is unset.
The Step 2B implementation is extended by the clean warm pool below; MCP remains outside scope.

Step 2C adds [single-node resource admission](docs/resource-admission.md) to managed
containerd notebooks: configurable CPU/memory/PID profiles, detected Linux capacity,
host reserves, atomic reservations, verified limits and cleanup-aware release.
The default profile is 0.5 CPU, 512 MiB and 128 PIDs. Use
`POST /api/runtime/provision?notebook_id=A&resource_profile=PROFILE` to select a
configured profile and `GET /api/runtime/admission` for accounting metrics.
Local development retains its existing behavior and has no enforced resource budget.

Step 2D adds [durable runtime recovery](docs/runtime-recovery.md) with SQLite,
startup inventory reconciliation, verified surviving kernel adoption and fenced SDK
operations. Set `EUROPA_RUNTIME_REGISTRY` to private persistent local storage.
Durable containerd shutdown preserves runtimes; explicitly delete them through
`DELETE /api/runtime?notebook_id=A`. `GET /api/runtime/recovery` reports blockers
and operator states. Local process adoption remains unsupported.

Step 2E adds [clean runtime prewarming](docs/runtime-prewarming.md): a fully reserved,
durably unassigned Gateway with zero kernels and an empty workspace. The default
`EUROPA_WARM_TARGET=1` keeps at most one ready container when capacity permits;
`0` disables and reclaims the unassigned pool. `EUROPA_WARM_PROFILE` selects its
resource profile. Claims commit atomically before kernel startup, and assigned
containers never return to the pool. `GET /api/runtime/pool` reports readiness,
capacity deferrals and resource accounting. Local development has no warm pool.

Step 2F adds [durable runtime activity, session leases and idle cleanup](docs/runtime-lifecycle.md).
Assigned containerd notebooks default to a 30-minute idle timeout with 30-second
grace and sweep intervals. `EUROPA_IDLE_CLEANUP=0` disables automatic retirement.
Active operations remain protected after HTTP disconnect; uncertain crash leftovers
retain reservations for reconciliation. `GET /api/runtime/activity` reports activity
and uncertainty. Unassigned warm containers keep their separate pool policy.

Phase 3A adds [standard notebook persistence and cell APIs](docs/notebook-persistence.md).
`EUROPA_NOTEBOOK_ROOT` selects private durable storage, independent of runtime
filesystems. `/api/notebooks` supports revision-checked notebook/cell management;
cell execution returns a durable receipt and saves standard Jupyter outputs.
Runtime deletion leaves documents intact. Saved notebooks do not checkpoint Python
variables; completed results conflicting with newer edits remain in their receipts.

See `docs/phase2-review.md` for migration results and limitations. Original code remains
at Git commit `c35de54ebdb282799c58239230f7e09e4c7f6680`.
`docs/modularOverview.png` is historical, not the current architecture.

Phase 3B adds a separate private SQLite application database, selected with
`EUROPA_APPLICATION_ROOT` (default: `application/` inside the notebook root).
Start with `POST /api/projects` and associate a saved notebook using
`POST /api/projects/{id}/notebooks`. Application mutations require an
`operation_id`; mutable records also require their current `revision`.
Project archive/delete retains notebook files, outputs, histories and runtimes.
See the [API examples and schema](docs/conversational-notebooks.md) and
[Phase 3A safety review](docs/phase3b-safety-review.md). No agents or frontend
are implemented. Receipts remain retained; output content remains untrusted.
