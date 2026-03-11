Run the `verify` phase for the infinite runner in execute-only mode.

Runner context args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- `PROJECT_ROOT=$PROJECT_ROOT`
- `MODE=$MODE` (expected: `execute_only`)

## Hard Rules

1. Do **not** run `/run --setup` as your first action.
2. Read `<target_root>/.memory/runner/RUNNER_STATE.json` and `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`.
3. Focus on failing validation, harness checks, tests, lint, typecheck, or blocker isolation.
4. Keep output operational and compact; prefer concrete failure surfaces over broad summaries.
5. If a blocker narrows, update `TASKS.json` so the next handoff names the exact failing surface.
6. Fail closed on zero-progress:
   - do not refresh + prepare + exit with unchanged blockers,
   - do not hand off a broad “still failing” state when you can isolate the failing surface.
7. Before the setup refresh, synchronize task state with reality:
   - if verification proves the active task is complete, mark that task done in `TASKS.json` first,
   - if the task is blocked, update the task metadata so the next handoff names the exact blocker.
8. At a real phase boundary, refresh once:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
9. Then write the prepared marker:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
   - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
10. Terminate this Codex chat session immediately after writing the marker.

## Completion Condition

Stop only when validation is clean enough to hand back to implement/closeout, or the blocker has been narrowed materially and the prepared marker is written.
