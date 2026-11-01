# Notebook agent turn interaction

Owner direction, 2026-10-31. This is the interaction contract for the next agent
engine milestone. Phase 3G implements the integrated notebook UI and persisted
user messages; it does not currently generate agent responses or execute agent
plans. Do not populate the UI with simulated activity to imply otherwise.

## One inline turn

A turn belongs in the same notebook stream as cells and outputs:

1. The user's message, including any explicitly attached cell reference.
2. A compact, expandable progress panel with concise activity summaries and real
   tool events, such as reading a cell, applying a change or awaiting execution.
3. The agent's persisted reply.
4. The affected or newly created notebook cells and their authoritative outputs.

The progress panel stays expanded during work and automatically collapses to a
short completion summary when the result arrives. Its activity remains available
on expansion. It presents useful progress and tool records rather than claiming
to reveal private model reasoning. No progress window should imply that work is
running before the server has actually started a turn.

The composer remains a floating overlay above the footer on the continuous white
notebook canvas. A turn is not sent to a separate Discussion section or sidebar.
Chat metadata shows a compact local time, retaining the machine timestamp.

## Cell targeting and navigation

An explicit attached cell is the initial context. Otherwise the engine identifies
the relevant existing cell, or creates a new cell when appropriate. A confirmed
existing target is brought into view in the notebook, with a clear turn/cell
association. The user can revisit it through a cell reference. Avoid duplicating
an existing executable cell merely to display it beneath a reply.

New cells follow the turn's reply in the mixed stream. Existing-cell updates and
the associated turn must be presented coherently when navigating to that cell;
the engine design must define persisted turn placement, independently of context
references, without fabricating cell timestamps or rewriting message history.
Retain the user's draft, editor focus and scroll intent while navigating; a
reference must never silently target a different cell after deletion or reorder.

## Persistence and correctness boundaries

Store turn identity, progress/tool events, reply and affected-cell references with
notebook scope and server-assigned authorship. Restore completed and interrupted
turns on reopening. The left roadmap reflects real saved planning/task progress
from the same work, not synthetic activity.

Cell writes retain revision checks and draft conflict review. Execution uses the
existing authoritative receipt lifecycle, including reconnect, uncertainty and
interrupt behavior. A successful reply or tool event is not evidence that code
executed: show execution results only from the corresponding server receipt.
Engine integration, streaming, cancellation, provider selection and concrete turn
API/storage contracts require the next milestone's implementation and tests.
