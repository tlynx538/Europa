# Integrated notebook conversation — architecture

Validated editor/settings checkpoint: `3d4a771`. The owner confirmed **integrated UI now; agent engine next**.

**Recommendation: approve the UI-only architecture, with a scope-filtering constraint.** I inspected the named files and repository guidance. I made no changes or ran tests: this Maintainer role is read-only, so I could not write `docs/phase3g-conversation-design.md`. Poseidon should route that single documentation edit to the Coder.

The document should specify one integration:

- `useNotebook` in `main.tsx` remains the sole notebook controller; `Workspace` remains the sole owner of project, conversation, message, comment and roadmap data. Use a Workspace render-prop/slot to supply notebook-scoped message groups and a composer to App’s existing cell mapping. **Do not add a second fetch provider.** Isolate textarea/draft state below the editor render path so typing does not rerender CodeMirror cells.
- Project the canvas deterministically from current `doc.cells` order and the message list’s persisted order. Place messages with a valid current-notebook `cell_id` beside that cell, preserving message order within each group; place notebook-scoped messages without a cell in a separate ordered group. Keep missing-cell references visible and labelled, not silently reattached. Do not infer cross-type chronology from nonexistent cell timestamps; leave canonical chronology for the engine phase.
- Filter by the selected conversation’s project and notebook scope **and** each message’s notebook link before rendering. The current Workspace loads project-wide conversations and renders every selected conversation message (`Workspace.tsx:37–42, 69–72, 132–133`), so moving that rendering unchanged would leak other-notebook context. Define an explicit, labelled treatment for legacy project-scoped messages rather than silently treating them as current-notebook messages.
- Put the removable, explicitly selected cell-reference chip above the fixed composer; show current cell number and source excerpt, stable ID, saved document revision, and a clear unsaved/missing warning. Send supported `notebook_id`, `cell_id`, `document_revision` links only when valid; never claim draft source was saved or executed. Keep composer above the full-width footer with measured clearance in the scrolling editor at desktop and mobile widths.
- Preserve conversation selection, replies and `needs_input`, cell comments and their outdated/orphaned history, receipt history, and existing task edit/evidence/acceptance handlers. Replace the right Discussion panel and its controls with a discoverable composer-focus action; retain the optional left Roadmap as an ordered task timeline using persisted task order, statuses and server-derived progress. Show individual progress bars only where `show_progress` and `in_progress` warrant them—not simulated live agent work.
- Preserve `change`/`retryMutation`’s exact persisted operation body and ID (`applicationApi.ts:28–49`), membership recheck (`Workspace.tsx:79–89`), notebook autosave/revision/receipt protections, and navigation pending guard (`main.tsx:180–184`). Persist or explicitly protect unsent drafts across refresh and conversation/notebook switches. After an exact retry succeeds, reconcile the sent draft so it cannot be sent again.
- Public message POSTs must not supply authorship: REST assigns `user`; display other roles only on trusted stored records (`models.py:54–59, 134–160`; `conversational-notebooks.md:146–150`). No backend changes, chat cells in `.ipynb`, execution from chat, fabricated agent responses, or invented plan states.

**Implementation validation ownership:** one frontend test owner should cover scope filtering, real message persistence and retry/reload, chip saved/draft/missing/remove cases, comments and replies, editor focus and keyboard behavior, timeline navigation, composer geometry after scroll/resize, and absence of chat-triggered execution POSTs. Run the frozen-candidate frontend/build/browser checks serially under the approved runtime; reuse unchanged backend evidence only with its limitations stated. None of those checks was run in this review.

This is a source-based architecture recommendation, not implementation validation. The supervisor recorded this read-only Maintainer handoff; no backend or agent engine is authorized in this UI checkpoint.

## Subsequent owner clarification — supersedes thread selection

The owner requires one notebook-owned persistent conversation, created with the notebook and resumed on reopen. New conversation controls/selectors are removed. Support standalone notebooks without inventing a project. Preserve existing histories through a documented conservative compatibility policy. A minimal lifecycle/storage extension is authorized; actual agent orchestration remains next phase. Roadmap/Discussion header buttons are removed; View controls the optional left roadmap. The composer floats above the footer with a gap, and saved messages receive bubble presentation.

## Stream placement clarification

New canonical notebook messages capture an immutable server-assigned anchor after
the last saved cell in the commit transaction; an empty notebook uses a null
anchor. Projection renders before-first-cell messages, then cells in saved order
and messages anchored after each cell, with no separate Discussion section.
The explicit selected-cell reference remains independent of placement. Deleted
anchors remain labelled historical. No cross-type event chronology is inferred.

The active canonical thread now receives a one-time server-owned layout snapshot
for pre-placement messages on first view or append. This records where the
message is displayed in the then-current saved notebook, not when it was
created, and does not rewrite immutable message data. The toolbar Add action
appends after the current saved final cell even when another cell is selected;
explicit per-cell insertion remains relative. Active chat bubbles show local
hour/minute time, with full timestamp available on hover.
