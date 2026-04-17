# /run_govern - Govern deterministic ORX/Linear runner state

Use this command to govern infinite-runner state after one execute slice finishes.

tmux-codex may render and inject this prompt body directly when Codex custom slash commands are unavailable in the live TUI. The instruction contract is the same either way.

This is the autonomous seam-governor prompt. It keeps the loop moving without wiping runner memory or rebuilding from scratch.

Optional runner context from `/run_govern` args:
- optional `PROJECT_ROOT=$PROJECT_ROOT`

In normal runner dispatch, the command is intentionally compact:
- `/run_govern`
- the active session cwd and runner state are the primary source of truth

## Scope First

Resolve target root in this order:
1. current working directory
2. explicit `PROJECT_ROOT`

`cd` to that root before doing anything else.

## Governing Contract

Read:
- `<target_root>/.memory/runner/OBJECTIVE.json`
- `<target_root>/.memory/runner/SEAMS.json`
- `<target_root>/.memory/runner/GAPS.json`
- `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`
- `<target_root>/.memory/runner/RUNNER_ACTIVE_BACKLOG.json`
- graph summaries only when the active seam boundary is ambiguous:
  - `RUNNER_GRAPH_BOUNDARIES.json`
  - `RUNNER_GRAPH_HOTSPOTS.json`

This prompt owns semantic backlog repair and seam governance only. The controller will run deterministic `runctl --setup` and `runctl --prepare-cycle` after this prompt exits.

Strictness rules during govern:

- preserve the objective, runner identity/runtime enablement, and overall project direction
- treat `OBJECTIVE.json` as fixed intent, `SEAMS.json` as flexible plan, and the active seam as strict scope
- preserve the active seam by default until its acceptance is fully cleared
- do not widen the active seam just because execution found a tempting side quest
- every real discovered gap must end up classified in `GAPS.json` or folded into the active seam metadata; do not drop it on the floor
- if the discovered work blocks current seam correctness and still fits the same owner problem, merge it into the active seam
- if the discovered work blocks current seam correctness but needs a distinct owner problem or write set, create a prerequisite seam
- if the discovered work is real but non-blocking, defer it without preempting the active seam
- reject cosmetic or opportunistic cleanup that does not materially advance the objective
- keep the seam queue sharp: one actionable frontier, explicit blocked follow-ons, and no umbrella seams that bundle unrelated families
- preserve or strengthen acceptance and validation contracts; never relax them
- preserve `model_profile`, `touch_paths`, `validation_commands`, `deprecation_phase`, `fanout_risk`, and `coupling_notes` unless execution proved the seam boundary wrong
- preserve `parity_baseline_ref`, `parity_surface_ids`, `parity_audit_mode`, and `parity_harness_commands` by default
- if the active seam became too broad for one clean slice, split it into the smallest truthful child seams and keep only one child actionable
- if execution proved the active seam boundary wrong, reseed only the active open seam set in place; do not wipe completed history
- if graph artifacts exist, use them as evidence for `split` or prerequisite decisions before broad repo rescans
- if active work collapses to zero open seams but the objective has not yet passed explicit done-closeout `run_gates`, create or reopen exactly one closeout seam instead of allowing the runner to infer `done`
- do not directly edit `RUNNER_STATE.json`, `RUNNER_EXEC_CONTEXT.json`, `RUNNER_HANDOFF.md`, or `RUNNER_CYCLE_PREPARED.json` from this prompt

## Output

Keep output compact:
- `state_repaired=<yes|no>`
- `scope_status=<ok|narrow|split|reseed>`
- `decision=<continue_seam|complete_seam|split_seam|create_prereq_seam|defer_gaps_and_continue|objective_complete|halt>`
- `exiting=<yes>`

Terminate this Codex chat session immediately after govern finishes.
