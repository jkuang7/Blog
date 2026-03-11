Run the `implement` phase for the infinite runner in execute-only mode.

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
3. Stay on the current `phase_goal` and one coherent implementation surface.
4. Multiple related edits are allowed inside this phase, but keep them bounded to the same surface.
5. Run targeted validation as you go; do not defer all validation to the end.
6. Fail closed on zero-progress:
   - do not treat inspection or state refresh as phase progress,
   - do not write `RUNNER_CYCLE_PREPARED.json` if the same phase/task/blocker state would be handed back unchanged.
7. Before the setup refresh, synchronize task state with reality:
   - if the active task acceptance is satisfied, mark it done in `TASKS.json` first, preferably via `python3 /Users/jian/Dev/tmux-codex/bin/runctl --task set --project-root <target_root> --runner-id main --task-id <active_task_id> --status done`,
   - if the task is not complete but the exact blocker changed, update the relevant task metadata first so the next handoff is concrete.
8. At a real phase boundary, refresh once:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
9. Then write the prepared marker:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
   - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
10. Terminate this Codex chat session immediately after writing the marker.

## Completion Condition

Stop only when the current implementation surface is complete, blocked, or should hand off to another phase, and the prepared marker is written.
