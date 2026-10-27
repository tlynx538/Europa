# Europa Release Notes

This file records user-visible changes and milestone history. The
[README](README.md) is intentionally kept as an evergreen project and setup guide.
Detailed implementation contracts and validation evidence remain in `docs/`.

## Unreleased

Record future user-visible changes here rather than adding milestone history to
the README. Move entries into a named release section when they are published.

## Phase 3E — Owner-facing notebook UI

**Commit:** `cb33325`

**Merged to main:** `71b2596`

Phase 3E completed the initial owner-facing browser workspace.

### Added

- Responsive notebook workspace with resizable desktop sidebars and modal mobile
  drawers
- CodeMirror Python editor with Shift+Enter execution and Cmd/Ctrl+S saving
- Revision-serialized autosave, durable browser drafts, and explicit conflict review
- Automatic saved-output refresh and durable receipt reconnection
- Verified receipt-based interrupts that remain server-owned after disconnection
- Conflict-output inspection and explicit unchanged-source recovery with provenance
- Full roadmap task editing, owner acceptance evidence, threaded conversations,
  cell comments, anchor navigation, and contextual activity
- Safe Markdown and rich-output rendering with sanitization, sandboxed iframes,
  raster validation, and a nonexecuting static Plotly subset
- Production Vite build served by FastAPI with CSP, loopback Host validation,
  same-origin mutation checks, no-referrer, nosniff, and no-store API responses
- Browser, restart, reconnect, conflict, interrupt, output-safety, operation-retry,
  anchor, and ARM64 runc tests

### Validation

- Frontend build passed
- 11 frontend unit/component tests passed
- 240 Python tests passed; 103 infrastructure-gated tests skipped
- 24 focused real-local Gateway tests passed
- 11 production Chrome tests passed; the separate VM test was explicitly skipped
- Separate ARM64 browser/runc test passed
- Separate ARM64 receipt/session/interrupt probe passed
- Final VM audit found no surviving Europa tasks, containers, managed snapshots,
  state directories, IPAM allocations, or probe processes

See [Phase 3E UI](docs/phase3e-ui.md) for behavior, limitations, screenshots, test
commands, and integration observations.

## Phase 3D — Persistent workspace UI

**Commit:** `1ac18a6`

- Connected the browser preview to persistent projects, roadmaps, conversations,
  comments, and notebook membership
- Added the initial owner workspace layout and application API integration

## Phase 3C — Initial notebook UI

**Commit:** `4868302`

- Added the first React/Vite notebook editor preview
- Connected basic notebook creation, editing, execution, and output display

## Phase 3B — Conversational notebook foundation

**Commit:** `2009a9e`

- Added private SQLite persistence for projects, conversations, messages, versioned
  roadmaps, cell comments, and activity history
- Added idempotent application operations and revision-checked mutable records

See [conversational notebooks](docs/conversational-notebooks.md) and the
[Phase 3B validation report](docs/phase3b-validation.md).

## Phase 3A — Notebook persistence

**Commit:** `a5f715d`

- Added standard `.ipynb` persistence, revision-checked cell APIs, durable execution
  receipts, and saved Jupyter outputs
- Kept notebook storage independent from runtime filesystems

See [notebook persistence](docs/notebook-persistence.md).

## Phase 2 — Managed runtimes

Phase 2 introduced and incrementally hardened runtime management:

- **2A:** asynchronous local runtime provider and notebook supervisor
- **2B:** optional containerd provider and ARM64 runc validation
- **2C:** resource profiles and single-node admission control
- **2D:** durable ownership registry and conservative recovery
- **2E:** clean, capacity-aware runtime prewarming
- **2F:** durable activity tracking, leases, and idle cleanup

Detailed reports:

- [Runtime provider](docs/runtime-provider.md)
- [Containerd provider](docs/containerd-provider.md)
- [Resource admission](docs/resource-admission.md)
- [Runtime recovery](docs/runtime-recovery.md)
- [Runtime prewarming](docs/runtime-prewarming.md)
- [Runtime lifecycle](docs/runtime-lifecycle.md)

## Earlier migration

Europa migrated kernel execution to Jupyter Kernel Gateway while preserving the
standard Jupyter HTTP/WebSocket protocol. See [Phase 2 review](docs/phase2-review.md)
for historical migration context and limitations.
