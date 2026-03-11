# tmux-codex (`cl` / `clls`)

Standalone tmux wrapper for Codex sessions and lock-gated multi-runner loops.

## Commands

- `cl` - interactive menu
- `cl ls` / `clls` - same interactive entrypoint
- `cl loop <project> [--runner-id <id>] [--complexity low|med|high|xhigh] [--model <provider/model>] [--hil-mode setup-only] [--runner-mode interactive-watchdog|exec]`
- `cl runner <project>` / `cl run <project>` / `cl r <project>` - loop aliases
- `cl stop <project>` / `cl k <project>` / `cl ka <project>` / `cl kb <project>` - immediate runner stop (writes stop lock + kills runner session)
- `cl k*` - stop all active runner sessions
- `python3 bin/runctl --setup ...` - deterministic runner state bootstrap + HIL enable gate
- `python3 bin/runctl --clear ...` - token-confirmed two-phase state clear

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

- `RUNNER_STATE.json` (`runner_id` is stored in JSON metadata)
- `RUNNER_LEDGER.ndjson`
- `PRD.json`
- `TASKS.json`
- `RUNNER_EXEC_CONTEXT.json`
- `RUNNER_WATCHDOG.json`
- `RUNNER_CYCLE_PREPARED.json`
- `RUNNER_TASK_INTAKE.json`
- `RUNNER_ENABLE.pending.json` (setup token gate; removed after approval)
- `RUNNER_CLEAR.pending.json` (two-phase clear token/manifest)
- `RUNNER_HOOKS.ndjson` (hook events)

Project-level lock files stay in `Repos/<project>/.memory/`:

- `RUNNER_DONE.lock`
- `RUNNER_STOP.lock`
- `gates.sh` (must define `run_gates`)

## `/run` Prompt Command

Canonical command spec lives at [`run.md`](/Users/jian/Dev/tmux-codex/run.md).

Default `/prompts:run` usage is plan-first and human-in-the-loop:
- use it to refine setup state, repo scope, and `.memory/runner/*` file conditions with the human
- do not treat it as permission to auto-start the runner
- once setup is satisfactory, the human can start the runner later from `cl` / TUI with `r=runner`

Fast task intake from a project conversation:
- `/prompts:add <task>` resolves the current runner root from the conversation cwd or active runner state, then queues the task via `runctl --task add`
- it defaults to non-preempting intake, so the new task waits behind the active cycle unless you explicitly ask to interrupt

While a runner is already active, queue extra work safely with:
- `python3 /Users/jian/Dev/tmux-codex/bin/runctl --task add --project <project> --runner-id main --title "<task>"`
- queued work lands in `RUNNER_TASK_INTAKE.json` and merges into canonical `TASKS.json` on the next setup refresh
- default intake anchors new tasks behind the current active task so they do not preempt the in-flight cycle

## Scrolling In Runner Panes
- Mouse wheel/trackpad scrolling is enabled in tmux runner panes.
- Keyboard fallback: `PageUp` (or `Shift+PageUp`) enters copy-mode and scrolls back.
- Manual fallback: `Ctrl+Shift+Up` enters copy-mode.

Install prompt into Codex:

```bash
bash /Users/jian/Dev/tmux-codex/scripts/install-codex-run-prompt.sh
```

This links `~/.codex/prompts/run.md`, `~/.codex/prompts/add.md`, the four phase prompts, and the legacy `runner-cycle.md` compatibility prompt to the repo-managed prompt specs.

## Runner Runtime

- Default mode (`interactive-watchdog`) runs a live interactive `codex --search` pane (same UX class as `n=new`) plus a detached watchdog.
- Runner chat launches from the resolved project root (not `~/Dev`) and uses `--dangerously-bypass-approvals-and-sandbox` to avoid approval prompts.
- Interactive wrapper keeps one Codex session alive for a bounded prompt phase and rotates on phase handoff, no-progress, budget expiry, or lock.
- Watchdog behavior:
  - detects idle prompt state
  - injects a phase-specific execute-only prompt (`runner-discover`, `runner-implement`, `runner-verify`, `runner-closeout`)
  - falls back to `/prompts:run ... MODE=execute_only PHASE=<phase>` if the dedicated phase prompt is unavailable
  - respawns Codex in the same project root with the same auto-approval profile if process exits
- setup builds phase-scoped exec context from compact repo context sources plus runner delta for better context carry-over
- "Infinite" means watchdog reseeds work whenever Codex returns idle and can restart Codex after exits.
- Watchdog exits on runner lock files:
  - `.memory/RUNNER_STOP.lock`
  - `.memory/RUNNER_DONE.lock`
- Fast manual stop:
  - `cl stop <project>` or `cl k <project>`
- `--runner-mode exec` keeps legacy non-interactive `codex --search exec` cycle behavior as a compatibility fallback.
- Existing tmux runner sessions must be restarted to pick up wrapper changes (tmux keeps the original startup command per session).

## HIL and Done Enforcement

- Loops are fail-closed until enable token approval is applied.
- Default runtime mode is `setup-only` HIL (no per-iteration approval blocking).
- Legacy deterministic worker path (`cl __runner-loop`) remains available only for backward compatibility.
- Done lock is created only when `done_candidate=true` update + passing `run_gates`.
- `runctl --setup` also performs final closeout when `TASKS.json` is already fully done and `run_gates` passes, so finished worktrees converge without requiring one extra runner spin.
- Done lock is only honored when project gates (`run_gates`) pass at lock detection time and `TASKS.json` has no open work.

## Test

```bash
cd /Users/jian/Dev/tmux-codex
python3 -m unittest discover -s tests -p 'test_*.py'
```
