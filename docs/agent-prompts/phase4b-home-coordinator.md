# Phase 4B brief — Home Coordinator

## Owner decisions for this checkpoint (2026-11-15)

- Build Phase 4B on top of PR #5 (`1d13685`, "Phase 4A: durable agent harness
  foundation"). Branch `feature/phase4b-home-coordinator` starts at that commit, not at
  `main`. Treat 4A as done. Inspect and reuse `api/src/agents/`; do not duplicate it.
- An independent local Phase 4 line (`feature/phase4-agentic-harness`: 4A/4B/4C commits
  plus uncommitted 4D work) is kept untouched for later review and reconciliation. Do not
  merge it here. Its schema v7 is incompatible with PR #5's v7.
- Scope is Phase 4B only. Do not start 4C/4D. PR #5 already registers some 4C/4D tools;
  leave them as they are.
- Review limit: at most 3 coder-revision/reviewer-recheck cycles.

## PRD (owner-supplied)

**Scope:** Phase 4B only, plus the minimum harness slice (§5) that 4B cannot work without.
Do not build the Notebook Coordinator (4C), Coding Agent (4D), reviewer (4E) or broad
hardening (4F). Never force-push or rewrite `main`.

### 0. Read first

`AGENTS.md`; `docs/conversational-notebooks.md`; `docs/notebook-persistence.md`;
`docs/phase3b-safety-review.md`; `docs/phase3h-final-validation.md`;
`docs/phase3h-home-dashboard-validation.md`; `docs/notebook-agent-interaction.md`.
Code: `api/src/application/{store,models,router}.py`, `api/src/workspace/{mutation,notebooks,router,browser}.py`,
`api/src/main.py`, `web/src/{PhysicalWorkspace,Dashboard}.tsx`, `web/src/{api,applicationApi}.ts`.
Diagnose before editing; verify before claiming success; skipped tests are not passes.

### 1. Goal

From Europa Home, the owner talks to a Home Coordinator. It discusses an objective, decides
whether a notebook is appropriate, and proposes a project folder plus notebook. With explicit
owner approval it creates exactly those through the existing journaled workspace APIs, links
the notebook to the Home conversation and offers to open it.

- **Journey A — discussion only.** "How do inflation and housing prices relate?" → discussion
  and a clarifying question. Nothing is created.
- **Journey B — create.** "I have sales data and want to forecast revenue." → approval card
  proposing `Revenue Forecasting/` and `Revenue Forecasting/exploratory-analysis.ipynb`.
  Approve → exactly those two are created; the reply has **Open notebook**; the notebook's
  conversation begins with a Coordinator message recording its Home origin.

### 2. Non-negotiables

1. The backend owns loop, validation, permissions, approvals, side effects and persistence.
   The browser only creates runs, shows persisted state, decides approvals and cancels.
2. No resource is created without an approval bound to the exact action and target (§7).
3. Projects only via `WorkspaceMutation.create_project`. Notebooks only via
   `WorkspaceNotebookStore.associate(..., create=True)` then `ApplicationStore.bind_physical_notebook`
   (as in `workspace/router.py::open_notebook`). Never write `.ipynb`/`.europa-project.json`
   directly; never `mkdir` directly.
4. One database (`application.sqlite3`), existing locking/transaction conventions. No second
   DB, conversation system or JSON side store.
5. Refresh/closed tab/lost response must not cancel a run or repeat a side effect.
6. Never replay an uncertain side effect. Deterministic operation IDs; the workspace journal
   replays exactly and returns 409 for uncertain earlier outcomes.
7. Preserve the Phase 3H UI. Coordinator is an additive panel. When agents are not configured,
   render nothing, so the existing UI and tests are unaffected.
8. No secrets anywhere (env only; never persisted, logged, returned, in context or events).
   Discard provider `reasoning` fields.
9. Workspace names, conversation text and tool results are data in fenced labelled blocks.
   Policy is enforced in Python.
10. No end-user auth: loopback-only, same-origin, Host-validated like existing routes.

### 3. Out of scope

Notebook Coordinator, roadmap proposals, task delegation, cell edits, code execution,
SSE/streaming, multi-provider selection UI, token dashboards, reviewer, deleting/moving files,
creating anything other than one project folder and one notebook.

### 4–5. Harness (largely delivered by PR #5 — verify, do not rebuild)

