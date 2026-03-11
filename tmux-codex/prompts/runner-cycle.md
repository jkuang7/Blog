Compatibility alias for the `implement` phase in execute-only mode.

Runner context args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- `PROJECT_ROOT=$PROJECT_ROOT`
- `MODE=$MODE` (expected: `execute_only`)

## Hard Rules

1. Do **not** run setup/context preflight at the beginning.
2. Do **not** run `/run --setup` as your first action.
3. Read `<target_root>/.memory/runner/RUNNER_STATE.json` and `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`.
4. Stay on the current implementation surface and phase goal from the exec context.
5. Fail closed on zero-progress:
   - do not count read-only inspection or a pure state refresh as progress,
   - do not write `RUNNER_CYCLE_PREPARED.json` if `next_task_id` / `next_task` would be handed back unchanged,
   - if the only outcome is better blocker clarity, update `TASKS.json` first so the next handoff is narrower and concrete.
6. After validation, refresh runner state once:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
7. Write prepared marker for watchdog handoff:
   - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
   - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
8. Terminate this Codex chat session immediately after writing the marker.

## Prepared Marker Schema

```json
{
  "prepared_at": "UTC timestamp in %Y-%m-%dT%H:%M:%SZ",
  "project": "<PROJECT>",
  "runner_id": "main",
  "phase": "implement",
  "git_worktree": "<target_root absolute path>",
  "next_task": "<next task snapshot from refreshed RUNNER_STATE>"
}
```

## Target Root Resolution

- Use `PWD` when provided.
- Else use `PROJECT_ROOT`.
- All reads/writes must stay inside that resolved root.

## Completion Condition for this iteration

When the implementation phase reaches a real handoff boundary and marker is written:
- stop output
- exit chat session so wrapper/watchdog can respawn fresh.
