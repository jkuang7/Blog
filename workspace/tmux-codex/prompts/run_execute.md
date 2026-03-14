Use this command to execute exactly one medium bounded infinite-runner work slice.

Runner context from `/prompts:run_execute` args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- optional `PROJECT_ROOT=$PROJECT_ROOT`
- optional `PHASE=$PHASE` (`discover|implement|verify|closeout`)

## Scope First

Resolve target root in this order:
1. explicit `PWD`
2. explicit `PROJECT_ROOT`
3. explicit `PROJECT` via `$DEV/Repos/$PROJECT`
4. current working directory

`cd` to that root before doing anything else.

## Execution Contract

Read:
- `<target_root>/.memory/runner/runtime/RUNNER_STATE.json`
- `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`

Resolve phase from:
1. explicit `PHASE=<phase>`
2. `RUNNER_EXEC_CONTEXT.json.phase`

Load only the compact `context_sources` and `context_delta` from `RUNNER_EXEC_CONTEXT.json` before extra repo reads.

Work within:
- the current `phase_goal`
- one coherent implementation surface
- one bounded validation surface for that phase

Target shape for the slice:
- larger than a tiny "next smallest step"
- smaller than a sprawling open-ended migration
- typically a focused subsystem change, a small vertical slice, or a feature spanning a few files with tests

Rules:
- do not do setup/clear behavior here
- do not expand into broad preflight unless closeout truly requires it
- do not treat read-only inspection or prompt restatement as completed work
- if no concrete progress happened, update `TASKS.json` first to narrow the blocker before handoff
- if the same `phase / next_task_id / blocker state` would survive unchanged, rewrite it to the exact failing surface or mark it blocked
- if the active task acceptance mentions parity, baseline matching, or restoring prior behavior/styling, treat that as fail-closed: do not claim completion on approximate similarity
- for parity-style tasks, require an explicit comparison against the recorded baseline; if any known delta remains, keep the task open and name the exact remaining surface/blocker
- do not use `phase_done=yes` for a parity-style task unless the current slice actually cleared the recorded parity delta for the audited surface
- never mark a parity/regression-restoration task complete just because tests pass; tests are necessary but not sufficient when acceptance requires baseline matching

## Handoff

Do not refresh runner state from this prompt.

When the bounded work slice is done:
- stop after one coherent work surface
- report compact operational output
- terminate this Codex chat session immediately

The runner controller will invoke `/prompts:run_update` after this prompt completes.

## Output

Keep output compact and operational.

End with:
- `phase_done=<yes|no>`
- `validation=<pass|fail>`
- `exiting=<yes>`
