# Phase 3H — owner-confirmed navigation and visual model

**Superseded on 2026-11-10:** the owner now requests a Jupyter-style Files/Running
dashboard at `/`, replacing the welcome/project-welcome structure described here.
The storage-safety constraints and deferred Quit/Logout still apply. See
[the reconstruction checkpoint](phase3h-home-dashboard-validation.md) for current
implementation and visual review evidence. This historical design is retained
to preserve the context of the uncommitted H1 work.

This records the owner's answers Q1–Q27 in the Phase 3H conversation. It supersedes the provisional H1 root file-manager layout; it does **not** establish that source code implements the design. See [the implementation brief](agent-prompts/phase3h-brief.md) and [validation](phase3h-validation.md).

## Journeys

| Entry | Action | Result | Side effects and failure |
| --- | --- | --- | --- |
| Open Europa at `/` | Start new project/notebook, open a recent item, or choose Browse below recents | VS Code-style **welcome** using Europa's existing theme; not a forced editor/runtime | No kernel starts just to show Home; listing failures do not fabricate stopped sessions. |
| Browse from Home | Open ordinary directory | Jupyter-style compact directory listing and breadcrumb; Back/Forward/refresh preserve location | Real existing files under the selected launch directory, never arbitrary paths outside that root. Inaccessible, linked or missing entries show an honest error and recovery route. |
| Browse | Open registered project directory or its project descriptor | **Project welcome**, with project-scoped recent notebooks and Browse files | Only contents beneath the project directory belong to it; no external references. A descriptor copied from elsewhere is not an ownership grant. |
| Project welcome | Browse files / Back to Europa Home | Directory listing / main welcome | Back control belongs at the upper-left of the page content, not the header. |
| Select `.ipynb` | Open existing Phase 3G notebook canvas | Notebook editor and its durable document/conversation | Explicit open updates recent order; no need to provision a runtime before execution. Missing/deleted notebook has a recovery route. |
| Select `.txt`, `.py`, `.md`, supported UTF-8 text | Edit, Save, Close | Separate simple editor; saved bytes reflect a revision-checked commit | A draft is temporary, not a commit. External file changes trigger review/conflict; `.py` is never automatically executed. Binary/oversized/unsafe files are not silently treated as text. |
| Notebook File menu | **Close Notebook** (last item; no Return Home) | Verified parent project welcome or main Home | Shared leave guard handles browser drafts/conflicts/receipts. Cancel keeps editor and drafts; success leaves running kernel alone. Europa wordmark always goes Home through the same guard. |
| Notebook Kernel menu (H2) | Start, Stop, Restart | Observed notebook-scoped runtime lifecycle | Start without running a cell; Stop/Restart warn that Python variables may be lost. Never infer success from request acknowledgment or expose kernel IDs. Active/uncertain work blocks unsafe teardown. |

## Real filesystem and existing data

Launch from a chosen working directory; Browse remains within that pinned root and descendants, using the launching account's existing OS permissions. No browser button pretends to grant server filesystem permission. New folders, text files, notebooks, project directories and descriptor/configuration files are **real disk items** that external tools can find. A project is a directory with a validated Europa project file and only its contained files/subfolders as members. Moving an item into or out of that project updates both real location and project association without deleting content. Existing private notebooks/projects/receipts remain in place until the user explicitly requests a verified move; automatic migration on startup is forbidden. No upload/download feature or arbitrary runtime mount is part of this milestone.

## Visual rules

- The owner supplied a VS Code welcome reference for the hierarchy of Start, Recent and Browse; do not copy its branding, walkthroughs, other panes, or dark default. Keep Europa's current light/dark theme and Phase 3G notebook interface.
- The owner supplied classic Jupyter dashboard reference for Browse: dense, keyboard-accessible folder/file rows, a breadcrumb/path, and clear actions; not a statistics/card dashboard. Folder icon means an ordinary directory; a project affordance leads to project welcome.
- Remove the obsolete three-way **Projects / Notebooks / Activity** top-level competition. The wordmark is persistent Home. Quit and Logout appear at the upper-right of **main Home and project welcome**, visibly disabled with “Coming later”; active no-op controls would mislead. Functional Quit/Logout were explicitly deferred *after* 3H, superseding the original H3 shutdown completion criterion. Legacy bookmarked routes can remain compatible.
- Desktop and 390px mobile views need actual browser captures and visual inspection; screenshots of the unfinished candidate are not final acceptance.
