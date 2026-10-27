# Phase 3E — owner-facing notebook UI

Continues `feature/europa-web-ui` from Phase 3D commit `1ac18a6`. Phase 3C supplied
the initial editor preview; 3D connected basic persistent workspace records. This
phase adds reliable editing/recovery, responsive chrome, safe rich output and
production browser serving. It does not add agents, MCP, uploads, authentication,
distributed services, a replacement notebook store, or new execution machinery.

## Running

Build `web/` with `npm ci && npm run build`. Start the repository API with:

```sh
.venv/bin/python -m uvicorn main:app --app-dir api/src --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. FastAPI serves only the fixed build index and assets;
there is no arbitrary filesystem browsing or catch-all mapping of API failures to
HTML. Rebuild before restarting the API when adding/removing the assets directory.
For development, Vite's loopback proxy preserves Host so same-origin validation
works without allowing arbitrary browser origins. Do not run multiple API workers.

## Interface and persistence

- Desktop has keyboard/pointer-resizable sidebars, persisted collapse/width state,
  and a notebook-prioritizing center. Under 850px, sidebars use native modal dialogs
  with focus containment, Escape/close, and focus return.
- Notebook creation/opening/renaming, code/Markdown cells, insertion, deletion,
  reordering, Python highlighting, execution counts, Markdown preview toggle,
  Shift+Enter execution, Cmd/Ctrl+S saving, and Escape-to-blur use CodeMirror.
- Autosave waits one second and serializes mutations against the latest acknowledged
  document revision. Run flushes drafts first. Accepted executions own their tasks
  on the server; UI controls never equate a lost HTTP request with cancellation.
- Browser drafts are stored before sending mutations. A recovered draft from a
  different revision or one requiring review pauses autosave. The comparison panel
  explicitly rebases or discards drafts; deleted-cell drafts must be copied before
  discarding. Failed writes do not blindly retry old revisions.
- Saved results refresh from the authoritative notebook without dropping unrelated
  local drafts. Concurrent saved-source changes pause editing persistence rather
  than silently accepting a newer revision as permission to overwrite it.
- Execution history is recovered independently of markers, including receipts for
  deleted/edited cells. `prepared`, `running`, `completed`, `applied`, `conflict`, and
  `uncertain` remain distinct. Only `applied`, `conflict`, and `uncertain` are settled
  polling outcomes. Transport failure is a separate reconnect state.
- Conflict output inspection does not change the document. An explicit copy may
  replace saved outputs only for unchanged-source code, using SHA256 plus the existing
  whole-document CAS save. The copied output has provenance metadata, no applied
  execution marker, and does not change the conflict receipt or satisfy execution
  evidence requirements. Dismissal closes the preview; no receipt is deleted.
- Receipt-based interrupts verify runtime identity/generation and kernel ownership,
  never provision a new runtime, and preserve durable lifecycle operation fencing.
  Interrupt acknowledgments remain server-owned after browser disconnection, blocking
  newer notebook execution admission until the interrupt request has settled.
  Acknowledgment is not completion: polling may later show saved `KeyboardInterrupt`
  error output or an uncertain outcome.
- The status area separates API connection, observed runtime/kernel availability,
  pending execution receipts, and document saving. Opening a document does not create
  a kernel; saved documents are not Python interpreter checkpoints.

## Workspace APIs

The UI uses existing persistent project/membership, conversation, message, roadmap,
comment and activity services. All history pages are followed, not just page one.
It supports project selection, membership, conversation selection and creation,
message replies and needs-input messages, full task field editing, task insertion,
removal/reordering, objectives, owner acceptance evidence, comment replies/resolution,
anchor status badges, linked-cell navigation and contextual owner activity.

Progress and compact/checkmark/progress-bar flags come directly from server roadmap
responses. The client does not implement graph validation, acceptance logic or its
own progress formula. Stale plans fail without replacing the editor's task draft.
Public authorship remains user-only; no synthetic agent responses are generated.

Application mutations persist the exact path/method/body and operation ID before
dispatch. Unknown outcomes block new application mutations until explicit identical
retry; the server supplies idempotent replay. Notebook mutations instead reconcile
revision state because their APIs do not have operation-idempotency contracts.

Two narrowly scoped read/control endpoints were added:

- `GET /api/notebooks/{id}/session`: observed readiness and owned-kernel count only;
  no provisioning or operational registry fields.
- `POST /api/notebooks/{id}/executions/{execution}/interrupt`: existing verified
  binding only; no replacement allocation or blind use of a saved kernel ID.

Public receipt lookup is now a projection: identity, source fingerprint, creation
time, state, standard outputs/count/status. Private runtime/generation/kernel and
protocol/display mappings remain in storage, not frontend responses. The existing
receipt listing supplies reconnect identities, including after marker invalidation.

## Rendering and browser boundary

All output is untrusted; nothing changes the original saved MIME bundles.

| MIME | Display policy |
|---|---|
| stdout/stderr, traceback, plain text | Escaped React text; nonexecuting ANSI-stripped error text |
| JSON | Escaped JSON viewer |
| HTML/tables, static SVG | DOMPurify tag/attribute allowlist; no scripts, handlers, resource/link/style attributes, embeds or animation. Opaque-origin iframe with no sandbox permissions, no referrer, and no-network/no-script CSP |
| PNG/JPEG | Embedded base64 only, signature and header dimensions checked before assigning a URL; encoded size <= 5.6 MB, dimensions <= 8192, pixels <= 16 million; decode errors stay inert |
| Plotly JSON | Limited static scatter/bar data preview using React SVG; at most 20 traces / 2000 points per trace. No Plotly JS, callbacks, remote resources, templates or interactive layout |
| JavaScript/widgets/custom MIME | Inert notice or escaped fallback; original data retained |

Markdown ignores raw HTML, replaces images with alt-text notices and renders links
as text. It makes no automatic external fetches. Rich output is not directly injected
into Europa's DOM. Sanitization and the iframe are separate protections; malicious
content cannot opt into a trusted rendering mode.

The served UI has a restrictive CSP, no-referrer and nosniff headers. API responses
are no-store. Browser mutations require same-origin and reject cross-site fetches;
Host validation limits the unauthenticated application to loopback hostnames. These
controls are not authentication or hostile-Python isolation. Continue binding to
loopback; keep Gateway credentials, private storage and containerd access server-side.

## Remaining boundaries

- Plotly support is a data preview, not full Plotly compatibility; unsupported traces
  and interactive features are not executed. Rich iframe height is fixed with internal
  scrolling. No trusted-output switch, widget runtime, remote resource loading, or uploads.
- Browser drafts/pending operations depend on the same origin and browser storage;
  clearing site data loses unsaved browser state. Browser storage is not encrypted,
  collaborative, quota-managed archival storage, or a backup. One owner tab at a time
  is recommended. Browser selection/collapse state is not a server preference service.
- The backend retains all receipts, plans and histories; no aggregate disk quotas/GC
  were added. Receipt listing scans retained files before pagination, and the UI polls
  buffered snapshots rather than streaming. Large-history throughput is not claimed.
- No claim is made that local-provider kernels survive backend restart. Verified
  durable containerd recovery retains its existing conservative semantics. Neither
  saved outputs nor reconnect invent missing live output or interpreter variables.
- Basic creation/comment controls still use native browser prompts. Advanced dataset,
  arbitrary metadata and notebook import/export interfaces are not part of this phase.

## Validation

Validation uses trusted fixture source and disposable owner storage, not user notebooks.
Infrastructure-gated skips are not passes. The existing ARM64 VM uses the cached runc
image and existing CNI: 0.25 CPU / 384 MiB / 128 PID per allocation, with total ceilings
of 0.5 CPU / 768 MiB. No daemon/network configuration or VM resource setting changed.

| Check | Result |
|---|---|
| `npm run build` | TypeScript and production build passed; vendor chunks separated, no >500 kB chunk warning |
| `npm test` | 11 unit/component tests passed |
| `.venv/bin/python -m pytest -q` | 240 passed, 103 infrastructure-gated skips |
| Focused real-local application restart, Gateway protocol and browser-contract selection | 24 passed |
| Production Chrome browser suite | 11 passed; 1 explicit VM opt-in skip (run separately below) |
| ARM64 browser against production FastAPI/runc | 1 passed; observed `aarch64`, successful `42`, receipt applied/ok and saved output after refresh |
| ARM64 receipt/session/interrupt API probe | 1 passed, including lifecycle-protected interrupt and verified disposal |

The browser suite covers the owner workflow; task acceptance; message replies;
comments/resolution/anchors; refresh recovery; serialized autosave and keyboard
execution; stale revision comparison and draft recovery through refresh; malicious
HTML/Markdown; responsive modal drawers and collapse/resize; transport reconnection;
idempotent retry after a deliberately lost response; explicit historical conflict
output import and rejection after source changes; and an actual stop/start of the
backend process serving the production UI. The backend-restart test checks saved
outputs and messages plus the honest loss of local-provider live-kernel state.

Reproduce the local suites with:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_application_integration.py tests/test_gateway_protocol.py tests/test_web_contracts.py --run-local-gateway -q
cd web
npm test
npm run test:browser
```

