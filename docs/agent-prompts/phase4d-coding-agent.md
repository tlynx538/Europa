# PRD: Europa Phase 4D — Supervised Coding Agent, kickoff questionnaire, progress tracking, project kernel environments

Owner brief, saved as given (§10). Owner decisions taken during implementation are appended
at the end; they amend this brief where stated. Implementation and validation:
[phase4d-coding-agent.md](../phase4d-coding-agent.md) and
[phase4d-validation.md](../phase4d-validation.md).

**Audience:** an autonomous OpenCode worker implementing this in `tlynx538/Europa`.
**Scope:** Phase 4D only. **No** Reviewer agent or verdicts (4E), and **no** broad hardening or SSE (4F).

---

## 0. Preconditions — stop and report if any fail

1. **PR #7 (Phase 4C) is merged.** `origin/main` contains `27b6d36` (4C head). Do not start before this.
2. **Never work in `~/experimental/Europa` while it is on `feature/phase4-agentic-harness`.** That checkout holds the *OpenAI agent's* older, incomplete 4D line: `/api/agents/*`, `AgentActivity.tsx`, an uncommitted `api/src/agents/coding.py`, `agent_calls`/`agent_handoffs` tables, and a `needs_reconciliation` state.
   - It is a **different architecture** from `main` and must **not** be merged, rebased or copied wholesale.
   - The owner archives it first: `git switch -c archive/phase4d-openai-wip && git add -A && git commit -m "Archive OpenAI 4D WIP (reference only)"`, optionally pushed.
   - You may **read** it for the ideas listed in §3. Never apply it as a patch.
3. Create a fresh worktree from `origin/main`. Make two branches, two PRs:
   - **PR A** — `feature/phase4d-coding-agent` (Stages 1–3) → base `main`
   - **PR B** — `feature/phase4d-project-envs` (Stage 4) → based on PR A's branch, opened as a stacked PR. Retarget it to `main` after PR A merges.
4. Record the baseline on merged `main` before changing anything. The expected numbers, from the 4C run:

| Suite | Expected |
|---|---|
| Python with `--run-local-gateway` | 412 passed, 79 skipped |
| Python without Gateway | 386 passed, 105 skipped |
| `npm test` | 115 passed |
| `npm run build` | passes |
| Phase 3 Chrome | 56 passed, 1 skipped |
| Agent browser suite | 6 passed |

Never commit, merge to `main` or tag without the owner. Never force-push. Never weaken a test to make it pass.

---

## 1. Read first

1. `AGENTS.md`, `README.md`
2. `docs/phase4-architecture.md`, `docs/phase4-harness-design.md`, `docs/phase4b-home-coordinator.md`, `docs/phase4c-notebook-coordinator.md`, `docs/phase4c-validation.md`, `docs/agent-prompts/phase4c-notebook-coordinator.md`
3. `docs/conversational-notebooks.md`: roadmap/task contract, owner acceptance, evidence rules, message `state`
4. `docs/notebook-agent-interaction.md`: inline turns, progress panel, receipts as the only proof of execution
5. Runtime: `docs/runtime-provider.md`, `docs/runtime-lifecycle.md`, `api/src/runtime/local.py`, `api/src/runtime/supervisor.py`, `api/src/notebooks/service.py`, `api/src/notebooks/store.py`
6. Harness: `api/src/agents/` (all files), `api/src/application/store.py` (`_validate_plan`, `roadmap_update`, `message_append`)
7. UI: `web/src/AgentRun.tsx`, `web/src/agentApi.ts`, `web/src/Workspace.tsx`, `web/src/HomeCoordinator.tsx`, `web/src/TaskEditor.tsx`, `web/src/main.tsx`

---

## 2. What already exists on `main` (reuse; do not rebuild)

