# Phase 4 harness design

Companion to [phase4-architecture.md](phase4-architecture.md). This document
defines the durable contracts. Code lives in `api/src/agents/`.

## 1. Storage (application schema v7)

Migration v6→v7 runs in one transaction inside `ApplicationStore.__init__`:

1. Rebuild `records` with one extra CHECK branch:
   `kind IN ('conversation','message') AND project_id IS NULL AND notebook_id IS NULL`
   (workspace scope). Then recreate indexes and append-only triggers, and
   verify `foreign_key_check` before commit.
2. `workspace_conversations(conversation_id PK → records, created_at)` registers
   Home threads. Notebook-canonical and project conversations are unchanged.
3. Agent tables:

| Table | Key columns | Notes |
|---|---|---|
| `agent_runs` | `id`, `seq`, `role`, `state`, `conversation_id`, `trigger_message_id`, `notebook_id`, `project_id`, `parent_run_id`, `depth`, `task` (JSON roadmap/task ref), `operation_id UNIQUE`, `fingerprint`, `provider`, `model`, `budget`, `usage`, `owner`, `fence`, `cancel_requested`, `error`, timestamps | `state` CHECK restricted to the eight lifecycle states. |
| `agent_steps` | `(run_id, idx)` | Private transcript (assistant messages and tool results) used to resume a run. Never served raw. |
| `agent_tool_calls` | `id`, `run_id`, `name`, `version`, `arguments`, `digest`, `state`, `binding` (JSON revision/hash/target), `operation_id UNIQUE`, `result` | States: `rejected`, `awaiting_approval`, `approved`, `denied`, `expired`, `executing`, `succeeded`, `failed`, `uncertain`, `cancelled`. |
| `agent_approvals` | `id`, `run_id`, `tool_call_id UNIQUE`, `digest`, `preview`, `notebook_id`, `project_id`, `revision`, `state`, `expires_at`, `decided_at`, `decision_operation_id` | Single-use. States: `pending`, `approved`, `denied`, `expired`, `invalidated`. |
| `agent_events` | `seq AUTOINCREMENT`, `run_id`, `type`, `data`, `created_at` | Public, append-only. A trigger rejects UPDATE and DELETE. |

Agent bookkeeping uses `ApplicationStore.bookkeeping()`. This is the same
locks and `BEGIN IMMEDIATE`, but it skips the catalog rescan, which only
matters for notebook-identity writes.

## 2. Run ownership and fencing

The runtime has an `owner` token (a fresh UUID per control-plane process).
Claiming a run is `UPDATE agent_runs SET state='running', owner=?,
fence=fence+1 WHERE id=? AND state='queued'`. Every later write for that run
includes `AND owner=? AND fence=?`. Zero affected rows means ownership was lost,
and the worker stops without writing. The Phase 3 storage owner lock already
guarantees a single control plane per application root. Fencing also protects
against overlapping tasks inside one process.

## 3. Loop

```
claim → load steps → assemble context → model call (budgeted)
  → no tool calls: persist reply message → completed
  → tool calls: for each, validate schema → policy (role capability, scope)
       read-only        → execute → persist result → continue loop
       needs approval   → persist tool call + approval (digest, binding, expiry)
                          → state waiting_for_approval → release worker
       not permitted    → persist rejected result → continue (model sees the refusal)
```

On approval, the run is re-queued. The worker resumes, finds the approved tool
call and executes **the stored arguments**. It never regenerates them. It then
appends the result step and continues the loop. A denial appends a "denied by
owner" result so the model can respond or stop.

## 4. Approvals

`digest = sha256(canonical JSON of {tool, version, arguments, binding, run_id})`.
`binding` holds the target identity captured by the server at proposal time.
That is the notebook ID plus document revision (and source hash for a replace),
the roadmap ID plus revision, or the workspace path plus root identity. The owner
POSTs `{operation_id, decision, digest}`. A mismatched digest, an expired or
already decided approval, or a cancelled run is refused with 409. At execution,
the binding is re-verified against current state. A stale revision fails the
tool with `conflict` and never overwrites. Expiry is 30 minutes by default.
An approval authorizes exactly one tool call.

## 5. Side effects and recovery

Before a side effect, the tool call moves to `executing` with a deterministic
`operation_id = "agent-" + tool_call_id`. Reconciliation per tool after a crash
in `executing`:

| Tool | Evidence used | Outcome |
|---|---|---|
| `create_project_notebook` | journaled workspace operations replayed with the *same* operation IDs (their own exact-replay contract) | succeeded / failed / uncertain (never a second mkdir) |
| `propose_roadmap` | application `operations` row for the operation ID | replay returns the committed record, or nothing committed → retry is safe |
| `insert_*_cell` | cell metadata marker `europa_agent.tool_call_id` written atomically with the cell | marker present → succeeded. Absent at the bound revision → not applied. Otherwise conflict. |
| `replace_cell_source` | source hash of the target cell versus the bound and proposed hashes | proposed → succeeded. Original → not applied. Other → conflict. |
| `execute_cell` | receipt ID persisted immediately after `NotebookService.submit` accepts. If missing, receipts for the cell created after `started_at`. | exactly one → bind and read state. Otherwise `uncertain`, which is surfaced and **never re-run**. |

At startup, `RunRecovery` reconciles each `executing` call. A run whose calls all
reconcile deterministically is re-queued with a `run_recovered` event. Any
`uncertain` call leaves the run `interrupted`. Runs that were `queued` or
`waiting_for_approval` are left as they are.

## 6. Budgets (defaults; per-role overrides)

| Limit | Default |
|---|---|
| model requests per run | 8 |
| tool calls per run | 16 |
| loop steps per run | 24 |
| wall-clock runtime per run | 600 s (excluding time waiting for approval) |
| max output tokens per model call | 2048 |
| context budget | ~24,000 characters assembled context |
| concurrent running runs | 2 |
| delegation depth | 1 (coordinator → coding agent) |
| failed execution repairs (coding agent) | 2 |

Exhausting a budget fails the run with a public `budget_exhausted` event. It
never truncates silently.

## 7. Cancellation

`POST /api/agent/runs/{id}/cancel` sets `cancel_requested`. A queued run or one
waiting for approval becomes `cancelled` at once, and its pending approvals
become `invalidated`. A running run moves to `cancelling`. The worker stops
before the next model call or tool, and an in-flight tool is allowed to settle
first. Cancellation never interrupts Python. The existing receipt interrupt
endpoint remains the only interrupt, and committed edits or executions are
reported as already applied.

## 8. Public API (`/api/agent`)

| Method/path | Contract |
|---|---|
| `GET /agent/status` | `{enabled, provider, model, reason}`. Never returns secrets. |
| `POST /agent/runs` | `{operation_id, conversation_id, message_id}`. The role is inferred from the conversation scope (Home → `home_coordinator`, notebook canonical → `notebook_coordinator`). 201 run. Replaying the exact request returns the same run. |
| `GET /agent/runs?conversation_id=` | runs for a conversation, including children |
| `GET /agent/runs/{id}` | run view: state, role, budgets/usage, pending approval preview |
| `GET /agent/runs/{id}/events?after=` | public events, paginated by sequence |
| `POST /agent/runs/{id}/cancel` | `{operation_id}` |
| `POST /agent/approvals/{id}` | `{operation_id, decision: approve|deny, digest}` |
| `GET /agent/home/conversation` | ensure/get the active Home conversation |
| `POST /agent/home/conversations` | `{operation_id}` start a new Home thread |

The REST layer cannot choose a role, capability, author or budget.
