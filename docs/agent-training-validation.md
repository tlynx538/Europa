# Supervised agent recovery — October 31, 2026

This is a bounded agent-improvement checkpoint, not Phase 3F-D completion. It
preserves the interrupted working tree on `feature/phase3f-d-ui-recovery`, based
on HEAD `53aaee1`. Existing UI changes, screenshots and validation claims were
inspected before editing and preserved. No milestone commit was created.

## Shared methods and project knowledge

The OpenCode starter and installed agent definitions now specify small scopes,
invariants, tool/deadline budgets, one test owner, sequential workers, targeted
reproduction before repair, and compact evidence-based handoffs. Models and native
permissions were preserved. Conservative governor defaults admit one worker and
one heavy tool/shell, with four queued jobs, one delegation level and a 15-minute
cooperative work deadline. These are per-governor controls, not machine-wide
confinement or a guarantee against macOS exhaustion.

After evaluation sessions were idle, `opencode reload` succeeded. A subsequent
live Poseidon status call reported **maxAgents=1, maxTools=1, maxDepth=1**. One
native Poseidon-to-Maintainer handoff then completed in 31 seconds with three
coordinator tool calls and no source changes. This verifies the tested location's
loaded configuration; independent servers retain their own governors.

The starter contains reusable disposable fixtures, a bounded live-evaluation
runner and its own evaluation report at `docs/AGENT_EVALUATION.md`. The runner
checks session location, fingerprints scoped source, records visible evidence and
interrupts server-side generation on timeout. Three mocked supervisor regressions
cover location mismatch, deadline cleanup and ignoring an old idle outcome when
reusing a session. No model weights were trained.

Europa-specific lessons live in [agent-workflow.md](agent-workflow.md), linked
from the preserved root `AGENTS.md`. The reusable
[P0 audit brief](agent-prompts/phase3fd-p0-audit.md) records scope, invariants,
allowed reads, budgets and a stop condition. Shared definitions contain general
methods; project notes contain Europa contracts and supported evidence.

## Exercises and observed corrections

| Role/task | Observed behavior |
| --- | --- |
| Poseidon diagnosis | Three reads; identified output preceding receipt settlement and preserved the guard |
| Poseidon native coordination | One status read and one worker handoff; live serial limits verified, acceptance checked, no duplicate investigation or test runs |
| Team Lead plan | 11 seconds, three reads; proposed deterministic synchronization and explicit refusal coverage |
| Researcher investigation | 18 seconds, five reads; correct cause, initially incorrect guard-coverage assertion. Two same-session corrections produced a runnable standard-library regression, independently executed with exit 0 |
| Coder toy repair | 32 seconds, eight tool calls; one failing regression before repair, six passing tests after; independent 32-case eligibility matrix passed |
| Reviewer adversarial candidate | 35 seconds, four reads; rejected unconditional execution blocking despite a passing paused test. Severity was overstated; impact calibration was added to shared instructions |
| Maintainer 3F-D snapshot audit | 42 seconds, seven reads; identified direct notebook switching with a pending receipt as a concrete source gap |
| Coder 3F-D snapshot repair | 67 seconds, ten tool calls; only controller/test changed; focused suite went from ten passed/one failed to eleven passed |
| Reviewer 3F-D repair | Larger review exceeded its 120-second deadline after five reads and was interrupted. Narrower same-session verdict took 26 seconds, no additional reads; no blocking defect found in the scoped change |

These results qualify the roles for similarly bounded supervised tasks. They do
not establish unrestricted readiness, one-shot accuracy, long-session stability
or a controlled before/after performance improvement. In particular, Researcher
test proposals need implementation review, and Reviewer briefs need narrow
questions and explicit deadlines. No silent model substitutions were made.

## First accepted application repair

`useNotebook.open()` blocked only the action lock. A nonterminal execution
receipt can remain after submission and document refresh release that lock.
Notebook-to-notebook navigation delegates its protection to `open()`, so it
could switch and clear the visible receipt history during that interval.

The isolated regression reproduced that switch. The accepted one-line repair
also checks `active` before opening. Its regression verifies refusal before a
target fetch or draft confirmation, preservation of the notebook/receipts/drafts,
and a permitted switch after authoritative receipt history settles. Existing
open deduplication and the controller's execution/receipt contracts remain intact.
The patch was reviewed independently, checked against the unchanged source
snapshot, and then applied to `web/src/useNotebook.ts` and its hook tests.

The audit's proposed execution/render-boundary race remains a hypothesis. It
was deliberately excluded from this repair; source inspection alone does not
prove that triggering sequence occurs.

## Validation and remaining scope

- Europa with Node **24.19**: `npm test -- --maxWorkers=1`, **43 passed**;
  `npm run build`, **passed** TypeScript and Vite.
- Initial Node **20.17** run: **failed** to start seven test files with
  `ERR_REQUIRE_ESM`; only five tests ran successfully. The initial build exited
  zero while warning that Node 20.17 is unsupported. These are not substituted
  for the clean Node 24 results. Runtime selection was corrected without changing
  dependencies or permissions.
- Starter: **24 Node governor/plugin tests passed**. Python discovery ran
  **64 tests: 59 passed, five opt-in live-server cases skipped**. Skill/provenance
  validation and shell syntax passed. Skipped cases are not live permission proof.
- Initial governor tests assumed the former parallel defaults and failed;
  fixtures now request explicit parallel stress budgets, while production-default
  tests verify serial admission. Only the identified owned failed run was stopped.
- The first fixture CLI resolved to the wrong directory; the next attempt hit
  the plugin's Git-repository requirement. Explicit session location checks and
  `git init` corrected these evaluation-infrastructure issues. Reused-session
  supervision also needed the new-idle regression noted above.
- No complete Chrome, Python application, real Gateway opt-in or Go regression
  was run in this checkpoint. No new screenshots, actual browser zoom or
  assistive-technology validation is claimed. No backend or Go source changed.
  Earlier milestone evidence remains historical.
- The complete Phase 3F-D acceptance checklist, browser regressions, accessibility
  and visual evidence still need completion on the current candidate before its
  requested milestone commit. Do not begin Phase 4 or migrate the frontend.

The saved prior session shows repeated full browser runs by multiple roles,
background test commands with timeout zero, and final DNS/authentication failures
after restart. This supports changes to task ownership and bounded execution;
it does not establish the root cause of macOS resource exhaustion. No credentials,
raw session dumps or private environment data were added to the repository.
