# Phase 4C — Notebook Coordinator

Status: implemented on `feature/phase4c-notebook-coordinator` (base `main` `ad0ca1a`, Phase
4A harness and Phase 4B Home Coordinator merged). Validation evidence is
in [phase4c-validation.md](phase4c-validation.md). The owner brief and PRD are
[agent-prompts/phase4c-notebook-coordinator.md](agent-prompts/phase4c-notebook-coordinator.md);
harness contracts are in [phase4-harness-design.md](phase4-harness-design.md).

## What is built

Inside an open notebook the owner talks to a **Notebook Coordinator** in the notebook's
existing canonical conversation. There is no separate chat sidebar or discussion panel.
The Coordinator reads this notebook (cells, outputs, revision), its project, the notebook's
roadmap and the earlier discussion. It can discuss the notebook, propose a roadmap, revise
it, and explain task state, including why work is blocked. A roadmap is saved only after the
owner approves the exact plan, bound to its digest and to the roadmap revision it was
proposed against. Task completion stays an owner action in the existing task editor.

Journeys (validated with the deterministic demo provider in Chrome and with scripted
providers in backend tests):

- J1 discuss: "What does this notebook do so far?" gets a reply about this notebook.
  Nothing changes.
- J2 plan: "Plan how to understand this Markov chain." shows an inline approval card
  (**Create roadmap**) with four chained tasks: Define a transition matrix → Simulate
  transitions → Estimate the stationary distribution → Visualize convergence. After
  approval the Roadmap panel shows four *upcoming* tasks at 0 %, without a page reload.
- J3 revise: "Add a step to compare with a 3-state chain." shows **Revise roadmap**, "Bound
  to roadmap revision 1", and any removed task IDs. Approval saves revision 2 and keeps the
  existing task IDs, states and evidence.
- J4 conflict: if the owner edits the roadmap while a plan approval is pending, approving
  gives "The plan was not saved: The roadmap changed after this plan was proposed…". The
  owner's edit stays.
- J5 status: "What's left and why isn't it done?" gives the task states from `read_roadmap`.
  A task is never called done unless its roadmap status is `completed`.
- J6 standalone: in a notebook outside any project, planning is refused ("Planning needs a
  project.") and discussion still works.
- J7 persistence: messages, run panels, pending approvals, event history and the roadmap
  survive reload, a Home round trip and a server restart.

The Coordinator **cannot assign work, edit cells, run code or read other notebooks** in this
phase.

## Roles and capabilities

| Role | Capabilities | Tools |
|---|---|---|
| `notebook_coordinator` | `read:notebook`, `read:roadmap`, `propose:roadmap` | `read_notebook`, `read_roadmap`, `read_execution`, `propose_roadmap` |

`delegate_task` and the `delegate` capability are removed from this role (`api/src/agents/policy.py`).
They stay disabled until Phase 4D. The `delegate_task` tool code, `AgentRuntime.delegate` and
the `coding_agent` role are unchanged and unreachable from a notebook run. A call to
`delegate_task`, `read_notebook_summary`, `insert_code_cell` or `execute_cell` is rejected
with status `policy`, and none of these tools is offered to the provider. Notebook tools
take no notebook ID: a run acts only on its own notebook.

Budgets are the defaults: 8 model calls, 16 tool calls, 24 steps, 600 s active time, 2048
output tokens, 24 000 context characters.

`AgentRuntime._scope_for` maps a notebook's canonical conversation to the
`notebook_coordinator` role and records the notebook and its current project.
`POST /api/agent/runs` returns:

- 409 for a legacy project conversation;
- 422 for a message from another conversation, including another notebook's;
- 422 for a non-owner (for example coordinator-authored) message.

## Context

`api/src/agents/context.py` assembles bounded, provenance-tagged context. All notebook,
project, roadmap and conversation content is placed in `<untrusted source="…">` fences and
treated as data. Policy is enforced in Python.

| Section | Provenance | Contents and limits |
|---|---|---|
| Notebook | `notebook:<id>@<revision>` | ID, title, revision, `cell_count`, `omitted_leading_cells`; the last ≤ 30 cells with index, cell ID, type, source ≤ 1500 chars, and for code cells `execution_count`, last status and plain-text output ≤ 500 chars (no HTML or rich markup); `agent_authored: true` when a cell has `metadata.europa_agent` |
| Project | `project:<id>` | title and status |
| Roadmap | `roadmap:<id>@<revision>` or `roadmap:none` | roadmap ID, revision, objective, progress; per task: ID, title, status, dependencies, acceptance criteria, explanation, last 3 evidence summaries. "No roadmap yet." when there is none |
| Standalone | (unfenced) | "Project: none. This notebook is not in a project, so a roadmap cannot be created yet." |
| Conversation | `conversation:<id>` | last 12 messages: author, content ≤ 1000 chars, `cell_id` when present |
| Owner message | (unfenced) | the triggering message ≤ 6000 chars, plus "(The owner attached cell <id>.)" when it references a cell |

**Oldest-first trimming.** The notebook section is limited to 12 000 characters. When the
serialized view is longer, whole cells are dropped from the front (oldest first) and
`omitted_leading_cells` is updated to match. The newest cell is always kept, with its own
source and output limits. Only if that cell alone still exceeds the limit does the generic
explicit clip marker ("… [N more characters omitted]") apply. The whole context is limited to
the 24 000-character budget, with the same explicit marker.

Excluded: receipt runtime, kernel and generation identities, kernel sessions, execution IDs,
Gateway URLs, ports and tokens, and provider keys.

The role prompt states: "You cannot assign work, edit cells or run code in this phase.",
"Never state that a task is complete unless its roadmap status is completed." and "When the
notebook is not in a project, explain that planning needs a project."

## Plan proposal, revision and binding

`propose_roadmap(objective, tasks[{key, title, acceptance_criteria, depends_on}], rationale)`
always requires approval and allows one supervised action per step.

- **Binding.** At proposal time the harness binds
  `{project_id, notebook_id, roadmap_id, roadmap_revision, objective, tasks, removed}`. The
  approval digest covers the tool, version, arguments, binding and run.
- **Execution.** On approval it runs `roadmap_create` for a new roadmap or `roadmap_update`
  at the bound revision, with operation ID `agent-<tool_call_id>`. A stale revision fails as
  `conflict` and never overwrites. Restart reconciliation replays by operation ID.
- **No fabricated progress.** `PlanTask` has no `status`, `accepted` or `evidence` fields and
  forbids extra fields, so `status: completed` in the arguments is rejected as `invalid`. New
  tasks are `pending`. Existing tasks keep `status`, `explanation`, `evidence`, `accepted`,
  `weight` and `links`.
- **Task IDs.** A key equal to an existing task ID of this roadmap keeps that task. A key may
  be up to 64 characters (the task-ID limit), so an agent-created ID such as
  `<8-char prefix>-<40-char key>` (49 characters) can be reused in a later revision. Keys of
  **new** tasks are limited to 40 characters; longer ones are rejected as `invalid`. A new
  task gets the server-generated ID `<first 8 characters of the tool call ID>-<key>`, which
  is at most 49 characters.
- **Collisions.** A plan is rejected as `invalid` if two keys produce the same ID, or if a
  new task's generated ID equals an existing task ID (including a task the plan leaves out,
  which would otherwise be replaced silently instead of listed as removed).
