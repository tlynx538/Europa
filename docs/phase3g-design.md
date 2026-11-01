# Phase 3G implementation brief

This document retains the original design review for context. For implementation,
the later owner directions in
[the integrated notebook brief](agent-prompts/phase3g-conversation-canvas.md)
take precedence, including canonical notebook conversations, the continuous
white mixed stream, overlay composer, inline comments, and removal of the
Discussion sidebar/header buttons. Consult
[the validation report](phase3g-validation.md) for current evidence and blockers.

This brief applies the user's approved [requirements](agent-prompts/phase3g-brief.md).
It retains React, CodeMirror, `useNotebook`, persistence and all backend contracts.
No Jupyter/Lumino migration, new product service or simulated agent activity.

## Reference and observed defects

The actual Notebook **7.6.4** reference was executed and captured in Chrome with
trusted disposable Python/IPython HTML/SVG examples. Reference and Europa captures
use the same five representative cells, 1440×900 and 1280×800 viewports, normal
zoom and device scale 1. Separate one-cell views are unexecuted. Notebook reference
code and assets are not imported into Europa. See the official
[UI anatomy](https://jupyter-notebook.readthedocs.io/en/stable/ui_components.html).

Measured reference: 42px title band, 28px menu, 32px toolbar, 64px prompt gutter,
13px Menlo-family code with 18.2px line height, 5px cell padding. Its single-cell
paper fits its content. Europa's before view has a 585px white paper at 900px
viewport height because `.notebook-surface` sets `min-height:65vh`. The source also
adds a 22px `.cell-meta` band, borders, and default `pre` margins. The screenshots
show these combining into excessive vertical space. The iframe's 220px minimum
also leaves table whitespace and can clip a 250px chart.

`main.tsx` currently adds one `Out` prompt around every cell's combined outputs,
including stdout, display tables and SVG. Jupyter reserves the result prompt for
expression results. Command selection currently tints an entire input/output
region; CodeMirror highlights inactive lines. The toolbar uses ambiguous Unicode
icons and omits the existing clipboard actions and compact kernel status.

Discussion's before view gives substantial space to raw cell IDs, administration
and metadata. Browser destructive actions sit beside ordinary row navigation.
Activity needs a true dated timeline and useful empty-state navigation.

## Target

- Keep the entire center workspace available by default, with optional panels.
  Use a content-sized notebook surface, restrained reading width around 1200px
  on large screens and room for wide outputs. No forced tall white box or cell
  cards. Align title/menu/toolbar/document consistently.
- Approximate the measured title/menu/toolbar density with recognizable original
  SVG icons, native disabled semantics and tooltips. One primary toolbar contains
  save, insert, cut/copy/paste, run, interrupt, cell type and observed kernel state.
  Advanced actions remain in File/Edit/View/Run/Kernel/Help. Keep menus single-open,
  keyboard operable and bounded. Preserve the working persistent command bar.
- Use 64px prompt gutters on desktop, reduced carefully on mobile; compact cell
  rhythm; 13px monospace input; thin command selection and distinct edit focus.
  Keep Markdown as rendered document content. Context actions appear on hover or
  focus without occupying a permanent large control strip. Preserve provenance,
  draft, conflict, pending and uncertain-execution information.
- Render each standard output with its appropriate prompt: expression result
  gets `Out [n]:`; streams, display data and traceback do not. Remove default
  paragraph-like spacing from text outputs. Contain long text and wide content.
  Keep rich HTML/SVG inside their opaque sandbox and current CSP. Use bounded
  presentation heights, not access to the untrusted iframe DOM from application
  code. Charts must be visible, not replaced by fallback text in evidence.
- Establish explicit surface/text/border/spacing/type/toolbar/gutter/focus/status
  tokens, a neutral light default and readable dark editor states. Preserve
  reduced motion and visible keyboard focus. Operational status is compact and
  subordinate to notebook content. The latest user feedback below supersedes the
  earlier roadmap-docked status placement.
- Roadmap stays task/progress first; configuration is a small disclosure. Completed
  and pending tasks stay compact, only ongoing tasks get server-derived progress
  bars, and blocked/needs-input states are clear. One useful empty state/action.
- Discussion gets a concise header, scrollable history, readable timestamps,
  compact messages/replies and cell references, useful empty state, and bottom
  composer. No invented participants or messages; preserve comment handlers.
- Projects/notebooks use focused lists, compact search/creation headers,
  breadcrumbs, hover/focus states and separated destructive row actions.
  Activity uses persisted records in chronological order with titles, timestamps,
  statuses and available references; the empty view links to useful context.

## Engineering boundaries and validation

Implement editor presentation first, then panels/browser/activity. Reuse existing
actions and do not modify `useNotebook`, API services, output sanitization rules,
runtime ownership, containerd, CNI, forwarding or CSP. The bounded menu prototype
may add the explicitly reviewed Radix primitives; unrelated dependencies remain
out of scope. No fake
restart, checkpoint timestamp or kernel state. Notebook 7 has no Europa roadmap
or discussion equivalent; compare those as Europa-specific contextual surfaces.

Review actual paired reference/before/after images, including single-cell,
executed overview, Markdown, table, chart, traceback, command/edit, contextual
panels and mobile. Viewport shots and separately labelled full-page supplements
are distinct evidence. Preserve historical screenshots exactly. Test actual UI
creation/execution/reorder/save/reopen and pending navigation. Complete frontend
build/unit, full Chrome, relevant Python and Go checks on the final candidate;
do not count infrastructure skips as passes or older results as current evidence.

## Maintainer review

**Recommendation: approve the design for implementation, with bounded constraints.** Its JSX/CSS and standard-output scope fits the approved brief without changing `useNotebook`, persistence, navigation, or the output-safety seam. This is an architectural review, not validation of a candidate; I did not run tests. The browser baseline and four new passes are user-provided evidence, not checks I verified.

- **Prompts:** `main.tsx:178` currently wraps all outputs in one `Out` gutter. Move prompt placement to individual outputs: only `execute_result` gets `Out [n]:`; streams, `display_data`, and errors do not. Preserve output order and avoid inventing an execution count, including in the conflict preview (`main.tsx:184`).
- **Safety:** Keep HTML/SVG in `Output.tsx`’s empty-sandbox iframe and retain `renderSafety.ts` sanitization and CSP. Size the iframe from outside with bounded scrolling; parent CSS cannot reliably style its opaque document. Verify table and chart visibility without DOM access or fallback substitution.
- **Interaction:** Reuse existing handlers and disabled/run guards (`main.tsx:83–87, 125–175`), not parallel toolbar state. Keep revision, draft, provenance, receipt, conflict, and uncertain-execution notices discoverable when compressing cell chrome. Preserve CodeMirror Escape/blur behavior, menu and dialog focus restoration, `Layout.tsx`’s portal/docking and mobile dialogs, and the pending-navigation guard (`main.tsx:198–202`). Do not regress nearest-cell scrolling or the persistent command bar.

No migration is needed. The final candidate still needs the brief’s visual and functional validation; prior browser results cannot establish that candidate’s correctness.

## Live preview feedback — authoritative update

The user reviewed the running preview and requested fixed, full-width chrome:
title/menu/toolbar at the viewport top and one status/activity bar across the
entire bottom edge, including when contextual panels are open. The footer must
no longer dock inside the roadmap or look like a small rounded card.
The latest follow-up moves Projects and Notebooks into File: **Open project…**
opens the project browser, each project lists its linked notebooks, and **Open
notebook…** opens the existing notebook library. Remove the standalone header
links; the title and menubar align left with modest edge padding. Saved/revision
information remains secondary at the right. The document reading width is
independent of these full-width bars. Reuse membership records and guarded routes;
do not introduce a second project model or unsupported file-tree behavior.

Floating dropdowns must use compact proper menu rows rather than bordered button
stacks. Adopt the React 19-compatible Radix Menubar primitive for the six top-level
menus, with its portal placement, keyboard navigation, checkbox/radio items and
focus handling. The pinned version selected for implementation is 1.2.1; its
published peer range includes React 19. This is a bounded menu dependency, not
a frontend migration. Review actual CSP, focus and bundle results before accepting.

File must clearly offer **Open project…**, **Open notebook…** and **Close notebook** using
the existing guarded navigation/controller close flow. Preserve unsaved-draft
confirmation and pending-execution refusal; do not bypass them with a full-page
URL replacement. Keep the already verified internal notebook scroll viewport.

Further owner review requests compact, consistently aligned title/menu/toolbar
rows, and moves the roadmap's Project settings disclosure into File. Settings
open in one accessible dialog over a dark blurred backdrop, with supported
project context, notebook membership information, roadmap/planning and history
controls grouped clearly. Existing project management remains reachable through
guarded navigation. Preserve mutation IDs, revisions, membership checks and
pending-work refusal; do not invent unsupported settings. Empty roadmap actions
open this same dialog. Verify focus return, Escape, mobile bounds and measured
header/footer offsets on the resulting candidate.
