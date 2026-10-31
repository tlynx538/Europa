# Phase 3F-D: bounded read-only execution audit

Role: Maintainer. Objective: identify remaining execution-eligibility or pending
navigation defects in the current candidate before assigning code changes.

Scope: `web/src/main.tsx`, `web/src/useNotebook.ts`, `web/src/Navigation.tsx`,
`web/browser/phase3fb.spec.ts`, and the baseline/recovery sections of
`docs/phase3f-c-validation.md` and `docs/phase3f-d-validation.md`. Follow
`AGENTS.md` and `docs/agent-workflow.md`. Read an additional file only if necessary
to resolve a concrete question; name that question in the handoff.

Invariants: preserve the leave guard, save-before-submit behavior, receipt
authority, paused/conflict protections, uncertain execution, revision and draft
recovery. No frontend migration or new backend contract.

Method: trace toolbar, menus, cell actions, command-mode shortcuts and CodeMirror
keymaps into the controller. Distinguish stale UI eligibility from controller
locking. Identify the state that permits navigation after execution and whether
the existing test waits for it. Cite locations and concrete triggering sequences.

Budget: eight tool calls, three minutes, at most 300 words in the final handoff.
Read only. No edits, tests, services, browser, Docker, external research, skill
loading or delegation. No worker owns validation in this audit. Existing report
claims are historical evidence, not personally verified results.

Return: defects with severity, file/line and causal explanation; covered entry
points; one smallest next reproduction/check per unresolved defect; what remains
unverified. Stop after the audit. Do not declare the milestone complete or commit.