- **Coding tools** in `api/src/agents/tools.py`: `insert_code_cell`, `insert_markdown_cell`, `replace_cell_source` and `execute_cell`, each with `bind`/`preview`/`reconcile`, plus the `europa_agent.tool_call_id` cell marker.
- **The `coding_agent` role** in `policy.py`.
- **`AgentRuntime.delegate()`:** checks dependencies are completed; allows one active Coding Agent per notebook; enforces depth 1 (`MAX_DELEGATION_DEPTH`); sets the task to `in_progress`; starts a child run with `parent_run_id` and a `task` brief.
- **The coding run's end** (`_complete`): it posts a `coding_agent` report message linked to the receipt and task, and appends task evidence. A failed run marks the task `blocked` with an explanation.
- **Repair counter:** `usage.repairs`. After `Budget.repairs` failed executions, supervised actions are refused.
- **UI from 4C:** `AgentRunPanel` renders child runs as **siblings**, so a collapsed parent never hides a child's approval card; the roadmap approval preview; the Ask Coordinator toggle; roadmap and messages reload whenever a run changes.
- **Delegation is disabled for `notebook_coordinator`.** 4C removed `delegate_task` and `delegate` on purpose. Stage 2 re-enables delegation in the controlled form described below.
- **`execute_cell`** finds receipts by cell and time after a lost acknowledgement. Stage 2 replaces that.

---

## 3. Owner decisions (fixed — implement as stated)

| Topic | Decision |
|---|---|
| Kickoff after Home creates a notebook | The Coordinator immediately **asks questions** (marked *needs input*) **and drafts a roadmap** for approval. |
| Task completion | **Auto-complete** when the Coding Agent's work is *verified* (§6). The owner can reopen. Attributed to `coding_agent`, never to `user`. |
| Package installation | **One kernel environment per project**, separate from Europa's own `.venv`. Standalone notebooks share one workspace-default environment. |
| Delegation | The owner's **"Assign to Coding Agent"** button is the primary path. The Coordinator may *propose* an assignment, but only through an approval card. |
| Calculation-only AST sandbox (from the OpenAI line) | **Not adopted.** Per-execution owner approval plus receipts is the safety model. |

Ideas to port from the OpenAI line, re-implemented on `main`'s architecture: a caller-supplied execution ID, with replay refused in `NotebookStore.begin`; execution only of the agent's own latest edit, chained by source hash; a repair must replace the cell that failed; Approve disabled while drafts exist; Gateway tests for cancel, lost acknowledgement and unknown outcome.

**Not ported:** `needs_reconciliation` and the owner reconcile endpoint (deferred to 4F); `agent_approved` roadmap gating; child budgets carved from the parent.

---

## 4. Stage 1 — Kickoff questionnaire (PR A)

### Behaviour
When `create_project_notebook` succeeds (Home), the harness starts a **kickoff run** in the new notebook's canonical conversation. When the owner clicks **Open notebook**, they land in the notebook and see, inline after the origin message:

1. A Coordinator message with **3–5 numbered questions**: goal and success criteria, data source, expected outputs, constraints/time, prior knowledge. Its `state` is `needs_input`, and it is visibly marked "Needs your input". The questions are specific to the Home purpose; there are no generic boilerplate-only questions.
2. **If the notebook is in a project:** a **draft roadmap approval card** (`propose_roadmap`) built from the Home purpose. The text says it is a draft that answers can refine. If the notebook is standalone, there are questions only, plus a note that planning needs a project.
3. Owner answers go into the normal composer. They trigger a normal Coordinator run, which may revise the plan (another approval). If the owner approves the draft before answering, that is fine too.

