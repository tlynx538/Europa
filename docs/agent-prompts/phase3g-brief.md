# Europa Phase 3G — Jupyter-Native Notebook UX and Design

## Starting point

Repository: Europa

Base: Latest verified Phase 3F-D working branch.

Create branch: `feature/phase3g-notebook-ux`.

Before implementation, verify Git status and inspect the complete Phase 3F-D report.

If the outstanding full browser-suite regressions remain unresolved, resolve or explicitly isolate them before making extensive UI changes. Do not claim old test results as validation of the new revision.

## Objective

Make Europa visually and interactively comparable to the classic Jupyter Notebook 7 experience, while retaining Europa's distinct project, roadmap, discussion, and runtime services.

The current UI remains too visually primitive and contains unnecessary controls, oversized whitespace, weak notebook cell affordances, and overly prominent administrative interfaces.

This milestone is not about adding features. It is about improving the existing user experience to a high-quality standard.

Retain React, TypeScript, CodeMirror, `useNotebook`, and Europa's backend APIs.

Do not migrate to JupyterLab/Lumino.

## 1. Reference-driven implementation

Study the actual Jupyter Notebook 7 editor in a browser at a consistent desktop viewport.

Reference:
https://jupyter-notebook.readthedocs.io/

Examine:
- Notebook page anatomy
- Title and checkpoint/save indicators
- Menubar and toolbar spacing
- Cell gutters and execution prompts
- Code and Markdown cell presentation
- Focus and command/edit states
- Output formatting
- Vertical spacing between cells
- Toolbar icons and disabled states
- Scrolling and viewport behavior

Capture reference screenshots using a disposable notebook with representative code, Markdown, tables, plots, and errors.

Do not copy Jupyter trademarks, logos, or incompatible assets.

The goal is equivalent interaction quality and visual hierarchy, not pixel-perfect cloning.

## 2. Notebook viewport — Highest priority

Make the notebook area feel like an actual document workspace.

Requirements:

- Full-width notebook workspace by default.
- No compulsory roadmap or discussion sidebar.
- Appropriate reading width for ordinary code and Markdown.
- Wide tables and charts may use available width.
- Subtle boundaries around active or selected cells.
- No large enclosing empty white panel around a small cell.
- No dashboard-card styling.
- Code and Markdown flow naturally down the document.
- Consistent and deliberate vertical rhythm.
- Output immediately follows its corresponding cell.
- Long outputs remain contained without breaking the page.

Remove visually unnecessary containers, padding, borders, and wrappers.

The notebook should not look empty merely because it contains only one code cell.

## 3. Cell presentation

Match familiar Jupyter cell anatomy.

Code cells should include:

- `In [ ]:` or `In [n]:` prompt in the left gutter.
- Properly aligned multiline CodeMirror editor.
- Visible selection state.
- Distinct command/edit focus modes.
- Syntax highlighting compatible with the active theme.
- Output prompts for appropriate result types.
- Stdout directly below the cell.
- Clean traceback presentation.
- Compact contextual cell actions.

Markdown should render directly as notebook content rather than inside a permanent editor box.

Use accessible hover/focus affordances for inserting or moving cells.

Do not display permanent Save Cell, Run, Delete, and Move controls on every cell.

Maintain keyboard and screen-reader discoverability through the existing toolbar and menus.

## 4. Menubar and toolbar

Use a restrained hierarchy:

Notebook title
Menu bar
Toolbar
Notebook canvas

Preserve:

File · Edit · View · Run · Kernel · Help

The menubar must appear as lightweight text navigation, not six large outlined buttons.

Menus must:
- Open one at a time.
- Remain within viewport bounds.
- Support keyboard navigation.
- Dismiss on Escape and outside click.
- Contain concise actionable items.

The notebook toolbar should use recognizable icon controls with consistent sizing and accessible tooltips.

Keep the main actions:

- Save
- Add cell
- Cut/copy/paste
- Run
- Interrupt
- Cell type selector
- Kernel status

Do not introduce a second competing toolbar.

Keep advanced actions inside menus.

Do not make a nonfunctional Restart button merely to imitate Jupyter.

## 5. Roadmap redesign

The roadmap is an optional contextual view, not a project administration screen.

When a roadmap is present:

At the top:
- Roadmap title.
- Overall progress with percentage aligned right.
- Overall progress bar.

Below:
- Ordered task list.
- Compact completed tasks with checkmarks.
- Compact pending tasks.
- Individual progress bar only for active tasks.
- Clear blocked and needs-input statuses.
- Linked cell navigation.

At the bottom:
- Compact runtime/connection/activity status.

Move project selection and roadmap configuration into a small contextual menu or settings disclosure.

Do not allow administrative controls to dominate the panel.

