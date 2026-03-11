# `/run` Command (Codex Infinite Runner)

Deterministic, auditable runner state management. `/run` prepares or repairs state only; it does not start `cl loop`.

## Non-Negotiables

- Fail-closed: runner stays disabled until enable token approval.
- Single runner id: `main`.
- Source of truth: `<target-root>/.memory/runner/RUNNER_STATE.json` + `<target-root>/.memory/runner/TASKS.json`.
- Audit log: `<target-root>/.memory/runner/RUNNER_LEDGER.ndjson`.
- Scope first: the wrong root causes wrong context and wrong edits.

## Root Resolution (Authoritative)

`runctl` resolves target root in this strict order:

1. Explicit `--project-root <abs-path>`
2. Canonical context pointer: `$DEV/Repos/<project>/.memory/RUNNER_CONTEXT.json`
3. Saved runner state discovery (worktree preferred over canonical)
4. Canonical fallback: `$DEV/Repos/<project>`

Setup writes/refreshes `RUNNER_CONTEXT.json` so future `/run --setup --project <name>` reuses the same worktree root.

## Commands

- `--setup`: create/refresh runner state for the resolved target root
- `--clear`: two-phase clear (`--confirm <token>` required)
- default action when omitted: `--setup`
- phase execution prompts:
  - `/prompts:runner-discover ... MODE=execute_only`
  - `/prompts:runner-implement ... MODE=execute_only`
  - `/prompts:runner-verify ... MODE=execute_only`
  - `/prompts:runner-closeout ... MODE=execute_only`
  - compatibility fallback: `/prompts:run ... MODE=execute_only PHASE=<phase>`
  - mode resolution must come from slash-command args context, never shell env probes like `$MODE`
- task APIs:
  - `runctl --task list|show|set|next|find|add|queue ...`
  - `runctl --objective show|set ...`
- quick intake prompt:
  - `/prompts:add <task>` resolves the current conversation runner root and queues work via `runctl --task add`

## Terminate-Only Requests

If the user explicitly asks to terminate/end the current Codex chat so wrapper/watchdog can respawn:

- do not run setup
- do not load context files
- do not run cleanup or validation gates
- exit immediately (minimal output only)

## Prompt Role Split

- `/prompts:run` (default): planning-heavy HIL setup/clear/state alignment only
  - ask questions, refine plan, and confirm setup file conditions with the human before writing runner state
  - do not auto-start `cl r` / `cl runner`; leave that to a later manual TUI action
- `/prompts:runner-discover|runner-implement|runner-verify|runner-closeout ... MODE=execute_only`: preferred phase prompts
- `/prompts:run ... MODE=execute_only PHASE=<phase>`: compatibility fallback when the dedicated phase prompt is unavailable
  - execute-only prompts end with `runctl --quiet` setup refresh + `runctl --prepare-cycle --quiet` marker write + exit

Execute-only convergence rule:
- Do not allow a read-only inspection cycle to hand off.
- Before writing `RUNNER_CYCLE_PREPARED.json`, either:
  - land a concrete code/test/config change with validation evidence, or
  - update runner metadata (`TASKS.json` first, then refreshed state) to narrow the next step/blocker from concrete evidence gathered in that cycle.
- If the same phase / `next_task_id` / blocker state would be handed back unchanged after a no-op cycle, rewrite it to the exact failing surface or mark it blocked; do not refresh + prepare + exit with identical task wording.

## Live Task Intake

- Fast prompt path:
  - `/prompts:add <task>`
  - resolves the target runner root from the current conversation cwd/ancestor runner state when possible
  - asks only if project scope is ambiguous
