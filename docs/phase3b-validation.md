# Phase 3B completion and validation

Branch: `feature/conversational-notebooks`, based on
`a5f715d970f4ba7ab9497a51d675c7d0ae28c40c`. Final implementation SHA is recorded
in Git history and the completion response; no agents/frontend work follows it.

## Delivered files

Added:

- `api/src/application/__init__.py`: application package boundary.
- `api/src/application/models.py`: bounded typed input/output contracts.
- `api/src/application/store.py`: SQLite schema/migration, transactions, histories,
  references, idempotency, progress, comment anchors and lifecycle policies.
- `api/src/application/router.py`: `/api` project/conversation/roadmap/comment/activity APIs.
- `docs/phase3b-safety-review.md`: completed prerequisite Phase 3A safety review.
- `docs/conversational-notebooks.md`: schema, API examples and operational contracts.
- `docs/phase3b-validation.md`: this completion/validation report.
- `tests/test_application.py`: 48 new storage/API cases.
- `tests/test_application_integration.py`: real local FastAPI restart/execution test.

Modified:

- `api/src/main.py`: application store lifespan and router wiring.
- `tests/conftest.py`: isolate application storage in disposable test directories.
- `tests/test_notebook_integration.py`: one bounded real runc application-history probe.
- `README.md`: link and describe the new application foundation.
- `docs/notebook-persistence.md`: identify the Phase 3B successor contracts.

Removed: none. Dependency pins, GatewayClient, notebook storage implementation,
runtime ownership/fencing DB, OCI mounts, CNI, daemon configuration and VM resources
are unchanged.

## Database and contracts

Separate `application.sqlite3`, WAL/FULL synchronous, foreign keys and transactional
schema migration `user_version=0 -> 1`. Tables: catalog, records, memberships,
versions and operations. The runtime registry is untouched. Typed JSON plan
snapshots retain tasks/evidence while SQL FKs retain resource identity. Application
validation enforces cell/receipt/task/reply scope; no distributed transaction with
`.ipynb` files is claimed. Startup/operation reconciliation preserves standalone
notebooks and missing historical references without rewriting documents.

API route families: `/api/projects`, project notebook membership,
`/api/conversations` and messages, `/api/roadmaps` and tasks/versions,
`/api/notebooks/{id}/comments`, `/api/comments/{id}`, and project activities.
All new mutations are operation-idempotent; mutable records require revisions.
Responses expose ordered cursors, derived progress and historical anchor status.
See [complete contracts and examples](conversational-notebooks.md).

## Prerequisite safety decisions

- Datasets stay outside ephemeral containers. Future scoped transfer must validate
  project membership and runtime generation before copying selected regular files
  into bounded `/workspace`; no broad host/storage-root mounts or upload API.
- Retain every execution receipt. Age-only cleanup is unsafe for uncertain work,
  conflict recovery, application links and cross-cell display identity. The safety
  review specifies the reference inventory and archiving work required before GC.
- Preserve valid untrusted MIME output. Renderers must escape text, isolate or
  sanitize HTML/SVG, disable JavaScript and prevent automatic external fetches.
- Project delete is a logical tombstone. Files, outputs, histories and runtimes
  remain intact. Explicit disassociation can release historical membership.
- Public authorship is user-only; privileged event types require trusted server
  code. No records can directly execute Python or provision a runtime.

## Validation

Validation completed October 27, 2026, including the existing ARM64 Lima VM.

Host full Python suite: **236 passed, 102 explicitly skipped**, 338 collected.
These skips are infrastructure-gated tests, not validation passes.
Host focused application/notebook selection with real local Gateway:
**89 passed**, including actual execution and FastAPI lifespan restart.
New cases total **50**: 48 storage/API cases, one real local test and one real runc
probe. Existing runtime and notebook test logic is preserved.

The initial VM command omitted `EUROPA_RUNTIME_PROVIDER=containerd`, causing crash
helpers to select local runtimes and later memory-admission failures. That run
was **not** accepted: 19 failed, 298 passed, one skipped, 15 deselected. Its logs
are retained as `initial-misconfigured.*`. Six process-identity-verified local
Gateway/kernel processes were cleaned; one remaining allocation was recovered
and destroyed using its original runtime registry and production fencing APIs.
The namespace was verified empty before the corrected run. No infrastructure
settings were changed to work around failures.

The corrected VM command selects containerd explicitly and uses the established
ARM64 Lima VM, cached image, runc, private CNI and bounded resource profiles:

```sh
.venv/bin/python -m pytest --run-local-gateway --run-containerd \
  -k 'not (test_real_container_runtime and runsc) and not test_real_cold_warm_benchmark' \
  -q --junitxml=PATH/suite.xml
cd containerd-bridge
go test -count=1 -v ./...
```

Fourteen experimental runsc scenarios remain explicitly deferred/deselected.
The long cold/warm benchmark is excluded; this milestone makes no new performance
claim. The daemon-restart scenario retains its separate opt-in and is not counted
as passing. Bounded runc probes retain the existing CPU/memory/PID ceilings and
exercise document execution, saved results, interruption, recovery, prewarming,
idle cleanup and application persistence without changing CNI or forwarding.

Corrected VM Python result: **322 passed, 1 skipped, 15 deselected** in
652.27 seconds, with no failures or errors. The one skip is the separately
opted-in containerd daemon-restart test; the 15 deselections are the fourteen
deferred runsc cases and the long cold/warm benchmark. These are not passes.
Native Linux Go bridge: **6 passed**, including fencing, hardened OCI specs,
network validation and durable ownership/generation checks.

The final independent infrastructure audit passed: no Europa tasks/containers,
managed snapshots, runtime state directories, allocated IPAM addresses or workload
processes survived. Containerd/system-CNI archive hashes, private CNI profile,
routes and firewall match the saved baseline. `europa0` remains 10.77.0.1/24;
IPv4 forwarding remains **0**. VM resource allocation is unchanged. Final available
memory was approximately 1332 MiB and free root disk 4.19 GiB; these are snapshots,
not peak resource measurements. No infrastructure rollback is needed.

Final evidence files: `suite.log`, `suite.xml`, `go.log`, `final-audit.json`,
host `unit-continued.xml` and `focused-continued.xml`. A continued host run again
confirmed **236 passed, 102 skipped**; the real local focused selection again
confirmed **89 passed**. These runs verify the same implementation used in the VM.
The first continued local integration attempt could not bind its loopback port
inside the host sandbox (88 passed, one environment failure); rerunning with
loopback access enabled supplies the focused result above.

Ignored evidence: host `test-results/phase3b/`; VM
`~/europa-validation/phase3b-validation/`. Only logs, JUnit and audits belong in
completion evidence; raw private notebooks, runtime registries, credentials and
execution receipts are excluded from Git.

## Known limitations and next milestone

Single-node/trusted-owner only; no end-user authentication, distributed coordination,
frontend, real agents, LLM integration, MCP, streaming or dataset transfer. Histories,
idempotency responses and receipts have no aggregate quota or GC yet. Presence
reconciliation scans notebook documents each application operation. Task acceptance
is explicit owner attestation with optional verified message/execution references;
automated Coordinator review is future work. Local execution has no hostile-code
isolation; runc retains its shared-kernel boundary and runsc remains deferred.

Recommended next milestone: owner-facing notebook/roadmap/discussion UI with
sanitized output rendering, explicit acceptance and stale/orphaned anchor handling.
Design trusted Coordinator review and scoped dataset access separately before
implementing agent orchestration. Phase 3B stops here.
