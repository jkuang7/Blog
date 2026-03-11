Run the `closeout` phase for the infinite runner in execute-only mode.

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
3. Focus on final convergence:
   - full closeout gates,
   - done-state validation,
   - or rewriting the exact final blocker when done-state still fails.
4. Do not reopen broad refactor work. Leave only the exact closeout blocker if completion is not yet valid.
5. Fail closed on zero-progress:
   - do not write `RUNNER_CYCLE_PREPARED.json` if closeout state would be handed back unchanged,
   - do not claim completion while `TASKS.json` still has open work.
6. Before the setup refresh, synchronize task state with reality:
   - mark any truly completed final task done in `TASKS.json`,
   - if closeout fails, leave only the exact blocker task open or blocked.
7. At a real phase boundary, refresh once:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
8. Then write the prepared marker:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
   - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
9. Terminate this Codex chat session immediately after writing the marker.

## Completion Condition

Stop only when the runner is truly converged to done, or the final blocker is explicit and the prepared marker is written.
