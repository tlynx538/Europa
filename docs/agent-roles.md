# Agent roles (Europa, Phase 4D onward)

Project-specific notes on how the OpenCode agents are used on this repository. General agent
definitions, models and permissions live in the shared OpenCode setup, not here; this page
records how the owner has asked them to be used and what each one can actually do. See also
[agent-workflow.md](agent-workflow.md).

## Roles

| Role | Purpose here | Capabilities observed | Limits |
|---|---|---|---|
| **Poseidon** (coordinator) | Takes the owner's request to completion: briefs, reviews, commits after review, owns heavy validation, reports to the owner | Shell, git, file tools, delegation | Commits only on feature branches; pushes only when the owner says so; never merges, tags or force-pushes |
| **Coder** | Implements one bounded stage per brief in its worktree | Shell, file edits, focused tests | Never commits or pushes; runs only the checks assigned in its brief |
| **Reviewer** | Independent review at each stage handoff, before Poseidon commits | Read-only source and log review | Verdict ACCEPT or FIX with file:line findings; re-runs a test only for a specific unresolved concern |
| **Team-Lead** | Short, read-only, decision-shaped oversight: design reviews before a stage brief, a final readiness check before a push | A high-capability model in a separate context from Poseidon and the Coder; reads source and docs | No edits, no test suites, builds or servers; always given a tool budget and a stop condition |
| **Maintainer** | Hygiene inventories (worktrees, stale processes, branches) and compatibility questions | File read, grep and glob only — **no shell** | Cannot run `git`, `ps` or `uptime`; Poseidon runs its read-only commands for now and reports them as Poseidon's results |
| **Researcher** | Bounded external/source research when a question changes a decision | Read and web research | Never receives private source in queries |

## Concurrency (owner exception for PR B, 2026-11-19)

`AGENTS.md` asks for one worker at a time on this machine. For Phase 4D PR B the owner allows
**one additional light agent while the Coder works**: no test suites, builds, servers or
Playwright, and no edits in the Coder's worktree. Heavy validation stays with Poseidon, one
job at a time. Every agent gets an outcome, file scope, tool budget and stop condition.

## Sequence used for PR B

1. Team-Lead design review before a stage brief (read-only, about 12 tool calls).
2. Coder implements the stage.
3. Reviewer reviews the stage diff and logs (about 15 tool calls); Poseidon commits only after
   ACCEPT or resolved FIX items.
4. Poseidon runs the full validation on the frozen candidate.
5. Team-Lead final readiness check before the push (about 8 tool calls).

Owner updates name which agent did what, its verdict, and any finding Poseidon overrode, with
the reason.

## Known capability gaps and provider limits

- **Maintainer has no shell** (2026-11-19): its Phase A inventory could only read `.git`
  metadata files. Poseidon ran `git worktree list`, `git status`, `git merge-base`,
  `git ls-remote`, `ps` and `uptime` on its behalf.
- **Provider limits stop a role; they are not silently replaced.** On 2026-11-19 a Team-Lead
  run failed because its configured model's usage credits were exhausted; the owner chose to
  skip that review and later to run Team-Lead on a high-capability model in a separate
  context. A Coder run was interrupted by a usage limit and resumed in the same session after
  the reset.