### Backend
- **New tool `ask_owner`** (all coordinator roles, no approval, side effect = one message). Arguments: `{questions: list[str] (1–6, each ≤500 chars), preamble?: str ≤1000}`. It posts one message, authored by `coordinator` with `state='needs_input'`, using operation ID `agent-<tool_call_id>`. The content is the numbered list. It is idempotent on recovery: replaying the operation ID returns the committed message. Add it to the `notebook_coordinator` and `home_coordinator` tool lists. The prompt says: use it when information is missing; at most once per run.
- **`AgentRuntime.start_kickoff(notebook_id, conversation_id, origin_message_id, purpose)`:** an internal trusted path, **not** exposed over REST. It creates a `notebook_coordinator` run with `trigger_message_id = origin_message_id` (coordinator-authored, which REST `start_run` would reject) and `task = {'kickoff': True, 'purpose': …}`. Operation ID: `agent-kickoff-<create tool_call_id>`, exactly once per created notebook. Replay is a no-op. It is called by the harness **after** `create_project_notebook` reaches `succeeded`, including from restart reconciliation, never from inside the tool. A crash between the two steps is recovered by the deterministic operation ID.
- **Kickoff context:** the trigger text is "Kickoff for a new notebook. Owner's purpose: <purpose>". The prompt instructs: call `ask_owner` first, then `propose_roadmap` if a project exists, then reply with one short sentence.
- **Demo provider:** a kickoff branch doing `ask_owner` → `propose_roadmap`. Purpose text mentioning Markov uses the Markov tasks; anything else uses the generic tasks.
- **Disable kickoff** when the Home run was cancelled before the creation succeeded. Kickoff is not part of the Home approval, so its own proposals still need approval.

### UI
- `needs_input` Coordinator messages get a visible "Needs your input" badge. The composer placeholder becomes "Answer the Coordinator's questions…" while the latest Coordinator message needs input.
- After **Open notebook** from Home, scroll to the first `needs_input` message and focus the composer. Do not steal focus if the owner is already editing a cell.
- The kickoff run panel and approval card render with the existing components.

### Tests
- **Backend:** a Home create of a project notebook starts exactly one kickoff run; a `needs_input` message is created with 3–5 questions, plus a pending draft roadmap approval; a standalone notebook gets questions only; a restart between the create and the kickoff starts the kickoff exactly once; a denied create starts no kickoff; REST cannot start a kickoff (a coordinator-authored trigger returns 422); `ask_owner` replay posts no duplicate.
- **Browser** (agent suite): Home "I want to understand a simple Markov chain." → approve → **Open notebook** → questions are visible with the badge, and the draft plan card is visible. Answer one question → a new run → a revised plan card bound to the right roadmap revision.

---

## 5. Stage 2 — Supervised Coding Agent (PR A)

### 5.1 Assignment paths
- **Owner "Assign to Coding Agent" button** on each roadmap task in the left Roadmap panel. It is enabled only when all of these hold: agents are enabled; the notebook is in that project; the task is `pending`, `blocked` or `needs_input`; all dependencies are `completed`; no other Coding Agent run is active in the notebook. Otherwise it is disabled, with a tooltip saying why. It calls new `POST /api/agent/tasks/assign` with `{operation_id, roadmap_id, task_id, instructions?}`. The server validates exactly the rules in `runtime.delegate()`, then creates the coding run. The parent is a new lightweight `notebook_coordinator` run record that starts the child, or the child is top-level with `parent_run_id = null` and a recorded `assigned_by='user'`. Choose one, document it, and keep run listing and UI nesting coherent. It returns the run, and replay returns the same run.
- **Coordinator-proposed assignment.** Re-add `delegate_task` to `notebook_coordinator`, but make it **approval-required**: preview "Assign '<task title>' to the Coding Agent", binding `{roadmap_id, roadmap_revision, task_id}`. Executing it calls `runtime.delegate()` and re-checks dependencies and the active-run rule. Restore `delegate` to the role's capabilities. Update the 4C tests that asserted delegation was absent, so they now assert it is approval-gated. This is an intended contract change; note it in the docs.

