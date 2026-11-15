# Phase 4 architecture: Europa agent harness

Status: design for Phase 4A–4D on branch `feature/phase4-agentic-harness`, based on
validated `main` at `c5a8a60` (tag `phase3h-complete`). Sections marked
**Implemented** are described as built and are validated in
[phase4-validation.md](phase4-validation.md). Everything else is a proposal.
Phase 4E (independent reviewer) and 4F (broad hardening) are out of scope.

## 1. Principle: the model is not the harness

The model produces text and *proposals* for tool calls. The backend harness
decides whether a proposal is well formed, permitted, approved and executed,
and records each outcome. The browser renders persisted state and submits owner
decisions. It never runs the agent loop. A refresh, a closed tab or a lost
response does not cancel a run, and it never repeats a side effect.

```text
Browser (Home panel · notebook stream · approval cards)
   │  REST: start run / decide approval / cancel / poll events
   ▼
FastAPI  /api/agent/*
   │
   ▼
AgentRuntime (one asyncio worker pool inside the Europa control plane)
   ├── ContextManager   bounded, provenance-tagged, untrusted-content fenced
   ├── ProviderAdapter  openai-compatible · openrouter · deterministic fake/demo
   ├── ToolRegistry     typed, versioned tools with scope/capability/approval policy
   ├── PolicyEngine     role capabilities, scope checks, budgets (server-side)
   ├── ApprovalManager  digest-bound, revision-bound, expiring single-use approvals
   ├── AgentStore       runs, steps, tool calls, approvals, public events (SQLite v7)
   └── RunRecovery      fencing, restart reconciliation, no blind replay
          │
          ▼  existing, unchanged Phase 3 services
   ApplicationStore (records, roadmaps, conversations, operations)
   WorkspaceMutation + WorkspaceNotebookStore (journaled physical files)
   NotebookStore.edit_cell (revision-checked document API)
   NotebookService.submit → RuntimeSupervisor → GatewayClient → Jupyter Kernel Gateway
   Execution receipts (authoritative execution outcome)
```

## 2. What is reused (verified in the code)

| Need | Existing component | How the harness uses it |
|---|---|---|
| Durable storage | `application.sqlite3`, `ApplicationStore.transaction()` (WAL, FULL sync, BEGIN IMMEDIATE, lock order notebook→application) | Agent tables live in the same database. The schema version moves from 6 to 7 transactionally. No second database. |
| Idempotent mutations | `operations` table + `_mutate(operation_id, …)` | Every harness-made application mutation uses a deterministic operation ID derived from the tool-call ID. |
| Conversations | canonical notebook conversation; `message_append(author_role=…)` trusted server parameter | Agent replies are ordinary `message` records with `author_role` `coordinator` or `coding_agent`. REST still assigns only `user`. |
| Home conversation | none (Phase 3 has none) | v7 adds a *workspace* conversation scope to the same `records` table (see §5). |
| Projects/notebooks | `WorkspaceMutation.create_project`, `WorkspaceNotebookStore.associate(create=True)`, `bind_physical_notebook` | The Home Coordinator calls these after approval, with journaled operation IDs. Physical `.ipynb` stays canonical. |
| Plans | `roadmap_create` / `roadmap_update` with versions, revision checks, owner-only acceptance | The Notebook Coordinator proposes a plan. The approval binds the plan digest and roadmap revision. Completion still needs owner acceptance. |
| Cell edits | `NotebookStore.edit_cell` (revision-checked) | Structured operations: insert code/markdown, replace source. Stale revision means rejection and replanning. |
| Execution | `NotebookService.submit` → receipts → `RuntimeSupervisor`/`GatewayClient` | The Coding Agent never touches kernels. It submits through the service and reads the receipt. |
| UI | notebook stream, composer, roadmap side panel, Phase 3H dashboard | Additive components only: a Home Coordinator panel and inline run panels with approval cards. |

The harness adds **no** kernel manager, host Python invocation, direct `.ipynb`
writes, second conversation store or browser-owned loop.

## 3. Roles and capabilities

| Role | Context | Capabilities | Side-effecting tools |
|---|---|---|---|
| `home_coordinator` | workspace listing, projects, notebooks, Home conversation | `read:workspace`, `propose:workspace` | `create_project_notebook` (approval) |
| `notebook_coordinator` | one notebook (cells, outputs, revision), its project, roadmap, conversation, runs | `read:notebook`, `read:roadmap`, `propose:roadmap`, `delegate` | `propose_roadmap` (approval), `delegate_task` (approved plan task only) |
| `coding_agent` | one notebook + one delegated task | `read:notebook`, `edit:notebook`, `execute:notebook` | `insert_code_cell`, `insert_markdown_cell`, `replace_cell_source`, `execute_cell` (each approved) |

Capabilities are attached by the server when it creates a run of a given role.
The model cannot name, request or widen them. The Home Coordinator cannot edit
or execute cells. The Coding Agent cannot create resources or plans.

## 4. Run lifecycle

```text
queued → running → (waiting_for_approval → queued → running)* → completed | failed
            │                      │
            └→ cancelling → cancelled          interrupted (restart with unresolved uncertainty)
```

A run is one agent turn for one role. It is triggered by a persisted user
message, or by delegation from a parent run. Parent and child are linked through
`parent_run_id`. A run that waits for approval holds no worker, lock or model
connection. When the owner decides, the run is re-queued and resumes from its
persisted steps.

## 5. Home conversation scope

Phase 3 records allow conversations scoped to a project or to one notebook.
Phase 4B needs a Home thread that belongs to neither. Two options were
considered:

1. Keep Home turns only inside agent tables. This is additive but creates a
   second conversation store, which the handover prohibits.
2. **Chosen:** extend `records` so that `conversation`/`message` may have no
   project and no notebook. A `workspace_conversations` table registers which
   conversations are Home threads. The rebuild follows the tested v1
   table-rebuild pattern. It preserves every row, ID, sequence, trigger, index
   and operation, and runs in one transaction.

Like any schema step, v7 is forward-only: an older Europa build refuses an
unknown version rather than guessing. Back up `application.sqlite3` while Europa
is stopped before upgrading owner data.

## 6. Trust boundaries

* Notebook cells, outputs, file names, roadmap text, conversation text, tool
  results and model output are **untrusted**. Context sections are fenced and
  labelled with provenance. Instructions inside them never alter policy, because
  policy lives in Python and not in the prompt.
* Provider credentials come from environment variables only. They are never
  stored, logged, returned or placed in context.
* Public events contain summaries, tool names, targets, previews and outcomes.
  Hidden reasoning is never requested, and provider reasoning fields are
  discarded.
* Execution remains Europa's trusted single-owner model. The local provider is
  not a sandbox, and runc is not equivalent to gVisor. Every code execution
  requires an explicit owner approval of the exact source and revision.
