# Repository instructions

## Keep this file

Keep this `AGENTS.md` in the repository root. The user explicitly requested that
it be added and not removed. Preserve it during cleanup, refactoring, and other
repository work. Update it when necessary; do not delete it unless the user
explicitly changes this instruction.

## Project context

Europa provides a FastAPI application backed by Jupyter Kernel Gateway, notebook
persistence, and project/conversation APIs. The React/TypeScript UI is in `web/`.
Read `README.md` and the relevant documents in `docs/` before changing behavior.

- `api/src/`: Python application and runtime providers.
- `tests/`: Python protocol, application, and integration tests.
- `web/`: Vite frontend and frontend/browser tests.
- `containerd-bridge/`: Go bridge for containerd operations.
- `gateway/` and `runtime-image/`: Gateway configuration and runtime image files.

## Working conventions

- Inspect existing code and preserve unrelated user changes.
- Keep changes focused on the requested task and follow nearby conventions.
- Use `rg` for file and text searches.
- Keep documentation aligned with implemented behavior; distinguish validated
  behavior from planned features and skipped integration tests.
- Never commit credentials, Gateway tokens, or private environment data.
- Keep local API and development servers bound to loopback. Gateway
  authentication is mandatory; Europa currently has no end-user authentication.
- Preserve Gateway ownership of kernel processes and its standard HTTP/WebSocket
  protocol. Read the runtime documentation before changing lifecycle behavior.

## Environment and validation

Use the repository `.venv` and the pinned dependencies in `requirements.lock`.
The documented Python version is CPython 3.13. Run checks appropriate to the change:

- Python tests: `.venv/bin/python -m pytest -q`.
- Gateway protocol tests:
  `.venv/bin/python -m pytest tests/test_gateway_protocol.py -q`.
- Frontend tests: run `npm test` from `web/`.
- Frontend type check and build: run `npm run build` from `web/`.

Real local Gateway tests require explicit opt-in with `--run-local-gateway` and
execute trusted repository code. Browser tests (`npm run test:browser` in `web/`)
also launch local services and require the repository `.venv` and Chrome.
Real containerd tests require approved Linux infrastructure and `--run-containerd`;
consult `docs/containerd-provider.md` before running them. Do not treat skipped
integration tests as successful validation.

For documentation-only changes, inspect the final diff; application tests are
usually unnecessary. Report what changed and any checks performed or limitations.

## Agent workflow and project lessons

Read [docs/agent-workflow.md](docs/agent-workflow.md) when delegating, resuming
interrupted work, or validating a milestone. Keep reusable Europa-specific
prompts in `docs/agent-prompts/`. Record evidence and limitations with each lesson;
do not turn unverified diagnoses into project rules. General agent instructions
and resource limits belong in the shared OpenCode setup, not application code.

For autonomous OpenCode work on the notebook UI, first read
[the current owner directions](docs/agent-prompts/phase3g-conversation-canvas.md),
[the persistence contract](docs/conversational-notebooks.md), and
[the latest validation report](docs/phase3g-validation.md). These current owner
directions supersede earlier sidebar/docking designs. Follow
[the future agent interaction contract](docs/notebook-agent-interaction.md)
when planning engine work; it does not authorize starting that milestone.

- On this development machine, use one OpenCode worker and one heavy validation
  job at a time. Do not recursively spawn agents or run duplicate browser suites.
- Give work a bounded file scope, tool/time budget, acceptance criteria and a
  concrete handoff. Diagnose before editing; verify before claiming success.
- Use Node 24 for frontend checks, single-worker unit/Chrome runs, and zero
  browser retries. Synchronize on actual saved/settled state, not sleeps or
  forced clicks. Investigate failures before increasing timeouts.
- Preserve the user's preview and data. Track services you launch, clean up only
  verified owned processes after interruption, and verify shutdown. Do not start
  or restart a preview after the user has requested it stopped.
- Keep Phase 3G incomplete while required regressions fail. Record the next
  reproducible investigation in the validation report so another agent can resume
  without a coordinator. Do not build the real agent engine in this checkpoint.