### 5.2 Execution hardening (ported ideas)
- **Deterministic execution ID.** `NotebookStore.begin(..., execution_id=None)` accepts a caller-supplied 32-hex ID. If a receipt with that ID already exists, it raises 409 ("execution already accepted"), so execution is never repeated. `NotebookService.submit(..., execution_id=None)` passes it through. `execute_cell` uses `execution_id = tool_call_id`, which is already a 32-hex UUID. Lost acknowledgement or restart reconciliation reads the receipt **by that ID**. If it exists, bind it; if not, nothing ran. Remove the time-window receipt scan.
- **Execute only the agent's own work.** `execute_cell.bind` requires the target cell to carry `metadata.europa_agent.run_id == this run`, or to be the cell of this run's latest succeeded `replace_cell_source`. The cell's current source hash must equal the hash recorded by that edit. Otherwise the call is rejected with `policy`: "The Coding Agent can only run cells it wrote in this task."
- **Repair rule.** After an execution whose receipt is `applied` with `status=error`, the next supervised action must be `replace_cell_source` on **that same cell** (followed by `execute_cell`), or an `insert_markdown_cell` note. Anything else is rejected with `policy`. Keep the existing repair budget (initial attempt + 2 repairs).
- **Approve disabled while drafts exist.** In the notebook UI, an approval card for `insert_*`, `replace_cell_source` or `execute_cell` disables **Approve** while the open notebook has unsaved drafts, or while its loaded revision differs from the card's bound `notebook_revision`. It shows "Save or discard your edits first", or "Notebook changed — the agent will re-propose". Deny stays enabled.
- **Report.** The report message keeps its receipt, cell and task links. It includes the bounded observed output, and says plainly when nothing ran (denied, conflict, uncertain).

### 5.3 Demo provider
Port the deterministic coding flow, pure Python (no numpy unless Stage 4 is present): `insert_code_cell` with task-specific Markov code (matrix: a 2-state matrix with row sums; simulate: `random.seed(7)` plus visit frequencies; stationary: power iteration; convergence: a text gap per step); `execute_cell` on the inserted cell; the report. On an error receipt, propose one `replace_cell_source` repair, then report.

### Tests (backend: `tests/test_agent_coding.py`)
Use `Harness`, plus a `FakeService` that creates real receipts through `NotebookStore.begin/finish` without a kernel, plus a role-dispatching fake script.
1. Full flow: assign → insert approval (nothing inserted before) → marker on the cell → execute approval (modification is not execution) → receipt applied → report message links → task evidence.
2. Dependencies: a dependency not completed is refused, and so is a second active coder.
3. Coordinator `delegate_task` needs approval. Denial creates no child.
4. The Coding Agent cannot delegate, plan or create resources.
5. Owner edit between proposal and approval: conflict, never an overwrite (insert and replace).
6. Execute a non-agent cell: rejected with `policy`.
7. Deterministic execution ID: replaying `begin` with the same ID returns 409, so it never runs twice.
8. Lost acknowledgement (the service raises after `begin`): reconciled by ID; exactly one receipt.
9. Restart while executing: a finished receipt is bound; a `running` receipt is `uncertain` and the run `interrupted`; no re-run in either case.
10. Repair rule: a non-repair action after an error is rejected; the repair budget is enforced.
11. Cancel after a committed insert: the cell stays, and the event says it was not undone.
12. Opt-in real Gateway, driving the full app lifespan with `DemoProvider`: Home → notebook → kickoff → approve plan → assign task 1 → approve insert → approve run; a real `applied` receipt whose output contains `row sum = 1.0`; the task auto-completes (Stage 3); restart: everything persists, nothing re-runs.
13. Gateway scenarios (integration): cancel during a long run, a lost acknowledgement, and an unknown outcome. Each settles from the receipt and is never replayed.

---

## 6. Stage 3 — Progress tracking and auto-completion (PR A)

### Defect being fixed
After the agent finished writing code or notes, the roadmap did not reliably reflect it: the % rarely moved and the "in progress" state was inconsistent.