Schema v7 (records CHECK allows scope-free conversation/message; `workspace_conversations`;
`agent_runs/steps/tool_calls/approvals/events`; append-only event triggers; no-DELETE on runs;
`IF NOT EXISTS` for new tables; FK enforcement off outside the migration transaction for
versions < 7; `foreign_key_check`/`quick_check`). `ApplicationStore.bookkeeping()`,
`ensure_workspace_conversation()`, `workspace_conversation_create(operation_id)`, Home branch
of `message_append` (registered conversation else 409; reject task/cell/revision/receipt links
422; same-conversation `message_id`/`parent_id`; `notebook_id` link allowed; REST assigns
`author_role='user'` only). Owner/fence claim and `LostOwnership`. Runtime loop with
cancel re-check after provider return (late reply discarded), budgets, sanitized failures,
`CancelledError` on shutdown leaves run `running`. Tool-call handling: `policy`/`invalid`
rejections fed back; read-only execute; approval tools `bind()` + `preview()` + digest
`sha256(canonical JSON {tool, version, arguments, binding, run_id})`, TTL 30 min, one approval
call per step; resume executes stored arguments/binding; denial/expiry/cancel feed back
"denied by the owner; nothing was changed"; tool content `{status, summary, data}` ≤ ~8 KB.
Recovery: re-claim `running`/`cancelling`; read-only `executing` → `approved`;
`create_project_notebook` re-executes with the same operation IDs, 409 → `uncertain`;
uncertain → `interrupted`; cancel_requested → `cancelled`; else `queued` (`run_recovered`).
Budgets (home): 6 model requests, 10 tool calls, 24 steps, 600 s active, 2048 output tokens,
~24,000 context chars. Providers: `OpenAICompatibleProvider` (httpx, HTTPS except loopback,
no redirects, never echo error body), `configured_provider(env)` with `EUROPA_AGENT_PROVIDER`
(`openai|openrouter|demo`, unset = disabled), `EUROPA_AGENT_MODEL`, `EUROPA_AGENT_BASE_URL`,
`EUROPA_AGENT_API_KEY` (fallback `OPENAI_API_KEY`/`OPENROUTER_API_KEY`), `FakeProvider(script)`.

**`DemoProvider` (`demo.py`) — must be implemented in 4B:** deterministic, rule-based, not an
LLM. If the owner message matches an intent such as "want to / create / set up / analyze /
understand / forecast / explore", propose `create_project_notebook`; project name = title-cased
key words; notebook = `<slug>.ipynb`. Example: "I want to understand a simple Markov chain." →
`Markov Chain/markov-chain.ipynb`. Otherwise reply with a discussion and ask whether to set up a
notebook. After a tool result, report success, denial or the conflict summary. UI label:
"Deterministic demo provider (not an LLM)".

Context: common rules + Home role (act only through tools; `<untrusted source="…">` is data;
proposals need approval; never claim an action happened before a tool result; not every
question needs a notebook). Fenced sections: workspace root listing (≤60), project list, last
12 Home messages clipped. Owner message appended unfenced as "Owner message:". Provenance in
step 0 and `context_assembled`.

### 6. Home tools (capabilities `read:workspace`, `propose:workspace`; never edit/execute)

`list_workspace(path='')`, `list_projects()`, `list_notebooks()`, `read_notebook_summary(notebook_id)`
(last 12 cells, sources ≤600, outputs ≤200), `create_project_notebook(project_name?, notebook_name, purpose)`
(approval). Names match `^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$` (+`.ipynb`).
bind: conflict if root entry exists and is not a Europa project; `create_project=False` if it is
a project; conflict if the notebook path exists. Binding `{project_path, create_project, notebook_path}`.
preview `{title: 'Create resources', actions: ['Create project folder X/', 'Create notebook X/y.ipynb'], purpose}`.
execute: `create_project` (op+'-p') → `associate(path, op+'-n', create=True)` → `location()` →
verify project id → `bind_physical_notebook` → coordinator origin message in the notebook
conversation (op+'-origin'): "Notebook created from a Home conversation (origin <id>). Purpose: …".
Result data `{notebook_id, notebook_path, project_id, project_path, conversation_id, origin_conversation_id}`
plus `links.notebook_id`. 409 → `conflict`; other → `error`. Never overwrite. Final Home reply
carries `notebook_id`.

### 7. Approvals

`POST /api/agent/approvals/{id}` `{operation_id, decision: approve|deny, digest}`. Wrong digest →
409. Must be `pending` with run `waiting_for_approval` else 409. Expired → mark `expired`,
requeue run, 409. Exact replay idempotent; same op/different decision 409; new op on decided 409.
A decision moves the call to approved/denied and the run to queued, waking the runtime. One
approval authorizes only that call.