The regular browser suite explicitly skips the separate VM opt-in. With the approved
VM probe server running on an already forwarded loopback port:

```sh
EUROPA_VM_URL=http://127.0.0.1:18200 EUROPA_VM_ONLY=1 npx playwright test vm.spec.ts --output=test-results-vm
```

`tests/web_vm_server.py` requires `EUROPA_WEB_VM_PROBE=1` and the existing operator
containerd/image/CNI/resource settings. It has a 180-second lifetime and explicitly
destroys its own probe allocations on graceful exit using the production ownership
APIs. It does not change the production app's runtime-preserving shutdown policy.

Integration observations: the first development-browser run correctly rejected
mutations because Vite's shorthand proxy changed Host. An explicit preserving proxy
fixed this without weakening origin validation. Keyboard testing found CodeMirror's
default Shift+Enter binding taking precedence; the notebook keymap now has explicit
highest precedence. Early VM probes were invalid: one used the wrong CNI binary
path and required verified targeted cleanup; a second passed execution but failed
writing evidence into the read-only host share. The final runs use the existing CNI
binary path, the established privileged bridge wrapper, and guest-local evidence.
A root-run probe also hit the existing owner-protected admission lock; it was rerun
as the established guest owner, without deleting or weakening the lock.
A later API rerun passed its assertions but could not overwrite root-owned evidence
from an earlier attempt. The final passing run uses a fresh guest-owned working
directory; earlier environment failures are not counted as validation passes.

Audit evidence is retained under guest `~/europa-validation/phase3e-validation/`;
host unit JUnit and browser outputs are Git-ignored test artifacts. Do not copy raw
notebooks, receipts, registries, credentials or private test roots into Git.

Final infrastructure audit: containerd/system-CNI/private-CNI hashes, routes and
firewall match the established baseline; forwarding remains **0**. No Europa tasks,
containers, managed snapshots, state directories, IPAM addresses or probe workload
processes survive. Permanent owner/fence coordination files and cached images remain.
The full old containerd suite and experimental runsc cases were not rerun for 3E;
the new bounded runc/API and actual browser execution probes were run separately.

### Screenshots (synthetic fixture content only)

- [Desktop notebook, roadmap and threaded discussion](screenshots/phase3e/desktop.png)
- [Mobile modal drawer](screenshots/phase3e/mobile.png)
- [Explicit revision comparison](screenshots/phase3e/conflict.png)
- [Sanitized rich output](screenshots/phase3e/safe-output.png)
- [Recovered UI after backend restart](screenshots/phase3e/restart.png)
- [Actual ARM64 runc browser execution](screenshots/phase3e/arm64.png)
