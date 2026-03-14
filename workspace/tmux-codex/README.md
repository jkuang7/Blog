# tmux-codex (`cl` / `clls`)

Standalone tmux wrapper for Codex sessions and lock-gated runner loops.

## Minimal User Flow

From inside the target repo or active worktree, users should only need to know:

- `/prompts:run_setup`
- `/prompts:run_execute` and `/prompts:run_update` are internal only
- `cl` -> `r=runner`

Important:
- `r=runner` only launches from existing runner state.
- It does not run setup, clear, auto-approve enablement, or rewrite `.memory/runner/*`.
- If setup is missing or not approved, runner start should fail fast and tell you to run `/prompts:run_setup`.

Everything else in this README is implementation detail for recovery, automation, or debugging.

## Commands

- `cl` - interactive menu
- `cl ls` / `clls` - same interactive entrypoint
- `cl loop <project> [--runner-id <id>] [--complexity low|med|high|xhigh] [--model <provider/model>]`
- `cl runner <project>` / `cl run <project>` / `cl r <project>` - loop aliases
- `cl stop <project>` / `cl k <project>` / `cl ka <project>` / `cl kb <project>` - immediate runner stop (writes stop lock + kills runner session)
- `cl k*` - stop all active runner sessions
- `python3 bin/runctl --setup ...` - advanced/internal equivalent of `/prompts:run_setup`
- `python3 bin/runctl --clear ...` - advanced/internal teardown primitive used by `/prompts:run_setup`

Single-runner policy:
- one loop runner per project
- canonical runner id is `main` (omit `--runner-id` or pass `--runner-id main`)

## Model Mapping

- `low` -> `gpt-5.3-codex` (effort `low`)
- `med` -> `gpt-5.3-codex` (effort `medium`)
- `high` -> `gpt-5.3-codex` (effort `high`)
- `xhigh` -> `gpt-5.3-codex` (effort `xhigh`)
- `--model` overrides mapping

## Runner State Contract

Runner-scoped files under `Repos/<project>/.memory/runner/`:

- `runtime/RUNNER_STATE.json` (`runner_id` is stored in JSON metadata)
- `runtime/RUNNER_LEDGER.ndjson`
- `PRD.json`
- `TASKS.json`
- `RUNNER_EXEC_CONTEXT.json`
- `runtime/RUNNER_CYCLE_PREPARED.json`
- `RUNNER_TASK_INTAKE.json`
- `locks/RUNNER_ENABLE.pending.json` (setup token gate; removed after approval)
- `locks/RUNNER_CLEAR.pending.json` (two-phase clear token/manifest)
- `runtime/RUNNER_HOOKS.ndjson` (hook events)

Runner lock files stay in `Repos/<project>/.memory/runner/locks/`:

- `RUNNER_DONE.lock`
- `RUNNER_STOP.lock`

Project-level top-level files stay in `Repos/<project>/.memory/`:

- `PRD.md` (runner-managed objective snapshot)
- `gates.sh` (must define `run_gates`)

## Runner Prompts

Canonical command spec lives at [`run.md`](/Users/jian/Dev/workspace/tmux-codex/run.md).

Normal usage from inside the repo/worktree:
- `/prompts:run_setup`
- approve enablement if prompted
- then `cl` -> `r=runner`

`/prompts:run_setup` now performs a two-phase clear before rebuilding runner state on non-approval runs. Manual teardown still exists at the CLI level via `python3 bin/runctl --clear ...`, but it is no longer a default installed prompt.

Internal runner dispatch:
- `r=runner` runs a two-step cycle:
  - `/prompts:run_execute`
  - `/prompts:run_update`
- setup/clear are decoupled and must never be injected into the runner pane

Fast task intake from a project conversation:
- `/add <task>` resolves the current runner root from the conversation cwd or active runner state, then queues the task via `runctl --task add`
- it defaults to non-preempting intake, so the new task waits behind the active cycle unless you explicitly ask to interrupt

While a runner is already active, queue extra work safely with:
- `python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --task add --project <project> --runner-id main --title "<task>"`
- queued work lands in `RUNNER_TASK_INTAKE.json` and merges into canonical `TASKS.json` on the next setup refresh
- default intake anchors new tasks behind the current active task so they do not preempt the in-flight cycle

## Scrolling In Runner Panes
- Mouse wheel/trackpad scrolling is enabled in tmux runner panes.
- Keyboard fallback: `PageUp` (or `Shift+PageUp`) enters copy-mode and scrolls back.
- Manual fallback: `Ctrl+Shift+Up` enters copy-mode.

Install prompt into Codex:

```bash
bash /Users/jian/Dev/workspace/tmux-codex/scripts/install-codex-run-prompt.sh
```

This validates `~/.codex/prompts/run_setup.md`, `run_execute.md`, `run_update.md`, and `add.md` in the global Codex home.

## Runner Runtime

- Public runner entrypoints launch an interactive Codex CLI pane with the infinite runner controller.
- `cl -> r=runner` and `cl loop <project>` should run work directly from existing runner state; they should not re-run setup as part of normal execution.
- `cl -> r=runner` and `cl loop <project>` are start-only paths. They must not bootstrap setup or approve enablement implicitly.
- setup builds phase-scoped exec context from compact repo context sources plus runner delta for better context carry-over
- setup/refresh also writes `.memory/runner/RUNNER_HANDOFF.md` so each fresh cycle gets a durable resume summary, not just thin state fields
- "Infinite" means the controller runs medium bounded slices, updates runner state, exits the current Codex session, then relaunches a fresh interactive TUI session until stop or done criteria are met.
- Runner exits on lock files:
  - `.memory/runner/locks/RUNNER_STOP.lock`
  - `.memory/runner/locks/RUNNER_DONE.lock`
- Fast manual stop:
  - `cl stop <project>` or `cl k <project>`
- Existing tmux runner sessions must be restarted to pick up wrapper changes (tmux keeps the original startup command per session).

## Done Enforcement

- Loops are fail-closed until enable token approval is applied.
- Internal worker paths (`cl __runner-controller`, `cl __runner-loop`) are implementation details and not public entrypoints.
- Done lock is created only when `done_candidate=true` update + passing `run_gates`.
- `runctl --setup` also performs final closeout when `TASKS.json` is already fully done and `run_gates` passes, so finished worktrees converge without requiring one extra runner spin.
- Done lock is only honored when project gates (`run_gates`) pass at lock detection time and `TASKS.json` has no open work.

## Test

```bash
cd /Users/jian/Dev/workspace/tmux-codex
python3 -m unittest discover -s tests -p 'test_*.py'
```
