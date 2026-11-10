# Europa

Europa is a single-owner notebook workspace built with FastAPI, React, and
Jupyter Kernel Gateway. It combines persistent Jupyter notebooks with projects,
roadmaps, conversations, comments, execution history, and managed Python runtimes.

Europa is intended for trusted local use. It has no end-user authentication and
must remain bound to loopback.

## What Europa provides

- A browser notebook editor with Python syntax support and keyboard shortcuts
- Revision-checked notebook persistence and autosave conflict handling
- Durable execution receipts, reconnect recovery, and verified interruption
- Standard Jupyter outputs, including safely rendered rich output
- Projects, versioned roadmaps, tasks, conversations, and cell comments
- Inline cell comment and reply editors with scoped draft recovery
- Local development runtimes managed through Jupyter Kernel Gateway
- Optional containerd+runc runtimes with resource admission, durable ownership,
  recovery, prewarming, and idle cleanup
- REST APIs for notebooks, runtimes, and application records

Europa does not currently provide multi-user authentication, uploads, agents,
LLM providers, MCP integration, or distributed scheduling.

## How it works

```text
Browser UI
    │ HTTP
    ▼
FastAPI application
    ├── Notebook store (.ipynb + execution receipts)
    ├── Application store (SQLite projects and conversations)
    └── Runtime supervisor
            │ authenticated HTTP/WebSocket
            ▼
       Jupyter Kernel Gateway
            ▼
         ipykernel
```

FastAPI owns the application and persistence APIs. Jupyter Kernel Gateway owns
kernel processes and the standard Jupyter HTTP/WebSocket protocol. Europa does
not replace Gateway with a custom process manager or ZeroMQ client.

Notebook documents and execution receipts are stored independently of runtime
filesystems. Saving a notebook preserves its code and outputs, but does not
checkpoint live Python variables. Runtime deletion therefore does not delete the
notebook, while restarting a runtime loses in-memory interpreter state.

The default local runtime provider launches an authenticated Gateway on loopback
in a disposable environment. The optional containerd provider is for approved
Linux infrastructure and uses private networking, resource limits, durable
ownership records, and explicit cleanup.

## Requirements

- CPython 3.13
- Node.js and npm for the web application
- A POSIX environment for local runtime process management
- Google Chrome only when running the browser test suite
- Optional: approved Linux containerd+runc infrastructure for container tests

Dependencies are pinned in `requirements.lock`. Frontend dependencies are locked
in `web/package-lock.json`.

## Project setup

From the repository root, create the Python environment:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m ipykernel install \
  --prefix .venv --name python3 --display-name "Python 3 (Europa)"
```

Alternatively, with `uv`:

```sh
uv venv --python 3.13 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements.lock
```

Build the browser application:

```sh
cd web
npm ci
npm run build
cd ..
```

## Run Europa locally

Start Europa from the repository root:

```sh
.venv/bin/python -m uvicorn main:app \
  --app-dir api/src \
  --host 127.0.0.1 \
  --port 8000
