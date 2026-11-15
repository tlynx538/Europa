# Phase 4 implementation plan (4A → 4D)

Each checkpoint has its own correctness gate. A checkpoint is not considered
done until its listed tests pass together with the full Phase 3 regression
(backend, frontend unit, build, Chrome).

## 4A — Harness foundation

Files: `api/src/agents/{schema,store,providers,tools,policy,context,runtime,router}.py`,
`ApplicationStore` v7 migration and `bookkeeping()`, wiring in `main.py`.

Gate tests (`tests/test_agent_harness.py`):
* v6→v7 migration preserves records, operations, versions and triggers, and append-only still holds.
* run state machine, fencing (a stale owner cannot write), concurrency limit.
* tool argument validation and rejection of unknown tools, policy denial by role, capability isolation.
* approval digest binding, expiry, single use, mismatched digest, decision replay.
* public events paginated and persisted across restart. No secrets in events.
* restart: queued runs resume, `executing` read-only calls reconcile, an uncertain call leaves the run interrupted.
* budgets: model calls, tool calls, steps.
* cancellation in each state.
* OpenAI-compatible adapter request/response mapping (httpx MockTransport), with no network.

## 4B — Home Coordinator

Backend: workspace conversation scope, Home role, `create_project_notebook`
tool through `WorkspaceMutation`/`associate`, and an origin link message in the
new notebook's canonical conversation. Frontend: `HomeCoordinator` panel
above the Files/Running tabs on Home.

Gate: approval creates exactly the proposed resources. Denial creates nothing.
Duplicate decision submission is idempotent. A path collision fails without
overwriting. The Home conversation persists across restart. The notebook opens
from the panel. Home navigation regressions pass.

## 4C — Notebook Coordinator

Backend: notebook context (bounded cells/outputs/revision/roadmap/runs), and a
`propose_roadmap` approval bound to roadmap revision and plan digest. Frontend:
the composer starts a coordinator run after a message is persisted, with an
inline run panel and approval card in the notebook stream.

Gate: a stale roadmap revision is rejected. The approved plan is persisted as a
roadmap version. Restart keeps runs and events. A run in notebook A cannot read
notebook B. Conversation identity is preserved.

## 4D — Supervised Coding Agent

Backend: `delegate_task` (approved plan task, dependencies complete, depth
limit), the coding role, structured cell operations with markers, and
`execute_cell` via `NotebookService`. Receipt polling, a bounded repair loop,
task evidence update and a report message. Frontend: nested child run panels.

Gate: an edit is rejected on a stale revision. A user edit during agent work
causes replanning, not an overwrite. A lost execution acknowledgement is
reconciled from receipts. The repair limit holds. Cancellation does not imply
undo. One **real Gateway-backed execution** runs in a disposable notebook with a
verified durable receipt (`--run-local-gateway`), plus the full browser
acceptance journey (Home → notebook → plan → coding agent → execution → return
Home → reopen).

## Out of scope

Reviewer agent and verdicts (4E). SSE streaming, multi-provider routing UI,
production sandboxing and token accounting dashboards (4F).
