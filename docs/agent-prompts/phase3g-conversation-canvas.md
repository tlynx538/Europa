# Phase 3G update — integrated notebook conversation

Owner-approved scope: integrated UI now; real agent engine next. This supersedes the separate Discussion sidebar design. Retain React/CodeMirror/useNotebook, actual .ipynb persistence, server-assigned authorship, receipts/revision/draft/recovery/interrupt protections. No Phase4 orchestration, invented responses/plans, autoexecution or backend contract expansion.

The notebook is one shared canvas: user messages, persisted server-authored replies when present, executable notebook cells and outputs. A floating composer stays fixed just above the fullwidth footer during notebook scrolling. Existing messages remain in the application store, real code/Markdown cells remain in .ipynb. No message becomes executable code.

Users explicitly discuss/attach a selected cell; a shadowed removable reference chip above the textarea shows its cell number and source excerpt. It must target a stable cell ID, retain revision context, flag drafts/missing references, and never misrepresent unsaved text as saved/executed. Clicking/removing reference must not mutate/execute notebook cells.

The left roadmap becomes an ordered chapter/progress timeline: vertical rail and status markers, plan order, completed/current/upcoming/blocked/needs-input states, overall progress, individual bars only ongoing server tasks, navigation to related cells. Progress and task order come from persisted records; no fabricated agent work. Existing task editing/evidence/acceptance handlers remain.

Remove the right Discussion sidebar as the primary conversation surface. Preserve discussion replies, needs-input state, conversation selection, cell comments and orphaned/outdated history within the notebook context. Header Discussion may focus the composer rather than open a sidebar; preserve discoverability without redundant toolbars. Roadmap remains optional.

Composer must reserve measured space so final cells/messages stay readable; remain above footer with Roadmap open, at1440/1280/390, and during resize. Avoid rerendering all CodeMirror editors per typed character. Use current safe application mutations, exact pending-operation retry, server roles, membership checks and stored timestamps. Keep unsent message drafts across refresh/switch or clearly protect them; successful exact retry must not leave a resend-ready duplicate draft.

New and legacy conversations must respect notebook/project scope; messages from another notebook must not leak into the current canvas. Define deterministic display ordering using existing links/record order without inventing cell creation timestamps. Document any future canonical cross-type chronology seam for the agent phase.

Validate real API/browser message persistence, composer position before/after scroll and resize, cell reference send/reload/remove/missing/draft behavior, no execution POST from chat, replies/comments/history, pending replay, CodeMirror keyboard focus, roadmap navigation/timeline states and existing safety regressions. Capture actual screenshots and run frozen-candidate frontend/build/fullChrome; unchanged backend Python/Go evidence may be reused explicitly. Single OpenCode worker and one heavy serial validation job; Node24, Vitest/Chrome workers1, retries0.
