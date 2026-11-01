# Phase 3G update — integrated notebook conversation

Current focused frontend change: Comment/Reply now use an inline multiline editor
with locally scoped drafts and exact pending-operation retry; persisted comment
threads show real server-owned authorship, times and readable widths. This does
not implement automatic agent replies: real agent turns and execution remain the
future engine scope in `docs/notebook-agent-interaction.md`. Focused desktop and
mobile comment checks passed; complete milestone acceptance remains open in
`docs/phase3g-validation.md`. Read its current evidence before running more tests.

Owner-approved scope: integrated UI now; real agent engine next. This supersedes the separate Discussion sidebar design. Retain React/CodeMirror/useNotebook, actual .ipynb persistence, server-assigned authorship, receipts/revision/draft/recovery/interrupt protections. No Phase4 orchestration, invented responses/plans or autoexecution. Latest owner clarification authorizes the minimal durable notebook-conversation lifecycle needed below; unrelated backend contracts remain unchanged.

The notebook is one shared canvas: user messages, persisted server-authored replies when present, executable notebook cells and outputs. A floating composer stays fixed just above the fullwidth footer during notebook scrolling. Existing messages remain in the application store, real code/Markdown cells remain in .ipynb. No message becomes executable code.

Users explicitly discuss/attach a selected cell; a shadowed removable reference chip above the textarea shows its cell number and source excerpt. It must target a stable cell ID, retain revision context, flag drafts/missing references, and never misrepresent unsaved text as saved/executed. Clicking/removing reference must not mutate/execute notebook cells.

The left roadmap becomes an ordered chapter/progress timeline: vertical rail and status markers, plan order, completed/current/upcoming/blocked/needs-input states, overall progress, individual bars only ongoing server tasks, navigation to related cells. Progress and task order come from persisted records; no fabricated agent work. Existing task editing/evidence/acceptance handlers remain.

Remove the right Discussion sidebar as the primary conversation surface. Preserve discussion replies, needs-input state, cell comments and orphaned/outdated history within the notebook context. Remove header Discussion and Roadmap buttons; View controls the optional roadmap. The composer remains directly available.

Composer must reserve measured space so final cells/messages stay readable; remain above footer with Roadmap open, at1440/1280/390, and during resize. Avoid rerendering all CodeMirror editors per typed character. Use current safe application mutations, exact pending-operation retry, server roles, membership checks and stored timestamps. Keep unsent message drafts across refresh/switch or clearly protect them; successful exact retry must not leave a resend-ready duplicate draft.

New and legacy conversations must respect notebook/project scope; messages from another notebook must not leak into the current canvas. Define deterministic display ordering using existing links/record order without inventing cell creation timestamps. Document any future canonical cross-type chronology seam for the agent phase.

Validate real API/browser message persistence, composer position before/after scroll and resize, cell reference send/reload/remove/missing/draft behavior, no execution POST from chat, replies/comments/history, pending replay, CodeMirror keyboard focus, roadmap navigation/timeline states and existing safety regressions. Capture actual screenshots and run frozen-candidate frontend/build/fullChrome; unchanged backend Python/Go evidence may be reused explicitly. Single OpenCode worker and one heavy serial validation job; Node24, Vitest/Chrome workers1, retries0.

## Authoritative owner clarifications

- Exactly one continuous notebook-owned conversation: initialized when a notebook is created and resumed when reopened. Remove New conversation and the conversation selector; no user-created parallel threads. Restore unsent draft and conversation position safely. Existing data/history must not be deleted or silently attributed to another notebook. A minimal idempotent persistence/API lifecycle extension is authorized to make this work for standalone and project-linked notebooks; this is not an agent engine.
- Remove both Roadmap and Discussion header buttons. View → Roadmap opens/closes the left outline; the floating notebook composer remains available directly. Preserve native mobile drawer dismissal and focus.
- Composer should look floating with an actual gap above the footer and rounded edges, not a docked form. Real saved messages use chat-bubble hierarchy; code cells retain notebook anatomy. Always provide a meaningful composer/loading/error state, even before any messages exist.

## Latest owner visual override

Keep File/Edit/View/Run/Kernel/Help as a compact Radix menubar with trigger-local, inset keyboard focus. Filter immutable execution receipts beneath each cell's output (or source without output), with an orphan-receipt fallback; keep pending/uncertain/conflict notices visible and the View command able to reveal history. The composer shares the notebook paper's horizontal bounds, not an arbitrary 920px cap. The latest clarification requires a viewport-pinned *overlay inside the notebook column*, not a separate flex-flow dock: notebook cells and chat scroll behind it, with one measured height reserve in the scroller so the final cell can scroll above it. White paper extends under the capsule and all the way to the footer at every scroll position, including short and empty notebooks; no gray strip behind the chat. Outer page gutters can remain neutral; the capsule stays rounded, with its gap inside the white paper. This supersedes the earlier floating-gap treatment where that gap exposed the gray canvas.

## Active stream placement clarification

The server assigns immutable new-message placement after the last saved cell.
On first active canonical conversation view, message-list view, or append, it
also stores a one-time separate layout snapshot for earlier unplaced messages.
That snapshot is not a claim about their original creation chronology and must
not change their immutable records. Toolbar Add cell appends at the notebook end
even when an earlier cell is selected; explicit Insert above/below stays relative.
Active bubbles show compact local hour/minute time (full timestamp in tooltip),
not internal legacy-placement diagnostics. Missing anchors remain historical.

## Cell action menu

Per-cell actions use the existing Radix dropdown primitive as compact menu
items, not a disclosure of full-size buttons. The visible cell trigger has a
cell-numbered accessible label; one menu opens at a time and dismisses on
Escape, outside click or selecting an item. Escape restores the trigger while
Discuss cell dismisses and focuses the composer with its cell reference.
Visible move arrows stay outside the menu; disabled execution, saving and
mutation guards retain their existing controller conditions.

## Inline comments — latest owner requirement

Replace native Comment/Reply prompts with a properly sized inline multiline
editor and readable comment threads beneath cells. Keep posting and retry on the
existing scoped mutation path. Preserve drafts on cancel/failure, clear only
matching acknowledgements, and distinguish new saved-cell anchors from replies
that inherit historical anchors. Retain navigation, outdated/orphaned/resolved
states and real author roles. Never fabricate agent replies; the next engine
milestone follows [the recorded turn contract](../notebook-agent-interaction.md).

Keep comment cards comfortably sized even for short text: current target is a
720px maximum width, 150px minimum height, 14px body text with 1.5 line height,
and clearly separated Resolve/Reply actions. Inline textareas have a 120px
minimum height and can resize vertically. Use full available width on narrow
screens, wrap actions, and keep send/cancel controls reachable above the main
composer. These are responsive bounds, not fixed desktop dimensions on mobile.
Comments and replies stay embedded under their cell/thread; never restore native
JavaScript prompt dialogs. Agents may respond only through real persisted,
server-assigned authorship when an engine is implemented.
