Use this command to manage Codex infinite-runner state files.

Runner context from `/prompts:run` args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- optional `PROJECT_ROOT=$PROJECT_ROOT`
- optional `MODE=$MODE` (`execute_only` for iteration mode)
- optional `PHASE=$PHASE` (`discover|implement|verify|closeout` for execute-only fallback)

## Mode Resolver (Run First)

Determine mode before any other action:

- Resolve mode from `/prompts:run` argument text in this prompt context, not from shell env.
- Do **not** run `echo $MODE` or any shell probe to decide mode.
- If `MODE=execute_only`: run **Execute-Only Path** below and stop.  
  Do not run setup/context preflight, do not load extra skill/context files, and do not do setup-only workflow.
- If mode is ambiguous but this invocation came from watchdog idle injection (`/prompts:run ... PROJECT_ROOT=... RUNNER_ID=main`), treat it as `execute_only`.
- Otherwise: run **Setup/Clear Path** below.

## Fast Exit Override

If the user explicitly asks to terminate/end this Codex chat session so wrapper/watchdog can respawn:
- do not run setup
- do not load context files
- do not execute implementation work
- terminate immediately

## Execute-Only Path (`MODE=execute_only`)

Preferred lightweight entrypoint:
- `/prompts:runner-discover ... MODE=execute_only`
- `/prompts:runner-implement ... MODE=execute_only`
- `/prompts:runner-verify ... MODE=execute_only`
- `/prompts:runner-closeout ... MODE=execute_only`
- `/prompts:run ... MODE=execute_only PHASE=<phase>` remains a compatibility fallback.

- Resolve `<target_root>` from `PWD` first, then `PROJECT_ROOT`.
- Read `<target_root>/.memory/runner/RUNNER_STATE.json`.
- Read `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`.
- Resolve phase from `PHASE=<phase>` when present; otherwise use `phase` from `RUNNER_EXEC_CONTEXT.json`.
- Load only the compact `context_sources` and `context_delta` from `RUNNER_EXEC_CONTEXT.json` before extra repo reads.
- Execute within the current `phase_goal` and one coherent work surface.
- Run validation for the active phase surface only (avoid full preflight unless closeout explicitly requires it).
- Fail closed on zero-progress cycles:
  - do not treat read-only inspection, state refresh, or prompt restatement as a completed step,
  - do not write `RUNNER_CYCLE_PREPARED.json` after a no-op pass,
  - if the cycle only discovers a narrower blocker, update `TASKS.json` first and then refresh state so the next task or phase changes concretely.
- If the same phase / `next_task_id` / blocker state would survive this cycle unchanged, rewrite it to the exact failing surface or mark it blocked before handoff.
- Refresh memory once:
  - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main`
- Write prepared marker via canonical command:
  - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main`
  - marker file: `<target_root>/.memory/runner/RUNNER_CYCLE_PREPARED.json`
- Terminate this Codex chat session immediately.

Output contract (token efficiency):
- Keep updates compact and operational.
- Do not reprint full scope-lock/setup docs in the iteration response.
- End with a short checklist:
  - `phase_done=<yes|no>`
  - `validation=<pass|fail>`
  - `state_refreshed=<yes|no>`
  - `prepared_marker=<yes|no>`
  - `exiting=<yes>`

Do **not** run setup-mode preflight/context loading in this mode.

## Setup/Clear Path (default)

- human-in-the-loop planning conversation first
- runctl setup/clear only after the human explicitly confirms the setup state is ready
- state inspection/alignment checks
- token approval/confirmation flow
- live backlog intake via `runctl --task add|queue` when the human wants to append new work without interrupting the active runner cycle

## Scope Lock (Mandatory)

Before any action:

1. Resolve target root exactly as `runctl` does:
   - explicit `PWD` arg, else
   - explicit `PROJECT_ROOT` arg, else
   - `$DEV/Repos/$PROJECT/.memory/RUNNER_CONTEXT.json` pointer, else
   - saved runner state discovery (worktree preferred), else
   - `$DEV/Repos/$PROJECT`
2. `cd` to that root.
3. Read/write only that root’s `.memory/*`.
4. If command output shows another root, stop and fix scope first.

## Command Mapping

When user invokes `/run`, map directly to `runctl`:

- setup/default:
  - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --setup --project-root <target_root> --runner-id main`
- clear:
  - `python3 /Users/jian/Dev/tmux-codex/bin/runctl --clear --project-root <target_root> --runner-id main`
  - confirm via returned token

Rules:
- never fabricate tokens
- never delete runner files manually
- default `/prompts:run` is planning/HIL mode, not autonomous execution mode
- start with an extensive planning conversation:
  - clarify objective, success criteria, repo/worktree scope, and setup constraints
  - ask follow-up questions until the human is satisfied with the setup shape
  - propose the setup file conditions that should exist in `.memory/runner/*`
- only run `runctl --setup` after the human explicitly confirms that the plan/setup state is correct
- `/run` does not start loop sessions unless user explicitly asks
- do not tell the watchdog to continue and do not ask the user to auto-start the runner from `/prompts:run`
- once setup is ready, leave runner start as a separate manual action in `cl` / TUI (`r=runner`)
- when the human adds more work while the runner is already active, prefer `runctl --task add ...` so new tasks land in `RUNNER_TASK_INTAKE.json` and merge on the next setup refresh without stomping the current exec context
- if active scope is worktree, never switch to canonical root
- setup builds phase-scoped `RUNNER_EXEC_CONTEXT.json` with compact repo context-pack summaries plus runner delta
- during iteration execution, add/tighten validation gates when newly discovered risk justifies it
- run one extra validation pass when done-candidate confidence is high and you need a final gate check
- only treat the runner as complete when both are true:
  - `run_gates` passes, and
  - `.memory/runner/TASKS.json` has zero tasks in `open|in_progress|blocked`
- if open tasks remain in `TASKS.json`, do not create `.memory/RUNNER_DONE.lock`

## After Setup

1. Inspect `<target_root>/.memory/runner/RUNNER_STATE.json`.
2. If `<target_root>/.memory/RUNNER_DONE.lock` exists or state `status` is `done`, stop.
3. Otherwise confirm `next_task_id` + `next_task` are non-empty and aligned with `TASKS.json`.
4. Do not execute implementation work here unless `MODE=execute_only`.
5. End by telling the human setup is ready and that runner start remains a separate manual `cl` / TUI action.

## Examples

- `/run --setup --project-root /Users/jian/.codex/worktrees/3f5e/time-track-platform --runner-id main`
- `/run --setup --project-root "$PWD" --runner-id main`
- `/run --clear --project-root "$PWD" --runner-id main`
- `/run --clear --project-root "$PWD" --runner-id main --confirm <TOKEN>`
