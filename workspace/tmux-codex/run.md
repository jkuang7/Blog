# Runner Prompt Contract

This contract now assumes a deterministic ORX/Linear control plane:

- ORX selects the active or next runnable Linear issue
- tmux-codex provisions the issue worktree and seeds local runner state from that issue
- `run_execute.md` and `run_govern.md` operate on that seeded issue/worktree context
- local runner files are execution continuity, not the canonical task graph

The runner control plane is fully decoupled into three prompts:

1. `/run_setup`
2. `/run_execute`
3. `/run_govern`

`cl -> r=runner` must always start with the rendered `run_execute` prompt body.
It dispatches the rendered `run_govern` prompt body in the same live session when execute explicitly requests semantic task-state changes or when scripted refresh cannot produce the prepared marker and the current model profile is already compatible. Otherwise it relaunches a fresh session for the required update profile.

Codex custom command files may still be installed for manual use, but the infinite runner must not depend on Codex exposing those commands in the live TUI.

## Minimal User Flow

From inside the target repo or active worktree:

1. `/run_setup`
2. approve enablement if setup returns a token
3. start the runner from `cl` / TUI with `r=runner`

Runner start is intentionally decoupled:
- `r=runner` is launch-only
- it must not run setup
- it must not clear state
- it must not auto-approve enablement
- if setup is missing or not approved, it must fail fast and tell the user to run `/run_setup`

## Prompt Ownership

- `/run_setup`
  - clear-then-setup on normal setup runs
  - setup-only on `--approve-enable <token>` reruns
  - creates fresh runner state and a deliberately narrow backlog
  - human reset/rebuild path
  - tmux-codex runtime must not dispatch this autonomously
  - may return an enable approval token
- `/run_execute`
  - execute-only worker prompt
  - one medium bounded phase iteration
  - validate the active surface
  - terminate the session
- `/run_govern`
  - semantic post-execute governor prompt
  - used when execute reported task-state changes that need modeled rewriting or when scripted refresh could not hand off the next cycle
  - performs in-place backlog repair without wiping runner memory
  - treats `OBJECTIVE.json`, `SEAMS.json`, and `GAPS.json` as the runtime bundle
  - terminate the session

Deprecated:
- `/run`
  - removed from the normal runner flow
  - retained only as a temporary migration alias while old sessions age out

## Non-Negotiables

- Single runner id: `main`
- Source of truth:
  - `<target-root>/.memory/runner/runtime/RUNNER_STATE.json`
  - `<target-root>/.memory/runner/OBJECTIVE.json`
  - `<target-root>/.memory/runner/SEAMS.json`
  - `<target-root>/.memory/runner/GAPS.json`
  - `<target-root>/.memory/runner/RUNNER_PARITY.json`
- Prepared handoff marker:
  - `<target-root>/.memory/runner/runtime/RUNNER_CYCLE_PREPARED.json`
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

Before `cl loop <project>` launches a runner, tmux-codex also asks ORX for the current Linear issue context for that project.
If ORX returns an active issue or a next runnable issue, tmux-codex must:

1. ensure the issue worktree exists
2. write the ORX/Linear issue snapshot into the local runner control plane
3. seed `KANBAN_STATE.json`, `RUNNER_EXEC_CONTEXT.json`, and `RUNNER_HANDOFF.md` from that issue
4. start the runner inside that issue worktree

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
- for migrations, refactors, and parity-sensitive UI work, seed `RUNNER_PARITY.json` with one explicit safe baseline commit plus the smallest truthful audited surface set
- use the latest explicit user request as the source of truth for the seeded objective and bounded tasks
- seeded tasks must be narrow enough for one bounded runner slice; do not seed umbrella tasks when the request already names smaller concrete surfaces
- seeded tasks should be concise and decoupled: one primary family, one seam, one validation path, and explicit blocked follow-ons for the rest
- for dependency-heavy migrations, `run_setup` is the strong planning gate: it should split work by file family, safe deprecation phase, and dependency containment before the infinite runner starts
- `run_setup` should score slice construction in this order: dependency correctness, deprecation sequencing, decoupling, independent executability, then smallest valid complexity
- `/run_setup` is intentionally reset-oriented: it may clear and regenerate memory files from repo state plus conversation context
- seeded active tasks should carry `model_profile`, `profile_reason`, `touch_paths`, `validation_commands`, `deprecation_phase`, `fanout_risk`, and optional `spillover_paths`
- risky migration/refactor tasks should also carry `parity_baseline_ref`, `parity_surface_ids`, `parity_audit_mode`, and optional `parity_harness_commands`
- refresh `.memory/runner/*`
- refresh `RUNNER_EXEC_CONTEXT.json`
- refresh `RUNNER_ACTIVE_BACKLOG.json`
- refresh graph artifacts when repo graph support is enabled:
  - `.memory/runner/graph/RUNNER_DEP_GRAPH.json`
  - `.memory/runner/graph/RUNNER_GRAPH_ACTIVE_SLICE.json`
  - `.memory/runner/graph/RUNNER_GRAPH_BOUNDARIES.json`
  - `.memory/runner/graph/RUNNER_GRAPH_HOTSPOTS.json`