| Event | Task change (server, durable) | Visible in the UI without reload |
|---|---|---|
| Assignment accepted | `in_progress`, explanation cleared | Task shows "Coding Agent working · <run state>" |
| Run waiting for approval | unchanged | Task shows "Waiting for your approval" with a jump to the card |
| **Verified success** | `completed`, `accepted=true`, `accepted_by='coding_agent'`, evidence `{summary, execution_receipt_id or cell ids, message_id}` | ✓, overall and parent progress % update, the next task's Assign button enables |
| Run failed, budget exhausted or interrupted | `blocked`, with an explanation naming the reason | "Blocked: <reason>" |
| Run cancelled, or every proposal denied | back to `pending` if no evidence was added, otherwise `in_progress` with an explanation | the state shown is the truth |
| Owner reopens | owner sets `in_progress` (or another state) in `TaskEditor` | the existing editor |

**Verified success** — all of these must hold: the run `completed` (not cancelled or interrupted); **code tasks:** the run's latest `execute_cell` receipt for a cell this run wrote is `applied` with `status=ok`, and no later edit to that cell is unexecuted; **notes-only tasks:** every proposed edit succeeded. Anything else does not auto-complete. A code edit with no successful execution never completes.

### Backend contract change (deliberate; document it)
- Phase 3 forced `accepted_by='user'` in `_validate_plan`. Add a **trusted server-only** path (for example `roadmap_update(..., task_only=True, trusted_acceptor='coding_agent')`), used only by the harness and never reachable from REST. REST keeps forcing `user`.
- `_validate_plan` must keep its rules: dependencies completed; children finished; execution evidence must be an applied, successful receipt for the linked cell. For notes-only completion, evidence lists the cell IDs and the report message ID (no receipt).
- Each auto-completion writes an activity event (`event_type='task_completed'`, `actor='coding_agent'`), with the receipt or cell links.
- Use operation IDs `agent-complete-<run_id>`, `agent-start-<run_id>` and `agent-blocked-<run_id>`. Retry a stale roadmap revision a bounded number of times by re-reading. Never overwrite an owner change made in between. If the owner changed this task meanwhile, record a run event and skip the update.
- Task `links.cell_id` is set to the agent's primary cell when it completes. Revision-checked; keep the existing links.

### UI
The roadmap panel shows each task's live agent status (from `useAgentRuns`), the ✓ for completed tasks with the tooltip "Completed by Coding Agent — verified by receipt <id>", and a **Reopen** action that opens `TaskEditor` prefilled with `in_progress`. The overall progress bar and % update within one poll cycle of the change.

### Tests
Backend: each row of the table above, including notes-only completion and a code edit without execution (no completion); an owner edit to the task between the run starting and completing (no overwrite, an event recorded); completion unblocks the next task's assignment; `accepted_by='coding_agent'` cannot be produced through REST. Frontend: the task status chips, the Reopen action, and a progress % change on a run-state change. Browser: after the real run in the acceptance journey, the Roadmap panel shows task 1 ✓ and overall progress 25% without a reload.

---

## 7. Stage 4 — Project kernel environments and package installer (PR B)

*(Not part of PR A. Summary of the brief retained for PR B.)* Every project gets its own kernel
environment and standalone notebooks share a workspace-default environment, under
`<EUROPA_APPLICATION_ROOT>/kernel-envs/<project_id|workspace-default>/` (private, 0700, no
symlink following), created with `python -m venv --system-site-packages` and a `python3`
kernelspec, wired only through `RuntimeSpec.environment` and the existing supervisor and
Gateway client. Packages install through an owner dialog (Kernel → Packages…) or an
approval-required `install_packages` agent tool; strict PEP 508 validation with
`packaging.requirements.Requirement` (no URLs, paths, VCS, options or markers; 1–10 specs);
`asyncio.create_subprocess_exec` of the environment's pip, never a shell; 600 s timeout;
one install per environment; a `package_installs` journal (schema v8) where `running` becomes
`uncertain` on restart and is never retried; new `/api/notebooks/{id}/environment`,
`/packages` and `/api/package-installs/{id}` routes; containerd returns 501; offline
local-wheel integration test proving project isolation and an untouched `.venv`.

---

## 8. Non-negotiables (all stages)