```

Open <http://127.0.0.1:8000/>.

With `EUROPA_GATEWAY_URL` unset, Europa starts and manages an authenticated local
Gateway automatically. Stop the server with **Ctrl+C** so owned kernels and local
runtimes can be cleaned up normally.

Do not bind Europa to a public or LAN address. It has no end-user authentication.

## Try a notebook

1. In **Files**, browse to a directory and select **New → Notebook · Python 3**;
   enter a filename. Existing physical `.ipynb` files open the same notebook editor.
2. In the notebook editor, add a code cell using the notebook toolbar.
3. Enter:

   ```python
   print("Hello, Europa!")
   ```

4. Press **Shift+Enter** or select **Run selected cell**.
5. Confirm that `Hello, Europa!` appears below the cell.
6. Reload the page to verify that the notebook and saved output persist.

Useful editor shortcuts:

| Action | Shortcut |
|---|---|
| Save | Cmd/Ctrl+S |
| Save and run a code cell | Shift+Enter |
| Leave the editor | Escape |
| Focus the selected cell (command mode) | Enter |
| Add a code cell above/below (command mode) | A / B |
| Copy/paste a cell within Europa (command mode) | C / V |
| Change selected cell to Markdown/code (command mode) | M / Y |

Europa opens to the Phase 3H Files dashboard. The Europa wordmark
returns Home through the existing draft/receipt guard; **File → Close Notebook**
returns to the verified source directory. Neither action shuts down a kernel.
**File → Existing catalog projects / notebooks** retains access to private data
without migration. The Recent menu uses persisted open history. Running shows
bounded, read-only observations of managed notebooks and their kernels, including
Stopped, Unknown and Needs attention. Kernel shutdown remains unavailable in the
dashboard. See the [checkpoint validation](docs/phase3h-home-dashboard-validation.md)
and [final validation](docs/phase3h-final-validation.md) for status evidence,
refresh limits, regression results and infrastructure limitations.

Use **File → Open project** or **Open notebook** in the editor to switch workspaces. Project
overviews show their linked notebooks. **View → Roadmap panel** toggles the left
planning outline. Each notebook has one automatically restored conversation in
its canvas and a composer above the status footer, including standalone notebooks.
Agent orchestration is not implemented. Saved outputs do not restore live Python
variables after a runtime restart.

## Development workflow

For frontend development, run the API on port 8000 and start Vite separately:

```sh
cd web
npm run dev
```

Vite binds to loopback and proxies `/api` to the local FastAPI process. The
production path is the built frontend served by FastAPI, not the Vite server.

Key directories:

| Path | Purpose |
|---|---|
| `api/src/` | FastAPI application, persistence, Gateway client, runtime providers |
| `web/` | React/TypeScript browser application and browser tests |
| `tests/` | Python unit, contract, and integration tests |
| `containerd-bridge/` | Go bridge for supported containerd SDK operations |
| `gateway/` | Jupyter Kernel Gateway configuration |
| `runtime-image/` | Managed runtime image definition |
| `docs/` | Architecture, contracts, safety reviews, and validation reports |

## Persistence and configuration

Important environment variables include:

| Variable | Purpose |
|---|---|
| `EUROPA_NOTEBOOK_ROOT` | Private notebook and receipt storage |
| `EUROPA_WORKSPACE_ROOT` | Pinned physical file-browser root; defaults to launch directory |
| `EUROPA_APPLICATION_ROOT` | Private SQLite application storage |
| `EUROPA_RUNTIME_PROVIDER` | Runtime provider (`local` by default, optional `containerd`) |
| `EUROPA_RUNTIME_REGISTRY` | Durable containerd ownership registry |
| `EUROPA_GATEWAY_URL` | Optional externally managed loopback Gateway URL |
| `EUROPA_GATEWAY_TOKEN` | Token for an externally managed Gateway |

Storage roots must be private, owner-controlled locations. Never commit Gateway
tokens, runtime registries, notebooks containing private data, or test evidence.

For the complete runtime configuration, consult:

- [Runtime provider](docs/runtime-provider.md)
- [Containerd provider](docs/containerd-provider.md)
- [Resource admission](docs/resource-admission.md)
- [Runtime recovery](docs/runtime-recovery.md)
- [Runtime prewarming](docs/runtime-prewarming.md)
- [Runtime lifecycle and idle cleanup](docs/runtime-lifecycle.md)

## API overview

OpenAPI and Swagger documentation are available while Europa is running:

- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Swagger UI: <http://127.0.0.1:8000/docs>

Major API groups:

- `/api/notebooks` — notebook documents, cells, receipts, and interrupts
- `/api/projects` — projects, notebook membership, and activity
- `/api/roadmaps` — versioned plans and tasks
- `/api/conversations` — conversations and messages
- `/api/comments` — cell comment threads
- `/api/runtime` — runtime status, provisioning, cleanup, and diagnostics
- `/api/kernel` — low-level Gateway-compatible kernel operations

See [notebook persistence](docs/notebook-persistence.md) and
[conversational notebooks](docs/conversational-notebooks.md) for detailed API
contracts and consistency rules.

## Security model

Europa is a trusted, single-owner localhost application:

- FastAPI and local Gateways bind to loopback.
- Gateway authentication is mandatory.
- Browser mutations are same-origin and Host-validated.
- The production UI uses restrictive CSP, no-referrer, and nosniff headers.
- Rich HTML and SVG outputs are sanitized and isolated in sandboxed iframes.
- Local Python execution is not isolated from the host.
- Runc containers share the host Linux kernel and are not equivalent to a strong
  hostile-code sandbox such as gVisor.

Do not execute untrusted Python with the local provider. Read the
[safety review](docs/phase3b-safety-review.md) before changing storage, output
rendering, or runtime lifecycle behavior.

## Testing

Run the standard checks from the repository root:

```sh
.venv/bin/python -m pytest -q

cd web
npm test
npm run build
```

Real local Gateway tests explicitly opt into executing trusted repository code:

```sh
.venv/bin/python -m pytest --run-local-gateway -q
```

The browser suite builds the UI and starts disposable loopback services:

```sh
cd web
npm run test:browser
```

Containerd tests require approved Linux infrastructure and explicit
`--run-containerd`. Skipped integration tests are not successful validation.

## Documentation

- [Release notes](RELEASE_NOTES.md)
- [Owner notebook UI](docs/phase3e-ui.md)
- [Phase 3F-B navigation validation](docs/phase3f-b-validation.md)
- [Phase 3F-D UI recovery validation](docs/phase3f-d-validation.md)
- [Phase 3G notebook UX validation](docs/phase3g-validation.md)
- [Notebook persistence](docs/notebook-persistence.md)
- [Projects and conversations](docs/conversational-notebooks.md)
- [Runtime architecture](docs/runtime-provider.md)
- [Containerd validation](docs/step2b-runc-validation.md)
- [Safety review](docs/phase3b-safety-review.md)

Historical diagrams and earlier milestone reports may describe superseded
architecture. Current behavior is defined by the implementation, current contracts,
and the latest documentation linked above.