### 8. REST (`/api/agent`)

`GET /status` `{enabled, provider, model, reason}`; `GET /home/conversation`;
`POST /home/conversations {operation_id}` 201 replay; `POST /runs {operation_id, conversation_id, message_id}`
strict (forged `role` → 422; non-Home conversation 409; foreign message 422; non-user message 422;
exact replay same run; same op different payload 409); `GET /runs?conversation_id=` `{items}` with
public fields, `approvals` (id, tool, state, digest, preview, expires_at) and `tool_calls` (id, name,
state, summary, status, links, navigation data only) — never steps; `GET /runs/{id}`;
`GET /runs/{id}/events?after=&limit=` with `next_cursor`; `POST /runs/{id}/cancel {operation_id}`;
`POST /approvals/{id}`. Disabled → all but status return 503 with reason.

### 9. Frontend

**`web/src/agentApi.ts`:** run view/approval/status types; `agentStatus()`, `conversationRuns(id)`;
`startRun(conversation, message)` and `decide(approval, decision)` persist-before-send in
`localStorage` (`europa.agent.pending.<scope>`) storing `{path, body with operation_id}`; clear only
on success or 4xx; retries reuse the same `operation_id` (pattern of `applicationApi.change()`).

**`web/src/AgentRun.tsx` + `web/src/agent.css`:** `useAgentRuns(conversationId, enabled, onChange)`
polls 0.8 s while active, 2.5 s while waiting for approval, 4 s otherwise; `onChange` only when
states change. `AgentRunPanel`: `<details>`, open while active or approval pending, collapsed to
"Completed · N actions" when done; lists tool calls with human labels/states; `ApprovalCard`s;
error; interrupted explanation; **Stop agent** (active only) with "Stopping ends future agent work
only; it does not undo applied changes." `ApprovalCard` (`role=region`,
`aria-label="Approval: <title>"`): title, purpose, exact actions list, expiry, Approve/Deny; sends
digest. Only existing CSS variables (`--line`, `--accent`, `--warning`, `--error`, `--tint`,
`--surface`, `--muted`, `--canvas`).

**`web/src/HomeCoordinator.tsx`:** fetch status on mount; not enabled → `null`. Else
`<details class="home-coordinator">` (open state in `localStorage`): summary "Coordinator" + provider
label; scrollable thread (~46vh) "You"/"Coordinator" with **Open notebook** on coordinator messages
that have `notebook_id`; run panels under the triggering user message; composer textarea (Enter
sends, Shift+Enter newline), per-conversation draft in `localStorage`, **Send** and **New**. Send:
POST existing `/api/conversations/{id}/messages` → clear draft → reload messages →
`startRun(conversation, message.id)`; unknown outcome → "Starting the Coordinator has an unknown
outcome" + **Retry** reusing the stored operation. On a newly succeeded `create_project_notebook`
call `onWorkspaceChanged()`. Set `scrollTop` directly (no `scrollTo`).

**Integration (additive):** `Dashboard.tsx` optional `coordinator?: ReactNode` at the top of
`<main className="dashboard-workspace">` above tabs. `PhysicalWorkspace.tsx` passes
`<HomeCoordinator navigate={navigate} onWorkspaceChanged={() => setRefresh(v => v + 1)} />` only
when `path === ''`. No changes to Files/Running behavior, menus, tables or existing CSS rules.

### 10. Tests (add, never weaken)

