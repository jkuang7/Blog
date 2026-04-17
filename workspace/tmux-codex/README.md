# tmux-codex (`cl` / `clls`)

Standalone tmux wrapper for Codex sessions and lock-gated runner loops.
It is the execution/session layer in a Linear-native stack:

- Telegram command surface -> `telecodex`
- deterministic orchestration -> `ORX`
- durable task graph / issue context -> Linear
- live worktree + Codex session residency -> `tmux-codex`

## Minimal User Flow

From inside the target repo or active worktree, local operators should only need to know:

- `/run_setup`
- `cl` -> `r=runner`
- `clls` to inspect the live ORX-backed runner/session view
- the Start Runners picker now reads ORX `/dashboard` queue state instead of local `.memory` task counts
- `tmux-codex` renders `run_execute` / `run_govern` prompt templates internally, so the infinite runner does not depend on Codex surfacing custom slash commands

Important:
- `r=runner` only launches from existing runner state.
- It does not run setup, clear, auto-approve enablement, or rewrite `.memory/runner/*`.
- If setup is missing or not approved, runner start should fail fast and tell you to run `/run_setup`.

Everything else in this README is implementation detail for recovery, automation, or debugging.

## Commands

- `cl` - interactive menu
- `cl ls` / `clls` - same interactive entrypoint
- `cl loop <project> [--runner-id <id>] [--complexity low|med|high|xhigh] [--model <provider/model>]`
- `cl runner <project>` / `cl run <project>` / `cl r <project>` - loop aliases
- `cl stop <project>` / `cl k <project>` / `cl ka <project>` / `cl kb <project>` - immediate runner stop (writes stop lock + kills runner session)
- `cl k*` - stop all active runner sessions
- `python3 bin/runctl --setup ...` - advanced/internal equivalent of `/run_setup`
- `python3 bin/runctl --clear ...` - advanced/internal teardown primitive used by `/run_setup`

Single-runner policy:
- one loop runner per project
- canonical runner id is `main` (omit `--runner-id` or pass `--runner-id main`)

## Model Mapping

- Plain interactive `cl` sessions inherit the global Codex default profile. In this workspace that now means cheap control-plane chat on `gpt-5.4-mini` with `medium` reasoning.
- Wrapper-launched planning and execution sessions such as `/plan`, `/kanban`, `/continuous-workflow`, `/run`, `/integrate`, `/spec`, `/refactor`, `/enhance`, and `/review` are promoted to `gpt-5.4` with `high` reasoning at launch time.
- `low` -> `gpt-5.3-codex` (effort `low`)
- `med` -> `gpt-5.3-codex` (effort `medium`)
- `high` -> `gpt-5.3-codex` (effort `high`)
- `xhigh` -> `gpt-5.3-codex` (effort `xhigh`)
- `--model` overrides mapping

## Runner State Contract

Runner-scoped files under the active issue worktree `/.memory/runner/`:

- `runtime/RUNNER_STATE.json` (`runner_id` is stored in JSON metadata)
- `runtime/RUNNER_LEDGER.ndjson`
- `PRD.json`
- `TASKS.json`
- `KANBAN_STATE.json`
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

## Deterministic Linear-Native Direction

- The legacy file-managed planner is now compatibility-only.
- `PRD.json` and `TASKS.json` still exist so the runner can execute deterministically, but they are no longer the canonical work ledger.
- The canonical model is:
  - Linear owns task identity, dependencies, blockers, and acceptance context
  - ORX owns issue selection, queueing, continuity, and worktree/session routing
  - `tmux-codex` owns the live Codex/tmux session and bounded execution slices
- Linear issues and ORX now own task selection; legacy GitHub issue / shared-board assumptions are deprecated in this runner path.
- `KANBAN_STATE.json`, `RUNNER_EXEC_CONTEXT.json`, and `RUNNER_HANDOFF.md` should be treated as local execution state derived from ORX/Linear, not as a competing planner.

## Runner Prompts

Canonical command spec lives at [`run.md`](/Users/jian/Dev/workspace/tmux-codex/run.md).

Normal usage from inside the repo/worktree:
- `/run_setup`
- approve enablement if prompted
- then `cl` -> `r=runner`

`/run_setup` now performs a two-phase clear before rebuilding runner state on non-approval runs. Manual teardown still exists at the CLI level via `python3 bin/runctl --clear ...`, but it is no longer a default installed prompt.

Internal runner dispatch:
- `r=runner` runs a two-step cycle:
  - rendered `run_execute` prompt body
  - rendered `run_govern` prompt body when semantic repair is needed
- setup/clear are decoupled and must never be injected into the runner pane
- before the runner starts, `tmux-codex` asks ORX for the current Linear issue context, provisions the issue worktree if needed, and seeds the runner state from that issue

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

Install runner commands into Codex:

```bash
bash /Users/jian/Dev/workspace/tmux-codex/scripts/install-codex-run-prompt.sh
```

This validates the canonical prompt templates and refreshes optional Codex command links under `~/.codex/commands/` and compatibility links under `~/.codex/prompts/`. The live infinite runner still submits rendered prompt bodies directly, so it does not depend on custom slash-command discovery.

## Runner Runtime

- Public runner entrypoints launch an interactive Codex CLI pane with the infinite runner controller.
- `cl loop <project>` and `cl -> r=runner` are ORX/Linear-native:
  - they ask ORX for the current active or next runnable Linear issue
  - they ensure the issue worktree exists
  - they seed runner state from the Linear issue snapshot and issue metadata
  - they do not enumerate projects or task counts from local `.memory` files
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
- `clls` should show the live ORX-managed runner/session state and the worktree it is actually attached to; if it does not, treat that as a bug in session truth, not as expected behavior.

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