When no roadmap exists, display one useful empty state and a clear action.

## 6. Discussion panel

The discussion panel should resemble a focused messaging interface.

Requirements:
- Clear conversation header.
- Scrollable message history.
- Compact user message styling.
- Readable timestamps.
- Contextual cell references.
- Replies and needs-input indicators.
- Fixed bottom composer.
- Useful empty state.

Don't display a large form with many fields simultaneously.

Do not invent Coordinator or Coding Agent activity.

## 7. Projects and notebooks browser

Use Jupyter's file browsing experience as an information-architecture reference.

Improve:
- Header hierarchy.
- Search placement.
- List spacing.
- Notebook and project rows.
- Row hover and selection.
- Creation actions.
- Empty states.
- Navigation breadcrumbs.

Avoid placing destructive actions such as Delete next to primary creation/navigation controls without appropriate separation.

Favor focused lists over excessive cards.

## 8. Activity page

Implement two distinct presentation states.

Empty:
- Explain that no recorded activities exist.
- Provide a way to navigate to the project or notebook.
- Avoid showing a blank divider beneath a selector.

Populated:
- Chronological activity timeline.
- Meaningful event titles.
- Timestamps.
- Notebook/task/cell references when available.
- Clear status differentiation.

Use existing persisted activity records.

Do not invent events or mistake saved notebook receipts for agent activity.

## 9. Design system

Establish explicit tokens for:
- Backgrounds
- Notebook surface
- Text and secondary text
- Borders and separators
- Spacing
- Typography
- Toolbar density
- Editor gutters
- Interactive focus
- Success/error/warning
- Light and dark editor themes

Default to a neutral light theme.

Keep the interface consistent across Projects, Notebooks, Activity, and notebook editing.

Avoid visually prominent borders and high-contrast containers unless conveying selection, focus, or error.

Respect accessibility contrast, keyboard navigation, and reduced-motion settings.

## 10. Maintain execution correctness

All UI changes must preserve:

- Standard `.ipynb` files
- Document revision checks
- Autosave and local drafts
- Conflict review
- Execution receipts
- Reconnect and uncertain execution handling
- Notebook switching guards
- Shared run eligibility checks
- Receipt-aware interruption
- Safe MIME rendering
- CSP and same-origin restrictions
- Project, roadmap, and comment persistence

Never prioritize visual similarity over execution correctness.

## 11. Visual quality gates

This milestone MUST use real browser screenshots and human-readable visual comparisons.

Capture equivalent Jupyter Notebook 7 and Europa views with:

1. One simple code cell
2. Several executed cells
3. Markdown and code together
4. A table output
5. A plot output
6. A Python traceback
7. A selected cell in command mode
8. A focused cell in edit mode
9. Notebook with roadmap open
10. Notebook with discussion open

Also capture project browser, activity empty/populated states, and mobile layouts.

At a minimum, inspect desktop widths 1440px and 1280px, plus a narrow viewport.

For each visual defect:
- Record what is wrong.
- Identify its responsible component or CSS rule.
- Fix it.
- Capture a new screenshot.
- Compare again.

Do not stop at a successful TypeScript build.

Do not claim visual parity based on screenshots of Europa alone.

## 12. User workflow validation

A real user should be able to:

1. Open the notebook library.
2. Create a notebook.
3. Enter code and execute it.
4. Insert a Markdown cell.
5. Reorder cells.
6. Save and reopen the notebook.
7. Review a traceback.
8. Open and close the roadmap.
9. Open and close discussion.
10. Return to the notebook library.

This workflow must be understandable without prior knowledge of Europa's internal architecture.

Test with actual browser interaction, not only mocked components.

## 13. Technical validation

Run:
- Production frontend build
- Frontend unit tests
- Full Chrome browser suite
- Relevant Python regression tests
- Go tests
- Safe rich-output checks
- Keyboard/focus tests
- Pending-execution navigation tests

Investigate the outstanding Phase 3F-D full-suite issues first.

If environment limitations prevent Docker or ARM64 tests, report that explicitly. Never count unavailable tests as passes.

No changes to containerd, CNI, or forwarding configuration.

## 14. Deliverables

Create `docs/phase3g-validation.md` containing:

- Starting commit and branch
- Visual defects identified
- UI architecture changes
- Before/after screenshots
- Jupyter reference comparisons
- Functional test results
- Browser regression results
- Accessibility observations
- Known limitations

Commit changes on the feature branch.

Stop after Phase 3G.

Do not start Phase 4 agent orchestration.

## Acceptance standard

Phase 3G is not complete merely because the UI contains Jupyter-like menu names and execution gutters.

It is complete only when the notebook feels coherent, familiar, uncluttered, visually polished, and reliably usable, and when the known correctness regressions have been resolved or explicitly reported as blockers.