- keep `active_seam_id`, `next_task_id`, and `next_task` aligned with the prepared seam frontier
- when graph support is enabled, default to a one-open actionable frontier unless the plan intentionally permits parallel starts
- write `RUNNER_HANDOFF.md`

Clear requirements:
- remove runner-managed state safely
- remove `RUNNER_HANDOFF.md`
- remove `.memory/PRD.md`
- remove legacy `REFRACTOR_STATUS.md` if present

## Execute Contract

`run_execute.md` is the default prompt template `r=runner` should drive each cycle.
`run_govern.md` is conditional and should run only when semantic refresh is required.
`/run_setup` is for human reset/rebuild only.

`/run_execute` must:
- read `.memory/runner/runtime/RUNNER_STATE.json`
- read `RUNNER_EXEC_CONTEXT.json`
- read `RUNNER_ACTIVE_BACKLOG.json`
- optionally read `.memory/runner/graph/RUNNER_GRAPH_ACTIVE_SLICE.json`
- rely on `graph_slice_reason` in exec context as the compact explanation of why this exact cluster/seam is the current frontier
- use `parity_baseline_ref` plus targeted `parity_surface_ids` to keep UI/UX parity audits diff-first and small
- resolve phase from explicit `PHASE` or exec context
- work only within the current phase goal and one coherent medium slice
- keep the slice small enough that a second independent family would be a scope failure, but detailed enough that the next step is obvious from metadata alone
- treat `RUNNER_HANDOFF.md` as human/manual recovery context rather than default per-cycle input
- keep full graph artifacts off the hot path; normal execute cycles must not load `RUNNER_DEP_GRAPH.json`, `RUNNER_GRAPH_BOUNDARIES.json`, or `RUNNER_GRAPH_HOTSPOTS.json`
- do not run broad parity audits every cycle; diff the slice against the safe baseline first, then use targeted MCP reads on only the affected declared surfaces
- treat medium slices as execution chunks while keeping the same active seam until its acceptance is fully satisfied
- strengthen the active seam contract when new in-scope acceptance criteria are discovered instead of silently carrying hidden requirements
- avoid setup/clear behavior
- terminate immediately

`/run_govern` must:
- repair semantic backlog state in `SEAMS.json` / `GAPS.json` and only touch objective wording when objective-level intent truly changes
- preserve the objective and completed history while repairing the active open backlog in place
- keep the backlog sharp: one open frontier, explicit blocked follow-ons, and no umbrella tasks that bundle unrelated families
- preserve the same active seam on partial progress and only advance when acceptance is actually cleared or the seam must be split into explicit independent blockers
- default to `mini`, but escalate to `high` when the repair requires broader reseeding, dependency rewrites, or model-profile reclassification
- use graph summaries for `split|reseed` when graph support is enabled, but keep the full graph out of ordinary `ok|narrow` refreshes
- reseed broad work into graph-local slices and keep one actionable `open` task when a single next frontier is obvious
- keep downstream unmet-dependency tasks `blocked` instead of leaving them `open` for visibility
- not directly edit `RUNNER_STATE.json`, `RUNNER_EXEC_CONTEXT.json`, `RUNNER_HANDOFF.md`, or `.memory/runner/runtime/RUNNER_CYCLE_PREPARED.json`
- terminate immediately

