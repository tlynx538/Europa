# Europa agent workflow

This file records project-specific working knowledge. It supplements `AGENTS.md`;
it does not grant tools, change user requirements, or replace current source and
test evidence. General agent methods belong in the shared OpenCode definitions.

## Resume before dispatching

Inspect branch, HEAD, status, diff and the latest milestone report. Preserve
uncommitted work and `AGENTS.md`. Read `README.md` and the contracts relevant to
the task. Do not repeat an already approved architecture investigation unless new
evidence makes its decision relevant again.

Before restarting tests, establish whether a previous run is still active and
inspect its final output. Stop only processes owned by the current task. A server
restart or lost agent connection does not prove its child processes stopped.
Record the candidate, active task, test owner, last actual result, and next step
in a short checkpoint. Do not paste private session exports or credentials here.

## Small tasks before milestone work

Use one worker at a time on this development machine. Give each worker an outcome,
named file scope, invariants, evidence, allowed commands, test ownership, deadline,
tool budget, acceptance criteria and stop condition. Read-only diagnosis precedes
implementation. A useful initial budget is eight tool calls and three minutes;
larger tasks need an explicit budget appropriate to their actual checks.

An agent is ready for a bounded application task after it can:

1. Explain a failing case from source with locations and preserve safety guards.
2. Demonstrate a defect with a deterministic regression, apply a minimal fix, and
   verify both blocked and allowed behavior without changing unrelated files.
3. Distinguish observed results from assumptions and stop on unavailable tools.

Evaluate each role separately. A successful Coder exercise does not qualify a
Reviewer or establish that the whole workflow will complete a milestone. Prompt
updates improve the process; they do not train model weights or guarantee behavior.

## Execution and recovery invariants

Retain React/CodeMirror, `useNotebook`, `.ipynb` revision checks, browser drafts,
server-authoritative receipts, uncertain-execution handling and receipt-scoped
interrupts. Gateway owns kernel processes and its standard HTTP/WebSocket protocol.
Read runtime documentation before lifecycle changes.

Visible output can arrive before the controller releases a pending operation.
Navigation tests must synchronize on the authoritative settled state needed for
navigation, rather than only `Out [n]`. Keep explicit coverage that navigation is
rejected while an operation remains pending. Do not add sleeps, automatic execution
retries or remove the leave guard. Evidence: `docs/phase3f-c-validation.md` and the
baseline diagnosis in `docs/phase3f-d-validation.md`; current source still needs
verification after any relevant change.

Audit execution eligibility across toolbar, menus, cell actions, command mode and
CodeMirror keymaps. Conflict/paused/pending/active states must respect the controller
contract. Unsaved drafts need the existing save-before-submit path; a blanket dirty
check may incorrectly disable that supported workflow. Test both blocked states
and the valid idle case. Output rendering, sanitization and CSP remain separate
invariants even when a patch only changes menus.

## Validation ownership

Assign one owner for a required suite. Run a focused reproduction and affected
checks first, then the complete required regression on a frozen candidate. An
independent reviewer inspects source, diff and original logs; rerun only when a
specific unresolved concern or source change justifies it. Do not run duplicate
browser suites in workers and coordinator. Keep services on loopback.

Use `.venv` and the pinned dependencies. Commands and infrastructure opt-ins are
in `AGENTS.md`. Browser checks already configure one Playwright worker. Preserve
command exit status and complete logs; never pipe a required run into `head`.
Record candidate identity, exact command, relevant environment, exit status,
summary and skipped coverage. Recheck affected evidence after edits. Neither a
targeted rerun nor an older full-suite pass erases a later unresolved full failure.

Verify the Node executable used from `web/` when local results disagree. During
the October 31 supervised recovery, Node 20.17 failed to start seven jsdom test
files with `ERR_REQUIRE_ESM`, while explicitly selecting Node 24.19 passed all
43 tests and the production build. Shell location and npm's script environment
selected different executables. Record the runtime used; do not change package
versions, loosen permissions or repeat the suite before resolving that mismatch.

Browser tests regenerate screenshots, including historical milestone images.
Inspect initial status and preserve pre-existing changes. Do not indiscriminately
restore screenshot directories; stage new evidence separately and review the diff.

The existing 3F-D report records a Docker `/tmp` mount with `noexec` as a diagnosis
of `tsc: Permission denied`. Treat this as recorded prior evidence until verified
against the current sandbox. Inspect mount flags, executable bits and the actual
binary path separately; blanket chmod is not a repair for a noexec mount.

## Keeping lessons useful

Put durable invariants and entry points here; put runnable task briefs in
`docs/agent-prompts/`; put run-specific facts in the milestone validation report.
Include evidence, date and uncertainty where appropriate. Replace stale guidance
when implementation changes. Do not accumulate session transcripts, personal
paths, temporary ports or speculative crash causes as permanent rules.

For Phase 3F-D, use [the P0 audit brief](agent-prompts/phase3fd-p0-audit.md).
The milestone remains incomplete until its required checks and deliverables pass;
do not begin Phase 4 or migrate the frontend.

For Phase 3G, follow the [approved brief](agent-prompts/phase3g-brief.md) and
[current design decisions](phase3g-design.md), including subsequent user feedback.
The latest direction requires full-width fixed notebook chrome, a global bottom
status bar, proper menu rows and guarded File navigation. It supersedes earlier
status docking choices. Read known file paths directly; do not spend repeated
glob calls rediscovering named files. A worker that exhausts investigation without
a handoff is not a successful design review. Prefer explicit source/evidence
questions and require a conclusion before more discovery.

When changing menu primitives, update tests to the resulting accessible roles,
including checkbox/radio state and submenu dismissal. A selection may close a
menu automatically; an old toggle click can reopen it. Preserve each test's
behavioral assertion instead of retaining obsolete disclosure selectors.

After responsive resizing, wait on the actual header/footer/editor geometry
when ResizeObserver measurements determine the scroll viewport. Use bounded
condition polling. An immediate snapshot can describe the previous frame;
fixed delays and forced clicks do not establish usable layout or focus.

Give each corrective test run a distinct log/exit filename and record its exact
scope. A passing single-case repair is not a complete-suite pass. Preserve failed
evidence instead of overwriting it with the latest output.

Keep automated frontend checks serialized, including diagnostic unit runs. Europa's
`npm test` script already selects `src`; appending another path does not narrow
that selection. For one file use `npx vitest run src/Example.test.tsx --maxWorkers=1`;
for the full unit suite use `npm test -- --maxWorkers=1`.

Workspace loads project choices before selected-project metadata. A project name
in an option does not establish readiness; wait for the selected membership or
metadata that the test actually asserts.