- **Preview.** The approval card shows "Create roadmap" or "Revise roadmap", the objective,
  the rationale, ordered tasks with dependency titles, acceptance criteria and non-pending
  status, removed task IDs, and `roadmap_revision`.
- **Standalone notebooks.** In a notebook without a project, `propose_roadmap` is rejected
  with status `policy` before any approval exists.

## Roadmap selection

The roadmap a notebook role sees (`_notebook_roadmap` in `api/src/agents/tools.py`) is the
**most recently created roadmap in the notebook's current project whose `notebook_id` is
this notebook**. It is queried with a notebook filter and read through every page, so it
stays correct when a project has more than 100 roadmaps. Roadmaps of other notebooks in the
same project, and project-level roadmaps without a notebook, are never returned.

## Deterministic demo provider

`EUROPA_AGENT_PROVIDER=demo` (`api/src/agents/demo.py`) is rule-based, not an LLM. Runs
whose system prompt contains `Role: Notebook Coordinator` take the notebook branch. The Home
branch is unchanged byte for byte. Rules, in order:

1. **After a `propose_roadmap` result.**
   - `ok`: "Saved the roadmap with N tasks. Accept tasks in the Roadmap panel as you verify
     them."
   - `denied`: "No plan was saved."
   - Otherwise: "The plan was not saved: <summary>".
2. **Planning request in a standalone notebook.** If "Project: none" appears in the
   *unfenced* context and the message asks to plan, the demo refuses honestly. A cell
   containing that text cannot trigger the refusal.
3. **Planning request** (`\b(plan|roadmap|steps?|break down)`) calls `propose_roadmap`.
   - The template is the four Markov tasks when "markov" appears in the message or context,
     and otherwise Inspect the data → Explore key relationships → Summarize findings, chained
     by dependency.
   - With an existing roadmap (parsed from the fenced roadmap context, or read first with
     `read_roadmap` when that context is truncated), the demo reuses existing task IDs as
     keys and appends the template tasks that are not already present.
   - If every template task is already present, it appends one task titled from the
     message (for example "Compare with a 3-state chain") that depends on the last task.
4. **Status request** (`status|left|blocked|progress|done`) calls `read_roadmap`, then
   replies with a per-task status list (status, explanation, unfinished dependencies) taken
   only from that tool result.
5. **Anything else** gets a short reply naming the notebook's cell count and revision.

## UI integration (additive)