No-progress recovery rule:
- if a cycle cannot produce durable progress, do not keep retrying the same broad task
- run `/run_govern`
- let `run_govern` tighten, split, or reseed the active backlog in place without clearing full runner memory
- prefer the smallest truthful recovery: tighten blocker first, split only when families are mixed, and reseed only when the task boundary is genuinely wrong
- treat coupling as a split trigger: if progress would require crossing into a second family or seam, stop widening and let `run_govern` keep that family separate in the next backlog shape

Graph support:
- graph generation is controller/script-owned and opt-in per repo via context-pack metadata
- `runctl --setup` may generate cached graph artifacts for supported repos, but prompts must never generate graph artifacts themselves
- full graph reasoning belongs to `/run_setup` and `/run_govern`, not normal execute cycles
- when graph support is unavailable or stale, runner behavior must fall back to the current non-graph flow without blocking startup

Fail-closed rules:
- do not hand off after a no-op inspection cycle
- if nothing concrete changed, narrow the blocker on the active seam first
- do not create `.memory/runner/locks/RUNNER_DONE.lock` while open seams remain
- if active work collapses to zero open seams without an explicit completed done-closeout frontier, synthesize or reopen one final done-closeout seam instead of inferring completion
- only write `.memory/runner/locks/RUNNER_DONE.lock` after that explicit done-closeout seam passes `run_gates`
- if final gates fail or cannot run, reopen closeout with one concrete blocker task and keep the infinite runner moving

## Runner Start Contract

`cl -> r=runner` and `cl loop <project>`:
- start an interactive Codex CLI pane
- resolve the active seam profile at the start of each cycle
- route `model_profile=mini` to the cheaper execution model and `model_profile=high` to `gpt-5.4` with `high`
- launch an internal controller per cycle
- controller dispatches the rendered `run_execute` prompt first
- after execute, controller scripts deterministic refresh itself with `runctl --setup` and `runctl --prepare-cycle` when no semantic update is needed
- when execute reports `needs_update=yes`, controller dispatches the rendered `run_govern` prompt in the same Codex session when that session already matches the requested update profile; otherwise it relaunches a fresh session for the required profile
- when scripted refresh fails to produce the prepared marker, controller likewise prefers same-session `run_govern` prompt dispatch to preserve execute context and only relaunches when the update profile is incompatible with the current session
- after govern, controller again scripts deterministic refresh itself with `runctl --setup` and `runctl --prepare-cycle`
- after scripted refresh or conditional update, controller exits the current Codex session so a fresh TUI session is launched for the next cycle
- the tmux-codex supervisor archives completed runner threads itself between cycles; prompts must not rely on `::archive`
- when setup refresh sees zero open seams but no completed explicit done-closeout seam, it must create or reopen one final closeout frontier instead of stopping
- the infinite runner should keep healing through seam repair, validation repair, and explicit closeout until completion is both semantically and operationally safe
- if a `mini` `run_govern` still cannot produce the prepared marker, controller retries `run_govern` on `high`
- if a `high` `run_govern` still cannot produce the prepared marker because no durable progress was recorded, controller queues a high-priority recovery triage task, refreshes state, and relaunches fresh on that recovery slice
- if that deterministic no-progress recovery still cannot refresh cleanly, or if the refresh failed for a different reason, controller stops in an explicit error state instead of hanging or spinning forever
- controller never dispatches setup or clear

## Prompt Install Contract

Installed commands in `~/.codex/commands`:
- `run_setup.md`
- `run_execute.md`
- `run_govern.md`
- `add.md`
- optional aliases:
  - `run-setup.md`
  - `run-execute.md`
  - `run-govern.md`
  - `runner-add.md`

Compatibility links may also exist under `~/.codex/prompts`, but the live infinite runner still treats prompt files as deterministic templates and does not require Codex to surface them as slash commands.

Removed legacy prompt files (cleanup only):
- `run.md`
- `run_clear.md`
- `runner-cycle.md`
- `runner-discover.md`
- `runner-implement.md`
- `runner-verify.md`
- `runner-closeout.md`