1. The model is not the harness. The backend owns runs, approvals, side effects and persistence; the UI polls persisted state.
2. Every edit, every execution, every Coordinator-proposed assignment and every agent install needs an owner approval, bound by digest to the exact action and target revision. One approval authorizes one action.
3. **Modification ≠ execution ≠ completion.** Never conflate them in code, events or the UI.
4. A receipt is the only proof of execution. Never fabricate output or progress. Auto-completion only follows the verified rules in §6.
5. Never re-run uncertain Python or pip. Reconcile from receipts or the journal; otherwise mark it uncertain and stop.
6. Never write `.ipynb` files outside `NotebookStore`/`WorkspaceNotebookStore`. Never launch kernels outside `RuntimeSupervisor`/`GatewayClient`. Never run pip through a shell.
7. Agents disabled (the default) → the notebook and Home UI are unchanged, apart from the Packages dialog in Stage 4, which works without agents.
8. No secrets or hidden reasoning in storage, events, logs or responses.
9. Do not redesign the editor. All UI changes are additive and follow existing styles and CSS variables.

## 9. Validation

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q --run-local-gateway
cd web && npm test && npm run build
cd web && EUROPA_CHROME=<chrome> npm run test:browser                               # agents disabled
cd web && EUROPA_CHROME=<chrome> npx playwright test -c playwright.agent.config.ts   # 3 consecutive runs, all must pass
```

Counts must equal the merged-`main` baseline plus additions; skips are never passes. `sessions.spec.ts:41` is a known machine-load flake. Make sure no stale `kernel_gateway` processes are alive; kill only processes your own runs created. Restore `docs/screenshots/phase3h/` after the Phase 3 suite runs. One worker and one heavy job at a time.

## 10–12. Docs, acceptance, commits

Docs: `docs/phase4d-coding-agent.md`, `docs/phase4d-project-envs.md` (PR B), `docs/phase4d-validation.md`, this brief, one README paragraph each for the Coding Agent and Packages, one `AGENTS.md` pointer line. PR A acceptance: kickoff exactly once with questions and a draft card; owner or approved Coordinator assignment; supervised insert/run with a deterministic execution ID, repair rule and report; progress and verified auto-completion attributed to `coding_agent`; conflicts never overwrite; no re-runs after lost acknowledgements or restarts; the real-Gateway acceptance journey passes. Push each branch; PR A title "Phase 4D: supervised Coding Agent, kickoff and progress". If anything conflicts with the code, stop and report.

---

## Owner decisions during implementation (2026-11-19)

1. **Commits.** "Never commit without the owner" means `main`, merges, tags and pushes. Local commits on the feature branch are made by the coordinator after reviewing each stage, one commit per PRD stage; push only after PR A (Stages 1–3) is validated.
2. **Kickoff design (approved):** creations record `origin_message_id`, and only those get a kickoff (no kickoffs for pre-4D notebooks after an upgrade); the kickoff starts after the create call is `succeeded`, from reconciliation, and from a restart sweep, exactly once by operation ID; `ask_owner` is limited to once per run in Python.
3. **Existing tests** may only extend exact assertions; never replace an exact check with a looser one; list every edit.
4. **Stage 2 browser journey** must reach an applied receipt on the real local Gateway (the agent Playwright config runs one); owner-assigned runs must render their panel and approval cards (unit test); capture Approve enabled, hovered and disabled with drafts, the disabled state clearly distinct.
5. **Re-propose for stale cards** replaces "Notebook changed — the agent will re-propose": the card says "Notebook changed since this was proposed." with Re-propose and Deny; the decision is recorded as a denial with reason `stale_revision`; the model receives `conflict` "The notebook changed since this was proposed. Re-read it and propose again."; the run continues; the demo coder re-proposes once. Listed as a PRD deviation.
6. **Phase 3 Chrome failures** on the PR A candidate are treated as the existing load flake after interleaved baseline/candidate runs; a 4F follow-up is recorded in the validation report.
7. **Stage 4 (PR B)** is not started until the owner says so.
