# Phase 4 implementation plan

Starting baseline: `phase3h-complete`, main `c5a8a603ddef020d42f802c15896174d11678287`.
Branch: `feature/phase4-agentic-harness`. No Phase 4E/F work is authorized.

1. **4A:** schema/run/event/approval/effect journal, scopes, provider/fake,
   bounded context and read tools, fenced runtime, cancellation/recovery and APIs.
   Validate backend security/reliability, frontend unit/build, browser API journey
   and complete Phase 3H regressions. Commit only on passing mandatory gates.
2. **4B:** compact Home Ask Europa entry and persisted conversation/activity client;
   exact approval-bound existing project/notebook creation and linked handoff.
   Validate denial/replay/collision/restart and existing Files/Running; commit.
3. **4C:** notebook's canonical inline conversation, approved versioned roadmap,
   dependency/scope/CAS checks and durable plan/activity UI. Validate concurrent
   edits, isolation and refresh/restart, then commit.
4. **4D:** bounded supervised Coding Agent delegation, structured cell proposals,
   exact edit/execute approvals, real Gateway receipts, bounded repair and owner
   interruption/reconciliation. Validate complete Home-to-Markov workflow, saved
   state/history, real execution and full regressions; commit and stop.

One worker and one heavy validation job at a time; Node 24, `.venv`, one Chrome
worker and zero retries. Use disposable roots and keep owner previews untouched.
Routine provider tests are deterministic and free. No real paid provider smoke is
planned without configured credentials and an explicit spending limit.
