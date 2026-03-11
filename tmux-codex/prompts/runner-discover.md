Run the `discover` phase for the infinite runner in execute-only mode.

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
3. Load only the compact `context_sources` and `context_delta` listed in `RUNNER_EXEC_CONTEXT.json` before doing extra repo digging.
4. Tighten PRD/tasks/context so the next work surface is narrower and more concrete.
5. Stay in discovery only: do not sprawl into unrelated implementation work.
6. Fail closed on zero-progress:
   - do not write `RUNNER_CYCLE_PREPARED.json` if phase, task, blockers, and worktree state would be handed back unchanged,
   - if you only learn a narrower blocker, update `TASKS.json` first so the next handoff is more concrete.
7. Before the setup refresh, synchronize task state with reality:
   - if discovery proves the active task should be split, blocked, or replaced, update `TASKS.json` first,
   - do not keep stale task wording when you have a more precise dependency or blocker shape.
8. When discovery reaches a clean phase boundary, refresh once:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
9. Then write the prepared marker:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
   - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
10. Terminate this Codex chat session immediately after writing the marker.

## Completion Condition

Stop only when discovery has materially improved the next prompt handoff and the prepared marker is written.