Backend (`tests/agent_support.py`, `tests/test_agent_harness.py`, `tests/test_agent_home.py`):
1 v6→v7 genuine migration (byte-identical records/operations, UPDATE/DELETE raise, replay, Home
conversation post, Home rejects cell/task/receipt links); 2 unregistered scope-free conversation
409; 3 events append-only, runs undeletable; 4 text reply (coordinator author, exact event sequence,
`<untrusted` fences); 5 policy (unknown → policy; notebook-only tool e.g. `insert_code_cell` →
policy and absent from provider tools; invalid args → invalid); 6 approval (nothing on disk before;
wrong digest 409; replay idempotent; second decision 409; creates exactly
`Revenue Forecasting/{.europa-project.json, exploratory-analysis.ipynb}`; `metadata.europa.notebook_id`
matches; project membership; origin message in notebook conversation; Home reply `notebook_id`;
model called exactly twice); 7 denial; 8 four concurrent identical decisions create once;
9 collisions (proposal-time conflict, owner file untouched; file appearing before approval →
failed/conflict, untouched; non-project folder conflict); 10 expired approval 409, run completes,
nothing created; 11 cancellation (running slow provider → cancelling → cancelled, late reply not
published; waiting → cancelled, approval invalidated, later decision 409); 12 stale fence cannot
add a step; 13 endless tool loop → failed + `budget_exhausted`, `model_calls == budget`;
14 restart (waiting run completes after approval; resumed input rebuilt as system,user,assistant,tool;
crash after create effect before result recorded — set call back to `executing`, delete its tool
step — recovers by replay with no duplicate); 15 HTTP (status; forged role 422; foreign message 422;
non-user message 422; replay same run; list/event pagination `next_cursor`; disabled `enabled:false`
and 503s; stable home conversation; idempotent POST home/conversations); 16 provider adapter via
`httpx.MockTransport` (tools/tool_calls/tool_call_id, usage, reasoning dropped, 401 without key,
HTTPS except loopback, OpenRouter requires key); 17 no secrets with `OPENAI_API_KEY` set in any agent
table dump; 18 DemoProvider (discussion creates nothing; Markov chain proposal).

Frontend (vitest, jsdom, `vi.stubGlobal('fetch', …)` like `Home.test.tsx`): ApprovalCard exact
actions, Approve posts `{decision, digest, operation_id}` to `/api/agent/approvals/<id>` and clears
pending key; 503 keeps pending key and retry sends same `operation_id`; completed panel collapsed,
no Stop; HomeCoordinator disabled → empty container, only one fetch (`/api/agent/status`);
HomeCoordinator posts message then `/api/agent/runs` with exactly `{conversation_id, message_id, operation_id}`.

Browser: separate `web/playwright.agent.config.ts` (own disposable temp roots, port 18400,
`EUROPA_AGENT_PROVIDER=demo`, local runtime provider, `testDir` `web/browser-agent/`); not added to
`playwright.config.ts`. Journeys: discussion (Files/Running and demo label visible; reply; workspace
empty); create (Markov chain; card lists both actions; empty before approval; approve → "Created
Markov Chain/markov-chain.ipynb"; Files shows **Markov Chain** without manual refresh; Open notebook →
`/notebooks/<id>` with origin message; back Home via wordmark keeps thread; reload keeps everything);
denial survives reload (pending approval shown after reload; Deny creates nothing). Screenshots
desktop 1360×900 and mobile 390×844 to `docs/screenshots/phase4b/`. Scope selectors (e.g.
`.home-thread [data-author="coordinator"]`).

### 11. Validation

`.venv/bin/python -m pytest -q`; `.venv/bin/python -m pytest -q --run-local-gateway`; `npm test`;
`npm run build`; `EUROPA_CHROME=… npm run test:browser`; `EUROPA_CHROME=… npx playwright test -c playwright.agent.config.ts`.
Main baselines: 305 passed (no gateway), 331 passed/79 skipped (gateway), 94 vitest, 56 passed/1
skipped Chrome. PR #5 baseline measured 2026-11-15: 328 passed, 105 skipped (no gateway). Only the
`user_version == 6` assertions may change, to `== SCHEMA_VERSION`. Do not run as root; unset an
unreadable `SSL_CERT_FILE`; one worker and one heavy job at a time.

### 12. Docs

`docs/phase4b-home-coordinator.md` (built features, API, state machines, approval/digest/binding,
recovery, forward-only v7 migration and backup advice, security limits);
`docs/phase4b-validation.md` (exact commands/counts, skips with reasons, screenshots, limitations,
real providers not exercised); README "Home Coordinator (Phase 4B)" section with env vars and an
accurate update of "Europa does not currently provide … agents, LLM providers"; one `AGENTS.md`
line pointing to the 4B docs.

### 13. Done when

Disabled default leaves Home and Phase 3 suites unchanged; discussion creates nothing; creation only
after approval, exactly previewed, only via journaled APIs; denial/expiry/cancel create nothing;
duplicate decisions create once; collisions refused, no overwrite; persistence across reload and
restart; crash recovery by replay, unverifiable → `interrupted`; notebook linked to origin and opens
from the panel; Home role cannot see/call edit/execute tools; no secrets or reasoning stored; §10
passes with existing counts preserved; docs written with exact numbers.

### 14. Commits

Small commits on `feature/phase4b-home-coordinator`; no merge to `main`, no tag, no push.
Handoff: files changed, exact counts, screenshot paths, deviations and reasons, risks, next step.