When `GET /api/agent/status` reports agents disabled (the default), the notebook UI is
unchanged. The only extra request is that status call.

When agents are enabled:

- **Ask Coordinator checkbox.** Shown above the message composer inside `.composer-shell`,
  default on, persisted in `localStorage` `europa.agent.ask`. It has an opaque surface
  because the composer floats over the stream.
- **Sending.** The existing message POST is unchanged. Only after it succeeds, and only with
  the checkbox on, the UI posts `POST /api/agent/runs` with exactly
  `{conversation_id, message_id, operation_id}`. The request is persisted before it is sent
  under `europa.agent.pending.run.<conversation>`.
- **Unknown start outcome.** An inline alert "Starting the Coordinator has an unknown
  outcome." offers Retry, which resends the stored operation. The message is never lost or
  posted twice.
- **Run panels.** They render inside the notebook stream directly after the triggering
  user message (a keyed `Fragment` with an `.agent-stream` wrapper). Child runs render as
  siblings after the parent's `<details>`, so a collapsed parent never hides a child's
  approval card. A finished panel collapses to "Completed · N actions".
- **Updates.** When a run, tool call or approval changes state, the conversation messages
  and the loaded project (Roadmap panel) are reloaded, so the roadmap appears without a page
  reload.
- **Labels and links.** Tools are labelled "Read the notebook", "Read the roadmap", "Read
  an execution receipt" and "Propose roadmap". A tool call linked to a cell offers "Show
  cell", which uses the existing `jump`; this is groundwork for 4D, since 4C tools produce
  no cell links.
- **Mobile.** At 390×844 the stream has no horizontal scroll and the approval buttons can
  be scrolled above the floating composer.

Cell rendering, menus, toolbar, autosave, conflict review and receipt behaviour are
unchanged.

## `GET /health` (owner-requested addition)

The owner asked for a top-level health route. The PRD (§4.5) had otherwise asked for no
API changes. Owner decisions (2026-11-15):

- **What it reports.** `/health` (also `HEAD`) reports the API's own health plus the
  Jupyter Gateway's current state, observed without starting or attaching a Gateway,
  runtime or kernel.
- **Status codes.** HTTP 200 whenever the API works, for example
  `{"status":"ok","api":"ok","gateway":"stopped","agents":"disabled"}`. HTTP 503 only for
  real API faults: `{"status":"unavailable","api":"unavailable","gateway":…,"agents":…,
  "unavailable":["application_storage"|"notebook_storage"]}`, which happens when the
  application database is closed, unreadable or locked for more than 2 s, or the notebook
  storage handle is closed or its directory removed.
- **`GET /api/health` is unchanged.** It still provisions the default runtime through
  `supervisor.client_for`.

**How the Gateway state is read** (`api/src/internal/health.py`). The route reads the
supervisor's in-memory tables of started or attached runtimes and the clients connected to
them. Only a runtime that already has a connected client is probed, with one read-only
authenticated request (`GET /api/kernelspecs`) limited to 2 s. It never calls
`client_for`, `ensure`, `attach`, `inspect` or a provider method. This works the same for
the auto-managed local Gateway and an external `EUROPA_GATEWAY_URL`.

| State | Meaning |
|---|---|
| `stopped` | nothing started or attached |
| `starting` | a start is in progress, or a runtime has no client yet |
| `stopping` | a runtime is being shut down |
| `failed` | a runtime that is not ready |
| `running` / `unreachable` | result of the probe |

With several runtimes, the most severe state is reported. `agents` is `running` or
`disabled`.

The body contains no paths, URLs, ports, tokens, runtime or kernel IDs, provider keys or
exception text. Responses carry `Cache-Control: no-store`. The loopback Host check still
applies (403 otherwise). The route does not shadow SPA routes.

In a normal server start the local Gateway is already started during startup, so `stopped`
mostly appears before first use or after the runtime is shut down.

## Known limitations

- No delegation, cell edits or execution by agents until Phase 4D. "Show cell" in run
  panels is groundwork only.
- The demo provider is rule-based. Its plans are templates, not analyses of the notebook,
  and its discussion reply only names the cell count and revision.
- While an earlier run start has an unknown outcome, a new message is still posted but its
  run cannot start until the earlier start is retried, as the alert offers.
- After a run changes state, the roadmap panel can take up to one poll interval (0.8–4 s)
  to update. On the first load of runs the page does not reload messages or the roadmap
  again, because they are already loading.
- The new-task key limit (40 characters) is enforced at bind time, not in the JSON schema
  offered to the model (which allows 64 so that existing IDs can be reused).
- `/health` reads the supervisor's private tables directly, as the Running dashboard
  already does. Its storage checks are a minimal schema read and a check that the storage
  directory still exists, not integrity checks.
- Screenshots of the Phase 4B journeys (`docs/screenshots/phase4b`) predate the 4C
  notebook UI. The agent suite regenerates them, and they were restored after the 4C runs.
