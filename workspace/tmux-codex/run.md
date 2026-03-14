# Runner Prompt Contract

The runner control plane is fully decoupled into three prompts:

1. `/prompts:run_setup`
2. `/prompts:run_execute`
3. `/prompts:run_update`

`cl -> r=runner` must use only `/prompts:run_execute` followed by `/prompts:run_update` internally.

## Minimal User Flow

From inside the target repo or active worktree:

1. `/prompts:run_setup`
2. approve enablement if setup returns a token
3. start the runner from `cl` / TUI with `r=runner`

Runner start is intentionally decoupled:
- `r=runner` is launch-only
- it must not run setup
- it must not clear state
- it must not auto-approve enablement
- if setup is missing or not approved, it must fail fast and tell the user to run `/prompts:run_setup`

## Prompt Ownership

- `/prompts:run_setup`
  - clear-then-setup on normal setup runs
  - setup-only on `--approve-enable <token>` reruns
  - creates fresh runner state
  - may return an enable approval token
- `/prompts:run_execute`
  - execute-only worker prompt
  - one medium bounded phase iteration
  - validate the active surface
  - terminate the session
- `/prompts:run_update`
  - post-execute refresh prompt
  - quiet setup refresh
  - writes the prepared marker
  - terminate the session

Deprecated:
- `/run`
  - removed from the normal runner flow
  - retained only as a temporary migration alias while old sessions age out

## Non-Negotiables

- Single runner id: `main`
- Source of truth:
  - `<target-root>/.memory/runner/runtime/RUNNER_STATE.json`
  - `<target-root>/.memory/runner/TASKS.json`
- Audit log:
  - `<target-root>/.memory/runner/runtime/RUNNER_LEDGER.ndjson`
- Scope first:
  - wrong root means wrong context and wrong edits

## Root Resolution

`runctl` resolves target root in this order:

1. explicit `--project-root <abs-path>`
2. `$DEV/Repos/<project>/.memory/RUNNER_CONTEXT.json`
3. saved runner state discovery (worktree preferred)
4. `$DEV/Repos/<project>`

## Setup / Clear

Advanced CLI equivalents:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --clear --project-root "$PWD" --runner-id main
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --clear --project-root "$PWD" --runner-id main --confirm <CLEAR_TOKEN>
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --setup --project-root "$PWD" --runner-id main
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --setup --project-root "$PWD" --runner-id main --approve-enable <ENABLE_TOKEN>
```

Setup requirements:
- when setup is invoked without `--approve-enable`, first clear existing runner-managed state via the two-phase clear flow
- if `PRD.json` / `TASKS.json` are missing, generic, or stale for the current request, seed fresh concrete files before running setup
- use the latest explicit user request as the source of truth for the seeded objective and bounded tasks
- seeded tasks must be narrow enough for one bounded runner slice; do not seed umbrella tasks when the request already names smaller concrete surfaces
- refresh `.memory/runner/*`
- refresh `RUNNER_EXEC_CONTEXT.json`
- keep `next_task_id` and `next_task` aligned with `TASKS.json`
- write `RUNNER_HANDOFF.md`

Clear requirements:
- remove runner-managed state safely
- remove `RUNNER_HANDOFF.md`
- remove `.memory/PRD.md`
- remove legacy `REFRACTOR_STATUS.md` if present

## Execute Contract

`/prompts:run_execute` and `/prompts:run_update` are the only prompts `r=runner` should drive.

`/prompts:run_execute` must:
- read `.memory/runner/runtime/RUNNER_STATE.json`
- read `RUNNER_EXEC_CONTEXT.json`
- resolve phase from explicit `PHASE` or exec context
- work only within the current phase goal and one coherent medium slice
- avoid setup/clear behavior
- terminate immediately

`/prompts:run_update` must:
- refresh runner state with quiet setup
- write `.memory/runner/runtime/RUNNER_CYCLE_PREPARED.json`
- terminate immediately

Fail-closed rules:
- do not hand off after a no-op inspection cycle
- if nothing concrete changed, narrow the blocker in `TASKS.json` first
- do not create `.memory/runner/locks/RUNNER_DONE.lock` while open tasks remain

## Runner Start Contract

`cl -> r=runner` and `cl loop <project>`:
- start an interactive Codex CLI pane
- launch an internal controller per cycle
- controller dispatches `/prompts:run_execute ...` and then `/prompts:run_update ...`
- after update, controller exits the current Codex session so a fresh TUI session is launched for the next cycle
- controller never dispatches setup or clear

## Prompt Install Contract

Installed prompts in `~/.codex/prompts`:
- `run_setup.md`
- `run_execute.md`
- `run_update.md`
- `add.md`

Removed legacy prompt files (cleanup only):
- `run.md`
- `run_clear.md`
- `runner-cycle.md`
- `runner-discover.md`
- `runner-implement.md`
- `runner-verify.md`
- `runner-closeout.md`