- While an infinite runner is active, add new backlog safely with:
  - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --task add --project <name> --title "<task>" --runner-id main`
  - inspect queued intake with `runctl --task queue ...`
- `--task add` appends work to `RUNNER_TASK_INTAKE.json`; it does not rewrite the current phase exec context mid-cycle.
- Default intake behavior is non-interfering:
  - new tasks are anchored behind the current active/next task unless you pass `--allow-preempt`
  - the next `runctl --setup` refresh merges queued intake into canonical `TASKS.json`
- Use explicit `--depends-on TT-...` when the new work should wait on a specific prior task or on another queued task you already added.

Execution-mode token policy:
- Keep iteration responses compact and operational.
- Avoid repeating setup/scope boilerplate in execute-only mode.

## Syntax

- `/run --setup --project-root <abs-path> --runner-id main`
- `/run --setup --project <name> --runner-id main`
- `/run --setup <project> --runner-id main`
- `/run --clear --project-root <abs-path> --runner-id main`
- `/run --clear --project <name> --runner-id main --confirm <token>`

## Worktree Rule

If active cwd is a Codex worktree (`/.codex/worktrees/`), use `--project-root "$PWD"` for setup/clear/approval.

## Setup Contract

`--setup`:

- is normally reached from a planning/HIL conversation in `/prompts:run`
- should only be written after the human is satisfied with the setup state for that project/worktree
- recreates `<target-root>/.memory/runner/`
- clears transient root locks (`RUNNER_STOP.lock`, `RUNNER_ACTIVE.lock`)
- keeps canonical objective/task artifacts in `.memory/runner/`:
  - `PRD.json`, `TASKS.json`, `RUNNER_EXEC_CONTEXT.json`
- writes phase-scoped runner state:
  - `current_phase`, `phase_status`, `phase_started_at`, `phase_budget_minutes`, `phase_context_digest`
- builds `RUNNER_EXEC_CONTEXT.json` from:
  - compact repo context sources in declared repo order when available
  - `.codex/context-pack.md` / `.codex/context-pack.json` when present
  - runner delta (`next_task`, blockers, validation surface, phase goal)
- removes deprecated legacy view files if present:
  - `RUNNER_NEXT.md`, `RUNNER_DOD.md`, `RUNNER_PLAN.md`, `PRD.md`, `TASKS.md`, `.memory/GOALS.md`
- selects `next_task_id` deterministically from `TASKS.json` (open + deps resolved + priority + oldest updated)
- updates legacy `next_task` string from canonical `next_task_id` for compatibility
- derives a deterministic phase:
  - `discover`, `implement`, `verify`, or `closeout`
- keeps prior enablement only when identity matches
- emits `approve_enable_token` only when needed
- if `TASKS.json` has zero open tasks and `run_gates` passes, setup writes `RUNNER_DONE.lock` and converges state to `status=done`
- after setup, runner launch remains a separate manual action in `cl` / TUI (`r=runner`)

Validation policy:

- runner iterations may add or tighten validation gates when new risk appears
- runner may perform one extra validation prompt run when completion confidence is high but a final guard check is still needed
- completion requires both:
  - `run_gates` passes
  - `.memory/runner/TASKS.json` has zero tasks in `open|in_progress|blocked`
- if open tasks remain, do not create or preserve `.memory/RUNNER_DONE.lock`

### Harness Completion Gate (Mandatory)

Before writing `RUNNER_DONE.lock` or setting runner state to done:

1. Run the project harness gate (`run_gates` from `.memory/gates.sh`).
2. Ensure harness reports success end-to-end (for this repo that is `pnpm run verify`, including lint/harness checks, structure checks, tests, and typechecks).
3. Confirm `.memory/runner/TASKS.json` has zero open/in_progress/blocked tasks.
4. Only then mark:
   - `done_candidate=true`
   - `done_gate_status=passed`
   - `status=done`

If any of the above fails, keep done state unset:
- `done_candidate=false`
- `done_gate_status=failed` (or `pending` if not validated yet)
- do not create `RUNNER_DONE.lock`

## Clear Contract

`--clear` is always two-phase:

1. phase pending: returns `confirm_token`
2. phase cleared: run again with `--confirm <token>`

On successful clear, runner context pointer is removed only if it points to the cleared root.

## Canonical Files

- `<target-root>/.memory/runner/RUNNER_STATE.json`
- `<target-root>/.memory/runner/RUNNER_LEDGER.ndjson`
- `<target-root>/.memory/runner/PRD.json`
- `<target-root>/.memory/runner/TASKS.json`
- `<target-root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`
- `<target-root>/.memory/runner/RUNNER_WATCHDOG.json` (operational heartbeat)
- `<target-root>/.memory/runner/RUNNER_CYCLE_PREPARED.json` (ephemeral handoff marker)
- `<target-root>/.memory/runner/RUNNER_TASK_INTAKE.json` (queued user-added backlog to merge on setup refresh)
- `<target-root>/.memory/runner/RUNNER_ENABLE.pending.json` (until approval)
- `<target-root>/.memory/runner/RUNNER_CLEAR.pending.json` (during clear flow)
- `<target-root>/.memory/gates.sh` (`run_gates` required)
- `<target-root>/.memory/lessons.md`

Notes:

- `TASKS.json` is canonical for execution and done checks.
- `lessons.md` is narrative memory, not a task control plane.

Pointer file:

- `$DEV/Repos/<project>/.memory/RUNNER_CONTEXT.json`

## Examples

```bash
# Preferred in active worktree
python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --project-root "$PWD" --runner-id main

# Approve enable token (if output included one)
python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --project-root "$PWD" --runner-id main --approve-enable <TOKEN>

# Project shorthand (reuses pointer/root context)
python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --project time-track-platform --runner-id main

# Clear flow
python3 /Users/jian/Dev/tmux-codex/bin/runctl --clear --project-root "$PWD" --runner-id main
python3 /Users/jian/Dev/tmux-codex/bin/runctl --clear --project-root "$PWD" --runner-id main --confirm <TOKEN>
```
