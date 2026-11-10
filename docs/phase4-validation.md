# Phase 4 validation

## Status

4A passed the supported host gate. Continue through 4D only after each independent
mandatory gate passes. Phase 4E/F are excluded.

## Git preparation

- Starting worktree clean on main `c5a8a603ddef020d42f802c15896174d11678287`.
- Verified validated commits `a6b2daa` and `7e7598b` are ancestors; main's tree
  equals the validated feature tip. Prior merge `c5a8a60` already contains Phase 3H;
  `git merge --ff-only feature/phase3h-workspace-navigation` reported up to date.
- Created local annotated tag `phase3h-complete` at that main commit.
- Created `feature/phase4-agentic-harness` from main.
- No reset, force-push, history rewrite, remote cleanup/publication or master branch.
- Preserved main and both linked worktree branches. No unmerged non-worktree local
  branches existed. Deleted only these ancestor-verified local branches:

| Deleted local branch | Retained commit (ancestor of main) |
| --- | --- |
| `feature/containerd-provider` | `53564981924df5ad3a74f53b59eff0fde6e001a9` |
| `feature/conversational-notebooks` | `2009a9e030042be1567de250c2adebcd40f0b273` |
| `feature/europa-web-ui` | `cb333250a826d0c75f03f0401428ab5c955dbbf8` |
| `feature/notebook-persistence` | `a5f715d970f4ba7ab9497a51d675c7d0ae28c40c` |
| `feature/phase3f-b-navigation` | `517185c5f133f7fcb3fa742bcd9a85d2e186190c` |
| `feature/phase3f-c-jupyter-ui` | `53aaee14171a985dc7887cc7a2e9a2f86c69771c` |
| `feature/phase3f-d-ui-recovery` | `53aaee14171a985dc7887cc7a2e9a2f86c69771c` |
| `feature/phase3g-notebook-ux` | `ad9a8fcbb9060708c4fa603967ad68cb521122a5` |
| `feature/phase3h-workspace-navigation` | `7e7598b7a370216870efa3fcdf85ea28a33fa7e0` |
| `feature/resource-admission` | `9a064c5e57e1558c3cb7a6cc14399228a1149729` |
| `feature/runtime-lifecycle` | `e3a4d4e85bceabc0364e2fa74bdab6dde2d3f89e` |
| `feature/runtime-prewarming` | `12e0ec7d02225c6b33dd8f0c82e0409a86fdd6e6` |
| `feature/runtime-provider` | `cd50ce8ebad6ddd674e24f46ccd4ce21c963ce57` |
| `feature/runtime-recovery` | `21dd42a5029e369b0210b92b40c8803561da22d0` |
| `feature/ui-refinement` | `c0a3f01d22a9c08100908e835166f965116a4791` |
| `migration/jupyter-kernel-gateway` | `9dd067308a50b985f85973c6ceb88643b2f69f26` |
| `validation/step2b-runc-arm64` | `681dec0f89991dc7a06da3aa765476c81b3ca691` |

All remote branches and worktrees were retained. Git preparation did not alter application files or owner data.

## Checkpoint 4A

Starting commit: `c5a8a603ddef020d42f802c15896174d11678287`.

Changed files: `api/src/agents/{schema,models,providers,store,tools,context,runtime,router}.py`,
application store and API lifespan wiring, `tests/test_agents_foundation.py`, migration
fixtures in `tests/test_application.py`, Chrome agent fixture/suite/configuration,
README, Phase 4 design reports and screenshots. Schema v7 extends the existing
application DB, permits workspace conversation/message rows and preserves existing
records/IDs/foreign keys/append-only rules. Old-version fixtures now reconstruct
actual old schema shapes; an initial 4-test fixture failure was resolved without
making production migration accept an inconsistent database.

`/api/agents/configuration`, `/conversation`, `/runs`, `/runs/{id}`, paginated
`/events`, exact approval decisions, `/cancel` and `/resume` provide the durable
client contract. Runs and user turns share a transaction; stable operation IDs
replay without duplicate messages. SQLite owner token/generation/expiry fences
worker commits. Only six scoped read tools are registered; approval enforcement
and effect interruption are tested with controlled journal fixtures. A notebook
scope grants its own cells/metadata and relevant project, with no directory grant.
No provider key or reasoning field is exposed; credential-bearing proposal IDs
and arguments are rejected. No production write or execution tool in 4A.

| Command | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m pytest tests/test_agents_foundation.py tests/test_application.py -q` | 0 | 86 passed before the final extra secret-ID test |
| `.venv/bin/python -m pytest --run-local-gateway -q -ra` | 0 | 357 passed, 79 Linux/containerd skips, 44.10 s on final 4A |
| `npm test -- --maxWorkers=1` (Node 24, `web/`) | 0 | 94 passed |
| `npm run build` (`web/`) | 0 | production build passed |
| `npx playwright test --workers=1 --retries=0 --reporter=line,json` | 0 | 57 passed, 1 ARM64 skip, 2.7 min |
| `npx playwright test --project=agents --workers=1 --retries=0 --reporter=line` | 0 | final secret-ID hardening affected-browser rerun: 1 passed, 12.9 s |

The complete Chrome run includes all Phase 3H suites and the 4A browser-origin
Markov study request. The last backend-only hardening rejected credential-bearing
provider call IDs and hid key values from configuration repr; the full backend and
relevant browser scenario passed again. Frontend/build were unchanged. Captures:
[desktop Home](screenshots/phase4/4a/home-desktop.png) and
[mobile after restart](screenshots/phase4/4a/home-mobile-after-restart.png) were
inspected. They show the preserved dashboard, not a fabricated chat UI. The
browser test verifies actual user/assistant records, read-tool events, idempotent
replay, cross-origin rejection and identical history after owned backend restart.

Fake provider and loopback transport fixtures only; no paid or external provider
smoke. Tool calls are single per response and public events are polled; native
provider streaming and general exactly-once effects are not claimed. Full logs:
`/private/tmp/europa-phase4-validation/` (initial application/agent fixture logs
also under `/tmp/europa-phase4-*.log`). Test listeners were stopped; owner data and
preview were untouched. Mandatory 4A gates pass; proceed to 4B.
