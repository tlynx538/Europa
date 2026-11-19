# Phase 4D — Supervised Coding Agent, kickoff and progress (PR A)

Status: implemented on `feature/phase4d-coding-agent` (base `main` `c83c683`, Phase 4C
merged). Stages 1–3 of the owner brief
[agent-prompts/phase4d-coding-agent.md](agent-prompts/phase4d-coding-agent.md). Validation
evidence is in [phase4d-validation.md](phase4d-validation.md). Stage 4 (project kernel
environments and the package installer) is PR B and is not part of this branch.

## What is built

With agents enabled:

1. **Kickoff.** When the Home Coordinator creates a notebook, the harness starts a Notebook
   Coordinator *kickoff run* in the new notebook. Opening the notebook shows numbered
   questions marked **Needs your input**, then (for a project notebook) a draft roadmap
   approval card. Answers go through the normal composer and may produce a revised plan.
2. **Assignment.** The owner assigns a ready roadmap task with **Assign to Coding Agent**, or
   approves a Coordinator-proposed assignment.
3. **Supervised coding.** The Coding Agent proposes a cell (approval), proposes running it
   (approval), reads the receipt, repairs within a rule and budget, and reports with output
   quoted only from the receipt.
4. **Progress.** The roadmap shows the agent's live status per task. A task is
   **auto-completed** (attributed to `coding_agent`) only on verified success; failures block
   it; the owner can reopen.

With agents disabled (the default) the notebook and Home UI are unchanged.

## Roles and capabilities

| Role | Capabilities | Tools |
|---|---|---|
| `home_coordinator` | `read:workspace`, `propose:workspace`, `ask:owner` | `list_workspace`, `list_projects`, `list_notebooks`, `read_notebook_summary`, `create_project_notebook`, `ask_owner` |
| `notebook_coordinator` | `read:notebook`, `read:roadmap`, `propose:roadmap`, `ask:owner`, `delegate` | `read_notebook`, `read_roadmap`, `read_execution`, `propose_roadmap`, `ask_owner`, `delegate_task` |
| `coding_agent` | `read:notebook`, `edit:notebook`, `execute:notebook` | `read_notebook`, `read_execution`, `insert_code_cell`, `insert_markdown_cell`, `replace_cell_source`, `execute_cell` |

**Contract change from 4C:** `delegate_task` and the `delegate` capability are restored to
`notebook_coordinator`, and `delegate_task` now **requires approval**. The 4C tests that
asserted delegation was absent now assert it is approval-gated (see the validation report).
The Coordinator still never edits cells or runs code itself. Coding Agent runs always have
depth 1, so policy keeps them unable to delegate.

## Stage 1 — kickoff questionnaire

- **`ask_owner`** (`api/src/agents/tools.py`): `{questions: 1–6 strings of 1–500 characters,
  preamble?: ≤1000}`. No approval. Posts one `coordinator` message with
  `state='needs_input'`, content = preamble plus a numbered list, operation ID
  `agent-<tool call id>` (recovery replays return the committed message). The harness
  enforces **at most one call per run** (`Tool.per_run`); a second call is rejected as
  `policy`.
- **Kickoff start** (`AgentRuntime.start_kickoff`, internal only). `create_project_notebook`
  results now include `origin_message_id`. The harness starts a kickoff run for a create call
  that is `succeeded` **and** has `origin_message_id`:
  - right after the call settles in `_execute`;
  - after restart reconciliation settles it;
  - in a restart sweep over already-succeeded creates (a crash after `succeeded` but before
    the kickoff leaves no other trace).
  The operation ID `agent-kickoff-<create call id>` makes it exactly-once. Notebooks created
  before 4D have no `origin_message_id` and never get a kickoff after an upgrade. A kickoff
  start failure records a `kickoff_failed` event and does not fail the Home run.
- The kickoff run is a `notebook_coordinator` run with `trigger_message_id` = the
  coordinator-authored origin message (REST `POST /api/agent/runs` still rejects such a
  trigger with 422) and `task = {kickoff: true, purpose}`. Run views expose only
  `task: {kickoff: true}`; the purpose stays internal.
- **Context and prompt.** Trigger text "Kickoff for a new notebook. Owner's purpose: …". A
  trusted kickoff section is added to the system prompt: call `ask_owner` first, then
  `propose_roadmap` if the notebook is in a project, then one short sentence. Conversation
  history marks `needs_input` messages. The `assigned-task` context fence is now limited to
  `coding_agent` runs.
- **UI.** Coordinator `needs_input` messages show a **Needs your input** badge (owner
  messages render as before). The composer placeholder becomes "Answer the Coordinator's
  questions…" while a question is open. After Home's **Open notebook**, the view scrolls to
  the questions and focuses the composer once (`web/src/kickoffFocus.ts`), unless a cell
  editor has focus. The kickoff run panel renders after the questions.

## Stage 2 — supervised Coding Agent

### Assignment paths

