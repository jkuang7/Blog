Use this command to refresh infinite-runner state after one execute slice finishes.

Runner context from `/prompts:run_update` args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- optional `PROJECT_ROOT=$PROJECT_ROOT`

## Scope First

Resolve target root in this order:
1. explicit `PWD`
2. explicit `PROJECT_ROOT`
3. explicit `PROJECT` via `$DEV/Repos/$PROJECT`
4. current working directory

`cd` to that root before doing anything else.

## Command

Refresh runner memory once:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --setup --quiet --project-root <target_root> --runner-id main
```

Strictness rules during refresh:

- preserve or strengthen the active task acceptance/validation contract; never relax it
- if the active objective/task is parity, regression-restoration, or baseline-matching work, the refreshed state must remain fail-closed
- do not refresh vague wording such as `looks right` or `matches old styling`; keep explicit baseline-comparison and no-known-delta criteria in place
- if the execute slice did not fully clear the parity delta, keep the task open and carry forward the exact remaining blocker

Write prepared marker:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --prepare-cycle --quiet --project-root <target_root> --runner-id main
```

## Output

Keep output compact:
- `state_refreshed=<yes|no>`
- `prepared_marker=<yes|no>`
- `exiting=<yes>`

Terminate this Codex chat session immediately after the update commands finish.
