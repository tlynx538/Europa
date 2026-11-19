# Phase 4C brief — Notebook Coordinator

## Owner decisions for this checkpoint (2026-11-15)

- Base: `main` at `ad0ca1a` (PR #5 = 4A and PR #6 = 4B merged). Verified before branching;
  Python baseline on the base: 353 passed, 105 skipped (no Gateway).
- Branch `feature/phase4c-notebook-coordinator`; work in the git worktree `.worktrees/phase4c`.
  The main checkout (`feature/phase4-agentic-harness`, uncommitted 4D work) must not be touched.
- Scope: 4C only. No delegation, cell edits or execution. Do not start 4D/4E/4F.
- **Owner-requested addition:** a top-level `GET /health` route (requests to `/health`
  currently return 404). Owner answer (2026-11-15): "mainly the api health alone, if
  jupyter backend is shutdown etc." Interpreted as: `/health` reports that the API itself is
  up, plus the Jupyter Gateway backend's current state (for example running, stopped,
  unreachable), observed without starting a Gateway or kernel. Owner answer: HTTP 200
  whenever the API itself works, with the Gateway state in the body (for example
  `{"status":"ok","api":"ok","gateway":"stopped"}`); 503 only for real API faults (application
  database unreadable, notebook storage unavailable). Decided: the existing `GET /api/health`
  stays unchanged (it calls `supervisor.client_for("default")` and can start a Gateway
  runtime); `/health` must never do that. Implemented in `1c2c80e`; see
  [the 4C design](../phase4c-notebook-coordinator.md#get-health-owner-requested-addition).
- `gh` is not installed: push the branch and provide the PR body and compare URL unless the
  owner installs it.
- Review limit: at most 3 coder-revision/reviewer-recheck cycles.

## PRD (owner-supplied)

**Base:** `main` at `ad0ca1a`. **Branch:** `feature/phase4c-notebook-coordinator` from `main`.
Open a PR to `main` when done. Do not merge, do not tag, never force-push.
**Scope:** 4C only. The Notebook Coordinator *discusses* a notebook and *plans* work through
approved roadmap changes. It does not delegate, edit cells or run code (that is 4D).

### 0. Read first

1. `AGENTS.md` (wins over this PRD on conflict).
2. `docs/phase4-architecture.md`, `docs/phase4-harness-design.md`, `docs/phase4b-home-coordinator.md`, `docs/phase4b-validation.md`.
3. `docs/conversational-notebooks.md`: canonical notebook conversation, message placement,
   roadmap/task contract (owner-only acceptance, evidence, progress).
4. `docs/notebook-agent-interaction.md`: turns live inline in the notebook stream: user message,
   then a compact progress panel, then the reply. No separate chat sidebar.
5. `docs/agent-prompts/phase4b-home-coordinator.md` (same conventions).
6. Code: `api/src/agents/` (`runtime.py` `_scope_for`/loop/`_complete`; `tools.py` `read_notebook`,
   `read_roadmap`, `read_execution`, `propose_roadmap`, `bind_roadmap`, `preview_roadmap`,
   `roadmap_view`, `_project_for`, `_notebook_roadmap`; `policy.py`; `context.py`; `demo.py`;
   `router.py`); `api/src/application/store.py` (`roadmap_create`, `roadmap_update`,
   `_validate_plan`, `message_append`, `listing`); `web/src/Workspace.tsx` (canvas, stream
   `messageView`, `canvas.composer`, `run()`, roadmap panel `loadProject`); `web/src/main.tsx`;
   `web/src/useNotebook.ts` (polls saved document every 1.5 s); `web/src/agentApi.ts`,
   `AgentRun.tsx`, `agent.css`, `HomeCoordinator.tsx` (reuse, do not fork);
   `web/playwright.agent.config.ts`, `web/browser-agent/`.

Diagnose before editing. Verify before claiming. Skipped tests are not passes.

### 1. Already on `main` (verify, reuse, do not rebuild)

- Role `notebook_coordinator` (`policy.py`) with `read_notebook`, `read_roadmap`,
  `read_execution`, `propose_roadmap`, `delegate_task`.
- `AgentRuntime._scope_for` maps a notebook's canonical conversation to `notebook_coordinator`,
  recording `notebook_id` and current `project_id`; legacy project conversations → 409.
- `propose_roadmap` requires approval; `bind_roadmap` captures `{project_id, notebook_id,
  roadmap_id, roadmap_revision, objective, tasks, removed}`; server-generated task IDs; existing
  task keys keep `status`, `evidence`, `accepted`, `explanation`; the model cannot set `status`
  (`PlanTask` has no status, `extra='forbid'`); execution via `roadmap_create`/`roadmap_update`
  with operation ID `agent-<tool_call_id>`; stale revision → `conflict`, never overwrite.
- Context for notebook roles: fenced notebook view (cells, outputs, revision), project, the
  notebook's roadmap, last 12 messages, `"Project: none…"` when standalone.
- `tests/test_agent_harness.py` covers `propose_roadmap` approval, denial, expiry, stale revision,
  one supervised action per step, restart.
- Agent replies post with `author_role='coordinator'` into the canonical conversation and get
  `placement_after_cell_id` like any message.

(Verified 2026-11-15: `policy.py:36-38`, `tools.py:330-366`, `application/store.py:572`, `context.py:76`.)

### 2. Goal and journeys

Inside an open notebook, the owner talks to the Coordinator in the existing conversation stream.
It understands this notebook (cells, outputs, revision, project, roadmap, tasks, earlier
discussion) and can discuss, propose a plan, revise a plan, and explain task state honestly
including why work is blocked. Plans are saved only after approval bound to the exact plan and
roadmap revision. Progress is never fabricated; completion still requires owner acceptance in
the existing task editor.

- **J1 discuss.** "What does this notebook do so far?" → references actual cells and outputs; nothing changes.
- **J2 plan.** In a project notebook: "Plan how to understand this Markov chain." → inline
  approval card with objective and four tasks with dependencies and criteria: 1 Define a
  transition matrix; 2 Simulate transitions; 3 Estimate the stationary distribution;
  4 Visualize convergence. Approve → roadmap appears in the left Roadmap panel at 0%, all tasks
  `pending` ("upcoming"), without a page reload.
- **J3 revise.** "Add a step to compare with a 3-state chain." → card says **Revise roadmap**,
  bound to the current roadmap revision, lists removed tasks. Approve → new roadmap version;
  existing task states and evidence preserved.
- **J4 conflict.** While a plan approval is pending, the owner edits the roadmap in the side
  panel. Approving yields a visible conflict ("The roadmap changed after this plan was
  proposed…"); the owner's edit stays.
- **J5 status.** "What's left and why isn't it done?" → reflects real task states and
  explanations; never claims done unless roadmap says `completed`.
- **J6 standalone.** In a notebook not in a project, planning is refused honestly ("needs a
  project"); no roadmap; discussion still works.
- **J7 persistence.** Reload, Home and reopen, server restart: messages, run panels, pending
  approvals, event history and roadmap persist.

### 3. Non-negotiables

1. The backend owns loop, policy, approvals, persistence; the UI polls persisted state and posts decisions.
2. **No delegation in 4C.** Remove `delegate_task` from the `notebook_coordinator` tool list and
   `delegate` from its capabilities in `policy.py`. Leave `tools.py` code and `runtime.delegate`
   untouched (4D re-enables). Test that the Notebook Coordinator cannot call `delegate_task` and
   that it is absent from provider tools.
3. No cell edits and no execution by the notebook role (test it).
4. Approval binds the exact plan revision (digest + `roadmap_revision`); one tool call only.
5. Never fabricate progress: agents cannot set task `status`, `accepted` or `evidence` via
   `propose_roadmap`. Completion stays an owner action in `TaskEditor`.
6. Cross-notebook isolation: a notebook run reads only its own notebook (notebook tools take no
   `notebook_id`). `read_notebook_summary` and workspace tools are not in this role.
7. No second chat system: canonical conversation, existing message endpoints, render inside the
   existing notebook stream. No new sidebar or Discussion panel.
8. Do not redesign the editor. `main.tsx`/`Workspace.tsx` changes are additive. With agents
   disabled (default) the notebook UI and every Phase 3 and 4B test are unchanged.
9. Cells, outputs, roadmap text and messages are fenced data; policy in Python.
10. No secrets and no hidden reasoning in storage, events, responses or logs.

### 4. Backend

**4.1 Policy.** Apply §3.2. `notebook_coordinator` budgets stay default (8 model calls, 16 tool
calls, 24 steps, 600 s, 2048 output tokens, ~24k context chars).

**4.2 Context (`context.py`).** Verify/fix that notebook context includes: notebook ID and
revision with provenance `notebook:<id>@<rev>`; ≤30 cells, source ≤1500 chars each, code output
plain text ≤500 chars; markers for omitted leading cells; `agent_authored` flag when a cell has
`metadata.europa_agent`; project title and status; the notebook's roadmap (ID, revision,
objective, progress, tasks with status, dependencies, criteria, explanation, last 3 evidence
summaries); last 12 messages (author, ≤1000 chars, cell reference); for runs triggered by a
message with `cell_id`: "(The owner attached cell <id>.)". Total within budget; explicit
truncation. Never include receipt runtime/kernel identities, Gateway URLs or tokens.
Role prompt must say: "You cannot assign work, edit cells or run code in this phase."; "Never
state that a task is complete unless its roadmap status is completed."; "When the notebook is not
in a project, explain that planning needs a project."

**4.3 Roadmap selection.** `_notebook_roadmap` picks the latest roadmap whose `notebook_id`
equals this notebook. Keep and document. Other notebooks' roadmaps in the same project are not
visible. Test it.

**4.4 Demo provider (`demo.py`).** Deterministic, not an LLM. Notebook branch detected by
`Role: Notebook Coordinator` in the system prompt. In order:
1. After a `propose_roadmap` result: `ok` → "Saved the roadmap with N tasks. Accept tasks in the
   Roadmap panel as you verify them."; `denied` → "No plan was saved."; otherwise → "The plan was
   not saved: <summary>".
2. Context contains `Project: none` and the message asks to plan → refuse honestly (J6).
3. Message matches `plan|roadmap|steps|break down` → `propose_roadmap`: "markov" in message or
   context → the J2 Markov tasks chained by dependency; otherwise Inspect the data → Explore key
   relationships → Summarize findings; if a roadmap exists, reuse existing task IDs as keys and
   append the new tasks (a revision, J3).
4. Message matches `status|left|blocked|progress|done` → `read_roadmap`, then a factual per-task
   status list taken only from the tool result.
5. Otherwise → short discussion naming the notebook's cell count and revision from context.
Home branch behaviour byte-for-byte unchanged; its tests still pass.

**4.5 API.** `POST /api/agent/runs` with the canonical conversation already works. Verify and
test: legacy project conversation → 409; message from another notebook → 422;
coordinator-authored message → 422. (Owner addition: `GET /health`, see decisions above.)

### 5. Frontend (additive)

**5.1 `AgentRun.tsx` / `agentApi.ts`.** `ApprovalCard` renders roadmap previews: title ("Create
roadmap"/"Revise roadmap"), objective, rationale, ordered tasks (title, "after <dependency
titles>", criteria as sub-bullets), removed task IDs as a warning, "Bound to roadmap revision N"
when `roadmap_revision` is set. Keep 4B `actions`/`purpose` rendering. Unknown preview fields
ignored, never raw HTML. `AgentRunPanel` gets optional `onRevealCell?(cellId)` and
`children?: RunView[]`; render child runs as siblings after the `<details>`, not inside it (so a
collapsed completed parent never hides an active child's approval card). Human labels for
`read_notebook`, `read_roadmap`, `read_execution`, `propose_roadmap`.

**5.2 `Workspace.tsx`.**
1. On mount `agentStatus()` → `agentEnabled`; false → render nothing new anywhere.
2. `useAgentRuns(conversation?.id, agentEnabled, onChange)`; on change reload conversation
   messages and, when a project is loaded, `loadProject(project.id)` so the roadmap updates.
3. In `messageView`, after each user message article, render `AgentRunPanel`s whose
   `trigger_message_id` equals it and `parent_run_id` is null, passing children. Wrap article and
   panels in a keyed `Fragment` (`messageView` is used in several stream positions).
4. "Ask Coordinator" checkbox, default on, persisted in `localStorage` `europa.agent.ask`, placed
   above `ConversationComposer` inside `.composer-shell` (below, it was hidden by the floating
   composer and footer). Only when agents are enabled.
5. Send: keep the existing message POST exactly (`change()` → `/conversations/{id}/messages`).
   On success, if enabled and toggle on, `startRun(conversation.id, posted.id)` (pending scope
   `run.<conversation>`). Unknown outcome → inline alert "Starting the Coordinator has an unknown
   outcome." with Retry reusing the stored operation. A start failure never loses or duplicates
   the user message.
6. `onRevealCell` → existing `jump(notebookId, cellId)`.
7. Agent messages keep `data-author="coordinator"`. Progress panel collapses to
   "Completed · N actions" when finished.
8. Mobile 390×844: panels and cards fit the notebook width, no horizontal scroll, approval buttons
   reachable above the composer.
Do not change cell rendering, menus, toolbar, autosave, conflict review or receipt behaviour.

### 6. Tests (add; never weaken)

**Backend `tests/test_agent_notebook.py`** (Harness + FakeProvider from `tests/agent_support.py`):
1. Context: `notebook:<id>@<rev>`, cell source, plain-text output from an applied receipt, earlier
   message, project title, "No roadmap yet."; reply from `coordinator`, `placement_after_cell_id`
   = last cell.
2. Plan approval: preview has 4 titles, dependency titles, `roadmap_revision: null`; after approval
   exactly one roadmap, `notebook_id` set, all `pending`, progress 0, dependencies mapped to server
   IDs, `links.notebook_id` set, version 1 stored.
3. Revision: arguments with `status: completed` → `invalid`; valid revision bound to current
   revision; after approval revision increments, existing task's `in_progress` and title update
   kept, new task `pending`, progress unchanged.
4. Stale plan: owner updates roadmap between proposal and approval → `failed/conflict`, owner's
   objective remains.
5. Removed tasks in preview `removed_task_ids`.
6. Isolation: `read_notebook_summary`, `delegate_task`, `insert_code_cell`, `execute_cell` →
   `policy` and absent from provider tools; transcript has notebook A content, none of B's; B's
   roadmap in the same project not returned by `read_roadmap` for A.
7. Standalone: `propose_roadmap` → `policy`; context contains "Project: none".
8. Restart: waiting plan approval survives `restart()`; canonical conversation ID unchanged;
   `GET /api/agent/runs?conversation_id=` shows `waiting_for_approval` with pending approval;
   after approval events start `run_queued, run_started, context_assembled, approval_requested`
   and end with `reply_posted`.
9. HTTP scope: legacy project conversation 409; other notebook's message 422; coordinator message 422.
10. Cancel: cancelling a waiting plan invalidates the approval; no roadmap.
11. Demo provider: J2 four Markov tasks; J3 revision preview keeps existing IDs; J5 statuses only
    from `read_roadmap`; J6 refuses; Home branch unchanged (re-run 4B demo tests).

**Frontend vitest:** roadmap `ApprovalCard` (objective, ordered tasks, dependency text, criteria,
removed warning, revision binding; Approve posts `{decision, digest, operation_id}`);
`AgentRunPanel` children render outside a collapsed completed parent and a child's pending
approval is visible; `Workspace` (or extracted component): disabled → no toggle/panels, only extra
request `/api/agent/status`; enabled + toggle on → message POST first, then `POST /api/agent/runs`
with exactly `{conversation_id, message_id, operation_id}`; toggle off → no run; start 503 →
alert with Retry, retry reuses the same `operation_id`, message not re-posted.

**Browser** (`web/playwright.agent.config.ts`, add `web/browser-agent/notebook.spec.ts`; set up a
project + notebook via workspace APIs or the Home flow): J2 (card lists 4 tasks → Approve →
Roadmap panel shows 4 upcoming without reload; `/api/roadmaps/<id>` statuses `pending`); J3
("Bound to roadmap revision 1" → Approve → revision 2); J4 (propose → edit objective via API →
Approve → conflict shown, owner objective kept); J7 (reload, Home and back, pending approval
visible after reload; panels collapsed "Completed · N actions"). Screenshots desktop 1360×900 and
mobile 390×844 → `docs/screenshots/phase4c/`. Scope selectors: the plan objective often repeats
the user's text; use `[data-author="user"]` and `role=region[name="Approval: …"]`.

### 7. Validation

Baselines (`main@ad0ca1a`): Python with Gateway 379 passed/79 skipped; without 353/105;
`npm test` 107; build passes; Phase 3 Chrome 56 passed/1 skipped (known flake
`sessions.spec.ts:41`); agent browser suite 3 passed.
Commands: `.venv/bin/python -m pytest -q`; `.venv/bin/python -m pytest -q --run-local-gateway`;
`npm test && npm run build`; `EUROPA_CHROME=… npm run test:browser`;
`EUROPA_CHROME=… npx playwright test -c playwright.agent.config.ts` (3 runs, all pass).
Counts = baseline + additions. `sessions.spec.ts:41` failing is reported as a failure with
context, never a pass, and not fixed here. Do not touch old `kernel_gateway` processes; report
interference. Restore `docs/screenshots/phase3h/` after the Phase 3 suite. One worker, one heavy job.

### 8. Documentation

`docs/phase4c-notebook-coordinator.md` (built features; roles/capabilities with delegation
disabled until 4D; context contents and limits; plan proposal/revision/binding rules; roadmap
selection rule; UI integration; known limitations). `docs/phase4c-validation.md` (exact commands
and counts, skips with reasons, screenshots, flakes, every deviation). This file. README: one short
Notebook Coordinator paragraph. AGENTS.md: one pointer line to the 4C docs.

### 9. Acceptance

Disabled → notebook UI and Phase 3/4B suites unchanged. J1–J7 work with demo in the browser and
FakeProvider in backend tests. Plans saved only after approval bound to digest + roadmap revision;
stale revision is a conflict, no overwrite. Revisions preserve task state/evidence; agents cannot
set status/acceptance/evidence. Notebook Coordinator cannot delegate, edit, execute or read other
notebooks. Persistence across reload, Home round trip, restart. Roadmap panel and conversation
update without reload. Child runs render outside a collapsed parent. No secrets/reasoning stored.
§6 passes, baselines hold, §8 docs written.

### 10. Commits, PR, handoff

Commits in order: (1) policy + context/prompt + backend tests; (2) demo notebook branch + tests;
(3) ApprovalCard/AgentRunPanel + unit tests; (4) Workspace integration + unit tests; (5) browser
journeys + screenshots; (6) docs + validation. Push and open a PR to `main` titled
"Phase 4C: Notebook Coordinator" (summary, commits, validation table, deviations, known flake,
not done, review focus: delegation removal, revision binding, additive `Workspace.tsx` diff).
Handoff: PR URL, head SHA, files, counts, deviations, risks, next step. On conflict with the code,
stop and report. Never weaken a test.