- **Owner: `POST /api/agent/tasks/assign`** `{operation_id, roadmap_id, task_id,
  instructions?}` → 201 with the run view. An exact replay returns the same run. Validation
  (`AgentRuntime.assignable`, shared with delegation): the notebook is in a project; the
  roadmap is this notebook's current roadmap; the task exists and is `pending`, `blocked` or
  `needs_input`; all dependencies are `completed`; no other Coding Agent run is active in the
  notebook. Failures return 409 (conflict) or 422. Creation is serialized so two requests
  cannot both pass the one-active-run rule. 503 when agents are disabled.
- **Run model (design choice).** An owner assignment posts one `system/tool` message in the
  notebook conversation — `Owner assigned "<title>" to the Coding Agent.` — and creates a
  **top-level** `coding_agent` run (`parent_run_id = null`, depth 1) triggered by that message,
  with `task.assigned_by = 'user'`. Its panel renders after that message. Because the author is
  not `user`, it does not count as an answer to open kickoff questions.
- **Coordinator: `delegate_task`** (approval required). The binding pins `{roadmap_id,
  roadmap_revision, task_id}`; the preview is "Assign '<title>' to the Coding Agent" with the
  criteria and instructions. On approval the same validation runs again; a changed roadmap
  revision is a `conflict`. The child run has `parent_run_id` = the Coordinator run and
  `assigned_by = 'coordinator'`; the 4C sibling rendering keeps its approval cards visible.
