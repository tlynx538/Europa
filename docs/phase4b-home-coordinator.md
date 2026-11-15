# Phase 4B — Home Coordinator

Status: implemented on `feature/phase4b-home-coordinator` on top of the Phase 4A
harness (PR #5, `1d13685`). Validation evidence and pending runs are in
[phase4b-validation.md](phase4b-validation.md). The owner brief is
[agent-prompts/phase4b-home-coordinator.md](agent-prompts/phase4b-home-coordinator.md);
the harness contracts are in [phase4-harness-design.md](phase4-harness-design.md).

## What is built

From Europa Home (`/`, Files or Running tab), the owner can talk to a **Home
Coordinator**. It discusses an objective and, when a notebook is appropriate,
proposes one project folder and one notebook. Nothing is created until the owner
approves that exact proposal. After approval the backend creates exactly the previewed
resources through the existing journaled workspace APIs, links the notebook to the
Home conversation, and the reply offers **Open notebook**. The new notebook's
conversation starts with a Coordinator message recording its Home origin.

- Journey A (discussion): "How do inflation and housing prices relate?" — a reply
  and a clarifying question; nothing is created.
- Journey B (create): "I have sales data and want to forecast revenue." with a model
  that proposes `Revenue Forecasting/exploratory-analysis.ipynb` — approval card with
  `Create project folder Revenue Forecasting/` and
  `Create notebook Revenue Forecasting/exploratory-analysis.ipynb`; approve creates
  exactly `.europa-project.json` and `exploratory-analysis.ipynb` in that folder
  (backend test `test_approved_creation_makes_exactly_the_previewed_resources`).

Out of scope and not built here: Notebook Coordinator UI, roadmaps, task delegation,
cell edits, code execution, streaming, provider selection UI, token dashboards,
reviewer, deleting or moving files. PR #5 registers 4C/4D tools and roles in the
harness; 4B leaves them unchanged and the Home role cannot see or call them.

## Configuration

Agents are **disabled by default**. With `EUROPA_AGENT_PROVIDER` unset,
`GET /api/agent/status` returns `{enabled: false, ...}`, every other `/api/agent`
endpoint returns 503 with the reason, and the Home panel renders nothing, so the
Phase 3 UI is unchanged.

| Variable | Meaning |
|---|---|
| `EUROPA_AGENT_PROVIDER` | `demo`, `openai` (OpenAI-compatible) or `openrouter`; unset disables agents |
| `EUROPA_AGENT_MODEL` | Model name (required for `openai`/`openrouter`) |
| `EUROPA_AGENT_BASE_URL` | Chat Completions base URL; HTTPS required except loopback |
| `EUROPA_AGENT_API_KEY` | Key; falls back to `OPENAI_API_KEY` / `OPENROUTER_API_KEY`. OpenRouter requires a key |

Keys are read from the environment only. They are never persisted, logged, returned,
placed in model context or in events. Provider `reasoning` fields are discarded;
provider error bodies are never echoed.

### Deterministic demo provider

`EUROPA_AGENT_PROVIDER=demo` selects `agents/demo.py`, a rule-based provider. It is
**not an LLM**; the UI labels it "Deterministic demo provider (not an LLM)".

- It reads only the unfenced owner message (text after the last `Owner message:`
  marker) and tool results; fenced workspace/conversation data is ignored.
- Intent words (`want to`, `create`, `set up`, `analyze`, `understand`, `forecast`,
  `explore`) produce one `create_project_notebook` proposal. The name comes from the
  key words after the last intent word (stop words removed, at most four words):
  "I want to understand a simple Markov chain." → `Markov Chain/markov-chain.ipynb`.
- Other messages get a short discussion reply that asks whether to set up a notebook.
- After a tool result it reports `Created <notebook_path>. Use Open notebook…`, a
  denial ("Nothing was created"), expiry/cancellation, or the conflict summary.
- **Deviation:** the demo names Journey B "I have sales data and want to forecast
  revenue." as `Revenue/revenue.ipynb` (words after the last intent word), not
  `Revenue Forecasting/exploratory-analysis.ipynb`. The exact Journey B files are
  covered with a scripted test provider.

## API (`/api/agent`)

All routes share Europa's loopback Host validation and same-origin mutation checks.
Request bodies use `extra='forbid'`; a client cannot choose role, author, tool,
capability or budget.

| Route | Behavior |
|---|---|
| `GET /status` | `{enabled, provider, model, reason}` (always available) |
| `GET /home/conversation` | Most recent registered Home conversation; creates one on first read |
| `POST /home/conversations {operation_id}` | 201; new Home conversation; exact replay returns the same record |
| `POST /runs {operation_id, conversation_id, message_id}` | 201. Extra fields (e.g. `role`) → 422; message from another conversation → 422; non-user message → 422; non-Home, non-notebook conversation → 409; exact replay → same run; same operation with a different payload → 409 |
| `GET /runs?conversation_id=` | `{items}` of public run views with `approvals` (id, tool, state, digest, preview, expires_at, …) and `tool_calls` (id, name, state, summary, status, links, navigation-only data). Never steps, arguments or bindings |
| `GET /runs/{id}` | One public run view |
| `GET /runs/{id}/events?after=&limit=` | Append-only public events with `next_cursor` |
| `POST /runs/{id}/cancel {operation_id}` | Cancel (see state machine) |
| `POST /approvals/{id} {operation_id, decision, digest}` | Decide one approval (see below) |

Owner messages are posted through the existing
`POST /api/conversations/{id}/messages`, which assigns `author_role='user'`.

## State machines

Run: `queued → running → (waiting_for_approval → queued → running)* → completed | failed`;
`cancelling → cancelled`; restart recovery may end a run as `interrupted`.
Cancelling a `queued` or `waiting_for_approval` run cancels it immediately and
invalidates its pending approval; a `running` run becomes `cancelling` and stops after
the current step. A model reply that arrives after cancellation is discarded.

Tool call: `rejected` (policy/invalid/bind conflict, fed back to the model) |
`awaiting_approval → approved → executing → succeeded | failed | uncertain`, or
`awaiting_approval → denied | expired | cancelled`. Read-only tools go straight to
`executing`.

Approval: `pending → approved | denied | expired | invalidated`.

## Approval, digest and binding

`create_project_notebook(project_name?, notebook_name, purpose)`; names match
`^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$` (notebook adds `.ipynb`).

At proposal time `bind` inspects the workspace through the verified browser:

- the root entry exists and is not a Europa project → conflict;
- it is a Europa project → `create_project = false`;
- the notebook path already exists → conflict;
- a name that differs only in letter case → conflict; directories are searched across
  all listing pages, not only the first 100 entries (4B fix, see validation).

Binding `{project_path, create_project, notebook_path}`; preview
`{title: 'Create resources', actions: [...], purpose}`. The digest is
`sha256` of canonical JSON (`sort_keys`, compact separators) of
`{tool, version, arguments, binding, run_id}`. Approvals expire after 30 minutes.

Decision rules: wrong digest → 409; the approval must be `pending` and its run
`waiting_for_approval`, else 409; an expired approval is marked `expired`, its run is
requeued and the request gets 409; an exact replay (same operation and decision) is
idempotent; the same operation with a different decision → 409; a new operation on a
decided approval → 409. A decision requeues the run, which executes the stored
arguments and binding without asking the model again. One approval authorizes one call.
Denial, expiry and cancellation are fed back as "… Nothing was changed."

Execution uses only existing journaled APIs, with deterministic operation IDs derived
from the tool call: `WorkspaceMutation.create_project` (`<op>-p`),
`WorkspaceNotebookStore.associate(path, <op>-n, create=True)`, `location()`, project
identity check, `ApplicationStore.bind_physical_notebook`, then the origin message
(`<op>-origin`): "Notebook created from a Home conversation (origin <id>). Purpose: …".
Result data `{notebook_id, notebook_path, project_id, project_path, conversation_id,
origin_conversation_id}` and `links.notebook_id`; the final Home reply carries
`notebook_id`. A 409 from the workspace is a `conflict`; nothing is overwritten.

## Recovery

On start the runtime re-claims `running`/`cancelling` runs left by a previous process.
For a call left `executing`: read-only tools return to `approved`;
`create_project_notebook` re-executes with the same operation IDs, so the workspace and
application journals replay the committed results exactly. If a journal reports an
uncertain earlier outcome (409, e.g. an `mkdir` whose inode was never journaled), the
call becomes `uncertain` and the run `interrupted`; it is never retried automatically.
Cancel-requested runs become `cancelled`; others are requeued (`run_recovered`) and
rebuild model input from persisted steps (`system, user, assistant, tool`).

## Frontend

- `web/src/agentApi.ts`: types, `agentStatus`, `conversationRuns`, and persist-before-send
  `startRun`, `decide`, `cancelRun`, `newHomeConversation` and Home message posting.
  Each request (path and body with `operation_id`) is stored under
  `europa.agent.pending.<scope>` before sending, cleared only on success or a 4xx, and
  every retry reuses it.
- `web/src/AgentRun.tsx`: `useAgentRuns` polls every 0.8 s while a run is active,
  2.5 s while waiting for approval and 4 s otherwise; `onChange` fires only when a run,
  tool-call or approval state changes. `AgentRunPanel` is open while active or awaiting
  approval and collapses to "Completed · N actions". `ApprovalCard`
  (`role=region`, `aria-label="Approval: <title>"`) shows title, purpose, the exact
  actions and expiry, and posts `{decision, digest, operation_id}`.
- `web/src/HomeCoordinator.tsx`: fetches status on mount and renders nothing unless
  enabled; otherwise a `<details class="home-coordinator">` (open state in
  `localStorage`) with the provider label, a scrollable `.home-thread`
  (`data-author="user|coordinator"`), Open notebook on replies with `notebook_id`, run
  panels under their triggering message, and a composer (Enter sends, Shift+Enter
  newline, per-conversation draft, Send, New, unknown-outcome Retry).
- Integration is additive: `Dashboard` gained an optional `coordinator` slot at the top
  of `<main class="dashboard-workspace">`; `PhysicalWorkspace` fills it only at the
  workspace root.

Deviations accepted by the owner:

- **Stop agent is also shown while a run waits for approval** (not only while active).
  Cancelling a waiting run is a valid backend action and invalidates the pending
  approval; Stop is hidden while cancelling and after the run ends.
- **Home messages use the agent pending store** (`europa.agent.pending.message.<conversation>`)
  rather than `applicationApi.change()`, so the Home panel is not blocked by, and does
  not block, the global application pending operation. The message is still posted
  before the run is started.

## Schema v7 migration

The v6→v7 migration (PR #5) is **forward-only**. Back up `application.sqlite3` (with
Europa stopped, copy the file together with any `-wal`/`-shm` files) before running
this version against existing data. There is no downgrade path; do not point an
older Europa build at a migrated database (its behavior against v7 was not tested).

## Security limits

- No end-user authentication; Europa must stay bound to loopback. Same-origin and
  Host checks apply to agent routes as to the rest of the API.
- Workspace names, conversation text and tool results reach the model only inside
  `<untrusted source="…">` fences; policy (roles, tools, capabilities, budgets,
  approvals) is enforced in Python regardless of model output.
- Home budgets: 6 model requests, 10 tool calls, 24 steps, 600 s active time,
  2048 output tokens, ~24,000 context characters. At most 2 runs execute concurrently.
- Real providers (OpenAI, OpenRouter) were not exercised in 4B validation; only the
  scripted test provider, an `httpx.MockTransport` adapter test and the demo provider.

## Known limitations

- `GET /api/agent/home/conversation` creates the first Home conversation on first read
  (a GET with a write; it only happens when agents are enabled).
- The crash-after-effect recovery test approves while the runtime is stopped and
  applies the effect directly with the call's own operation ID, then marks the run
  `running` and the call `executing` with no tool step. It models a crash after the
  effect and before the result was recorded, but not a literal mid-run process kill.
- Notebook-scoped runs (Phase 4A/4C roles) can be started through the API on canonical
  notebook conversations; 4B adds no UI for them.
