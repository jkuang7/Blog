Use this command to prepare Codex infinite-runner state for the current repo/worktree.

Runner context from `/prompts:run_setup` args:
- `DEV=$DEV`
- `PROJECT=$PROJECT`
- `RUNNER_ID=$RUNNER_ID`
- `PWD=$PWD`
- optional `PROJECT_ROOT=$PROJECT_ROOT`
- optional approval token forms:
  - `--approve-enable <token>`
  - `APPROVE_ENABLE=<token>`

## Scope First

Resolve target root in this order:
1. explicit `PWD`
2. explicit `PROJECT_ROOT`
3. explicit `PROJECT` via `$DEV/Repos/$PROJECT`
4. current working directory

`cd` to that root before doing anything else.

## Objective Seeding

Before running setup without an approval token, inspect the setup inputs:

- the current user request in this conversation
- `<target_root>/.memory/runner/PRD.json` if it exists
- `<target_root>/.memory/runner/TASKS.json` if it exists
- `<target_root>/.memory/PRD.md` if it exists
- any directly relevant repo planning doc explicitly referenced by the user

Treat the following as generic or stale and replace them for the new setup run:

- objective titles like `Establish the active objective...` or `<project> runner objective`
- a single boilerplate `TT-001` task about executing the next validated slice
- a previously finished objective when the user has clearly asked for a new one
- setup state that no longer matches the current request or current blocker

When setup inputs are missing, generic, or stale, create fresh concrete files after clear and before setup:

- write `<target_root>/.memory/runner/PRD.json`
- write `<target_root>/.memory/runner/TASKS.json`

Use the latest explicit user goal as the source of truth. Distill broad notes into:

- one concrete objective title
- 1-3 bounded open tasks
- acceptance and validation that name the real blocker or parity target

For complicated parity, migration, or regression-restoration work:

- seed acceptance as fail-closed, not approximate
- if the user asks for parity with an older baseline, record the exact baseline commit or artifact in the acceptance/validation text
- require explicit side-by-side comparison or equivalent concrete proof for parity tasks
- do not seed vague criteria such as `looks polished`, `matches old styling`, or `restore parity`; name the exact surfaces and the no-known-delta requirement instead
- make successful completion criteria explicit enough that a later runner cycle can tell the difference between exact parity and "closer but still off"

Task seeding must be narrow enough for a single runner slice:

- `TT-001` must name the first executable surface, blocker, or file cluster to touch first
- each seeded task must be completable or decisively re-scopable within one bounded runner iteration
- split umbrella work into ordered tasks instead of one broad task

Do not seed broad task titles such as:

- `Clean up setup files and recreate only the necessary setup surface`
- `Restore desktop parity`
- `Fix archive behavior`
- `Continue refactor`

Instead, name the first concrete slice, for example:

- `Audit Panda/Tailwind/PostCSS entrypoints and remove duplicate setup hooks`
- `Restore DesktopMainPanes spacing contract to HEAD wrapper layout`
- `Reproduce Archive Current Tab bundling against HEAD and identify whether the gap is store or presentation`

Do not preserve generic boilerplate when the user has already provided a specific plan.
Do not preserve broad umbrella tasks when the user has already provided enough detail to split them.
Do not preserve weak acceptance criteria when the user has described a strict parity target.

## Command

If no approval token is present, do a fresh reset before setup:

1. Run:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --clear --project-root <target_root> --runner-id main
```

2. Read the returned `confirm_token`.
3. Run:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --clear --project-root <target_root> --runner-id main --confirm <confirm_token>
```

4. If objective seeding is needed, write the concrete `PRD.json` and `TASKS.json` now.

5. Then run:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --setup --project-root <target_root> --runner-id main
```

Never fabricate the clear token. Use only the token returned by the first clear command.

If an approval token is present, skip clear and run only:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --setup --project-root <target_root> --runner-id main --approve-enable <token>
```

## After Setup

Inspect:
- `<target_root>/.memory/runner/runtime/RUNNER_STATE.json`
- `<target_root>/.memory/runner/TASKS.json`

Confirm:
- `.memory/runner/locks/RUNNER_DONE.lock` is absent unless state is truly done
- `status` is `ready` or `running`
- `next_task_id` is non-empty
- `next_task` is non-empty
- `TASKS.json` contains the same task id/title and it is actionable

## Output

Keep output compact:
- executed commands
- target root
- clear state if relevant
- seeded objective title if relevant
- seeded first task title if relevant
- approval state if relevant
- status
- next_task_id
- next_task
- setup ready message

Do not start the runner from this prompt. The next manual step is:
- `cl`
- `r=runner`