- **Roadmap panel.** Each task has **Assign to Coding Agent**, disabled with a tooltip naming
  the reason (another notebook's roadmap, status, unfinished dependencies, an active run).

### Execution hardening

- **Deterministic execution ID.** `NotebookStore.begin(notebook, cell, revision,
  execution_id=None)` accepts a caller-supplied 32-hex ID and refuses a known ID with 409
  "Execution already accepted" inside the store lock. `NotebookService.submit(...,
  execution_id=None)` passes it through; `None` keeps the previous behaviour. `execute_cell`
  uses its tool-call ID. Before submitting it binds an existing receipt with that ID; after a
  failed or lost submit it reads the receipt by ID — present means bind it, absent means
  nothing ran. Restart reconciliation reads the receipt by ID; the time-window scan is gone.
- **Own work only.** `execute_cell` binds only a cell that this run inserted (cell marker
  `europa_agent.run_id`) or replaced, whose current source hash equals the hash recorded by
  the run's latest succeeded edit of it. Otherwise: `policy` "The Coding Agent can only run
  cells it wrote in this task."
- **Repair rule.** After an `applied` receipt with `status=error`, the next supervised action
  must be `replace_cell_source` on that cell (then `execute_cell` of it) or an
  `insert_markdown_cell` note; anything else is `policy`. The budget stays initial attempt +
  2 repairs. Another replacement of the failed cell before re-running it is allowed.
- **Approve guards.** For insert/replace/execute cards, **Approve** is disabled while the
  open notebook has unsaved drafts ("Save or discard your edits first") or while its loaded
  revision differs from the card's bound revision (see Re-propose below). The disabled
  button has a dashed border, neutral background, muted text and `cursor: not-allowed`; the
  reason is next to it and linked with `aria-describedby`. A global `button:hover` rule that
  hid the Approve label on hover is overridden in `agent.css`.
- **Report.** The harness appends an "Execution record" to the Coding Agent's reply, built
  only from the receipt: receipt short ID, state, status and bounded observed output — or
  that nothing ran and why (denied, conflict, uncertain). Links to the receipt, cell and task
  are kept.

### Re-propose for stale cards (owner-requested deviation)

When the notebook changed after an insert/replace/execute card was proposed, the card shows
**"Notebook changed since this was proposed."** with **Re-propose** and **Deny** (Approve
disabled). `POST /api/agent/approvals/{id}` accepts `decision: 'repropose'` with the usual
digest and operation-ID rules. The server checks that the bound revision differs from the
notebook's current revision (otherwise 409, nothing changes). The approval is recorded as a
**denial with reason `stale_revision`** (tool-call result, decision event and approval view).
The model receives status `conflict`: "The notebook changed since this was proposed.
Re-read it and propose again." The run **continues**. This does not count as a repair or
as an owner denial for progress. The demo Coding Agent re-reads and re-proposes once; a
second stale conflict gets an honest report.

### Demo provider

`EUROPA_AGENT_PROVIDER=demo` remains rule-based, not an LLM. New branches:

- **Kickoff:** `ask_owner` with five questions naming the purpose's subject → draft
  `propose_roadmap` (Markov tasks when the purpose mentions Markov, generic tasks otherwise)
  or, standalone, a sentence that planning needs a project → one closing sentence.
- **Answers:** when the latest coordinator question is still open, an owner message (not a
  status request) produces a revision bound to the current roadmap that appends a task
  titled from the answer, or a fresh draft if none was saved.
- **Coding Agent:** inserts task-specific pure-Python Markov code (matrix with printed
  `row sum = 1.0`; `random.seed(7)` simulation with visit frequencies; power iteration;
  convergence gaps per step), runs it, repairs once after an error receipt, and reports.

## Stage 3 — progress tracking and auto-completion

`api/src/agents/progress.py` applies the task table from every run outcome path
(completion, failure and budget exhaustion, every cancel path, restart `interrupted`):

| Event | Task change | Operation ID |
|---|---|---|
| Assignment accepted | `in_progress`, explanation cleared | `agent-start-<run>` |
| Verified success | `completed`, `accepted=true`, `accepted_by='coding_agent'`, evidence `{summary, execution_receipt_id \| cell_ids, message_id}`, `links.cell_id` = primary cell; plus a `task_completed` activity (`actor='coding_agent'`) | `agent-complete-<run>`, `agent-complete-activity-<run>` |
| Completed but not verified | stays `in_progress`; the report is added as evidence with an explanation | `agent-unverified-<run>` |
| Failed, budget exhausted, interrupted | `blocked`, explanation naming the reason | `agent-blocked-<run>` |
| Cancelled, or every proposal denied | `pending` if no evidence was added, else `in_progress` with an explanation | `agent-reset-<run>` |

- **Verified success.** The run `completed`. Code: every code cell the run wrote has a
  latest receipt, read from `NotebookStore`, that is `applied` with `status=ok`, matches its
  latest edit, and the cell still holds that source. Notes only (Markdown edits, no code
  edits): every proposed edit succeeded (re-propose denials do not count). A code edit without
  a successful execution never completes.
- **Trusted path (Phase 3 contract change).** `ApplicationStore.roadmap_update(...,
  task_only=True, trusted_acceptor='coding_agent')` records `accepted_by='coding_agent'`.
  REST routes never pass `trusted_acceptor`, so REST still forces `accepted_by='user'`.
  `_validate_plan` keeps its rules (dependencies completed, children finished, receipt
  evidence applied and successful for the linked cell). Evidence `cell_ids` (notes-only) are
  accepted only on the trusted path and only for existing cells. `accepted_by='coding_agent'`
  survives later plan edits while the task and its evidence are unchanged.
- **Owner changes are never overwritten.** After each harness write, the task's fingerprint
  (status, explanation, accepted, evidence, links) is stored in a `task_progress` run event.
  Before the next write, a mismatch records `task_update_skipped` and leaves the task alone.
  Stale roadmap revisions are retried up to 4 times by re-reading. Runs the harness did not
  start never touch tasks.
- **UI.** Each task shows "Coding Agent working · <state>", "Waiting for your approval" with
  **Show approval** (scrolls to and focuses the card), or "Blocked: <reason>". Completed tasks
  show ✓ with the tooltip "Completed by Coding Agent — verified by receipt <id>" (or "…by
  notes <cells>") and a **Reopen** action that opens `TaskEditor` prefilled with
  `in_progress`. Decisions, Stop and run-state changes reload the roadmap, so progress % moves
  within one poll cycle.

## Deviations from the brief

1. **Open-questions rule.** The placeholder and the demo's answer detection treat questions
   as open until the owner posts a message after them, not "while the latest Coordinator
   message needs input": the kickoff's own one-sentence closing reply would otherwise end the
   state immediately.
2. **Browser sentence.** The new kickoff journey uses "I want to understand a simple Markov
   chain model." The Phase 4B journey already creates the "Markov Chain" project from the
   exact PRD sentence in the same disposable workspace, and there is no API to delete it; that
   4B journey now also asserts the kickoff badge, first question and draft card for the exact
   sentence.
3. **Folder name.** Agent browser specs live in `web/browser-agent/` (existing directory), not
   `web/browser-agents/`.
4. **Re-propose** replaces the brief's "Notebook changed — the agent will re-propose" (owner
   decision; see above).
5. **Owner-assignment model:** the brief allowed a lightweight parent run or a top-level run;
   the top-level run triggered by a `system/tool` message was chosen.
6. **4C assertion removed.** `'delegate_task' not in system` (prompt) could not stay true once
   the prompt names the tool; it is replaced by a positive check of the new prompt sentence.
7. **Unverified completion** keeps the task `in_progress` with the report as evidence and an
   explanation (`agent-unverified-<run>`); the brief did not specify this row.

## Known limitations

- The kickoff restart sweep reads all succeeded `create_project_notebook` calls on every
  restart; its cost grows with Home history.
- The "cancelled with evidence added → `in_progress`" branch is implemented but has no
  dedicated test: the harness only adds evidence when a run finishes.
- The roadmap reloads on run-state changes and after the owner's own decisions or Stop; a
  cancel issued from another tab can show a stale task status until the next change.
- In the Gateway "unknown outcome" test, the outcome is produced by cancelling the service's
  execution task mid-run, not by killing the server process.
- The demo provider's plans and code are templates, not analyses of the notebook.
- No package installation and no project kernel environments yet (PR B): numpy and pandas
  are unavailable to agent code.
