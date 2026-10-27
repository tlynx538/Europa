# Phase 3A safety review before Phase 3B

Reviewed at base `a5f715d970f4ba7ab9497a51d675c7d0ae28c40c` before application
implementation: notebook store/service/output reducer, runtime registry/provider,
Go OCI spec, architecture documents and notebook/runtime tests.

## Project workspace boundary

Notebook documents and receipts live in a private durable root outside runtimes.
Descriptor-relative opens reject traversal, symlink components, nonregular files,
foreign owners, public permissions and hardlinks. Documents are limited to 16 MiB.
The containerd adapter refuses host working-directory mounts. Its OCI spec uses
UID/GID 10001, no capabilities, no-new-privileges, read-only root, CPU/memory/PID
limits and four private 0700 noexec/nodev/nosuid tmpfs mounts. Each tmpfs is bounded
to one quarter of the memory limit. `/workspace` disappears with the container;
it is never a durable dataset store. Local subprocess mode has no hostile-code
filesystem or network isolation.

Future datasets must use an independent private project UUID directory, opaque
file IDs, size/quota checks and descriptor-relative no-follow operations. Durable
data stays outside containers. A future controlled transfer will verify current
project/notebook membership and runtime ownership/generation, copy only selected
regular files into that runtime's `/workspace`, and apply UID 10001 permissions.
Reject symlinks, hardlinks, devices, traversal and archive escapes; bound transfer
bytes and ephemeral usage. Export requires the same scoped checks. Never mount
the storage root, user home, application DB or ownership registry. No upload or
transfer endpoint is implemented here. Private CNI restricts routing; it neither
makes datasets visible nor provides a filesystem boundary. No CNI changes needed.

## Execution receipts and retention decision

Receipt identity is `(notebook UUID, execution UUID)` with stable cell ID,
source SHA256, expected notebook revision, runtime/generation/kernel and Jupyter
session identities. Prepared/running become uncertain on restart; completed is
replayed through document CAS; applied is acknowledged idempotently. Conflict
preserves results without overwriting newer edits. Execution is never retried
automatically. Applied/conflict are settled application outcomes, but conflict
still needs user recovery. Uncertain cannot be inferred successful from age.

Retain **all receipts** in 3B, including after document/project deletion. Output
traffic is bounded by GatewayClient's 4 MiB budget and persisted files by 16 MiB.
There is currently no aggregate disk quota. Age-only GC is unsafe: even applied
receipts supply display identity mappings for subsequent cells, and application
messages/tasks/events may reference receipts. Follow-up: build an explicit
reference inventory across cell execution markers, display maps, application
links and unresolved/conflict recovery; implement an operator-reviewed archive
and size-budget policy before pruning. Never prune prepared/running/completed/
uncertain/conflict receipts by age. Retention tests must preserve recovery on
both sides of document commit. 3B intentionally adds no GC.

## Untrusted output rendering contract

The reducer accepts only correlated IOPub messages and translates them into
standard nbformat outputs, preserving MIME bundles and display updates. nbformat
validation and finite UTF-8 JSON checks are structural validation, **not content
sanitization**. Whole-document saves can also contain active content. Valid
outputs must remain unchanged for .ipynb compatibility and export.

Future renderers MUST treat every output and Markdown/comment/message as
untrusted, including results produced by the owner. Plain text/tracebacks render
as escaped text; JSON uses a nonexecuting tree/text viewer. HTML and SVG require
an audited sanitizer removing scripts, handlers, unsafe URLs and active embeds,
or an isolated sandbox without script/same-origin privileges. Raster images
require validated MIME/decoding and size limits; SVG is active markup, not a safe
raster image. JavaScript MIME, widgets and other executable/custom MIME types
are inert/download-only unless a separate explicit execution policy is approved.
Never execute output-provided JavaScript. External images, links, CSS resources
and remote embeds must not fetch automatically; require explicit user action and
safe URL schemes. Use restrictive CSP and no ambient credentials in previews.
Sanitization occurs in rendering, never by silently stripping persisted bundles.
API JSON is data and is never injected directly into DOM/HTML. No renderer exists
in 3B; these requirements are a mandatory contract for its next milestone.
