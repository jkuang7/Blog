Use this command to prepare Codex infinite-runner state for the current repo/worktree.

This is the human reset/rebuild path. On normal runs it clears runner memory, then regenerates runner state from repo evidence plus the current conversation context.
This prompt is for the user/human setup flow only. tmux-codex runtime must not dispatch `/prompts:run_setup` autonomously.

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
- current repo state, not just stored memory:
  - `git status --short`
  - `git diff --stat` when the worktree is already dirty
  - destination seams that already exist on disk
  - legacy families that already import the destination seam
  - broad deprecation markers or compatibility wrappers already in progress

Treat the following as generic or stale and replace them for the new setup run:

- objective titles like `Establish the active objective...` or `<project> runner objective`
- a single boilerplate `TT-001` task about executing the next validated slice
- a previously finished objective when the user has clearly asked for a new one
- setup state that no longer matches the current request or current blocker

When setup inputs are missing, generic, or stale, create fresh concrete files after clear and before setup:

- write `<target_root>/.memory/runner/OBJECTIVE.json`
- write `<target_root>/.memory/runner/SEAMS.json`
- write `<target_root>/.memory/runner/GAPS.json`
- write `<target_root>/.memory/runner/PRD.json`
- write `<target_root>/.memory/runner/TASKS.json`
- write `<target_root>/.memory/runner/KANBAN_STATE.json` as additive ticket-native continuity state during the migration away from file-managed planning
- write `<target_root>/.memory/runner/RUNNER_PARITY.json` when the objective is a migration, refactor, parity-sensitive UI change, or regression-restoration effort

Use the latest explicit user goal as the source of truth. Distill broad notes into:

- one concrete objective title
- a dependency-safe task graph with one open frontier unless two starts are truly independent and separately executable
- acceptance and validation that name the real blocker or parity target
- enough detail for the next runner cycle to execute without re-reading chat history
- concise task text that names the first file family, seam, or blocker instead of broad architecture labels

For dependency-heavy migration work, `run_setup` is the strong human-invoked planning gate:

- assume `run_setup` is the expensive planning step and use `gpt-5.4` with `high` reasoning here
- do the dependency analysis here before the infinite runner starts
- order setup decisions explicitly:
  1. fix the dependency chain
  2. fix sequencing by safe deprecation phase
  3. decouple mixed families and seams
  4. choose the next independently executable slice
  5. minimize slice complexity inside that safe boundary
- split work by independent file family and safe deprecation phase instead of broad architecture labels
- if a slice cannot be completed safely by a cheaper model without crossing seams or widening scope, mark it `high`
- only mark a slice `mini` when it is dependency-contained and does not need architectural judgment
- `high` is not permission for a broad umbrella task; it still must stay within one dominant family and one seam
- respect safe deprecation order: `seam` -> `shim` -> `consumer_migration` -> `convergence` -> `removal`
- if graph artifacts exist under `.memory/runner/graph/`, use them as the first structural hint before broad repo scans:
  - `RUNNER_DEP_GRAPH.json`
  - `RUNNER_GRAPH_BOUNDARIES.json`
  - `RUNNER_GRAPH_HOTSPOTS.json`
- when graph artifacts are present, derive `touch_paths`, `spillover_paths`, `coupling_notes`, and `fanout_risk` from actual dependency neighborhoods instead of folder-name intuition alone

Deep-analysis grouping rules:

- derive candidate slices from repo evidence, not just prose:
  - current dirty worktree concentration
  - dominant file family
  - dominant import/dependency cluster
  - destination seam/library
  - safe-deprecation phase
- if current uncommitted changes already concentrate on one migration family, prefer stabilizing that in-flight family first instead of seeding a brand-new unrelated slice
- if the repo already has broad deprecation comments without a corresponding destination seam, seed a stronger seam-or-readiness task before cheap consumer-migration slices
- prefer one destination seam and one file family per seeded task
- if a candidate slice crosses more than one major ownership seam or more than two dependency clusters, split it or mark it `high`
- if a candidate slice mixes seam creation with removal, split it into separate phased tasks
- if a candidate slice would require touching files outside its declared scope to finish cleanly, split it before setup completes
- if a slice is likely to grow into multiple independent families, seed only the first family and keep the rest blocked
- if multiple truthful decompositions are possible, choose the lowest-complexity slice that still has a real chance of completion in one cycle
- if unrelated dirty files are present, keep them out of `touch_paths` and call them out explicitly in `coupling_notes` rather than silently absorbing them into the slice
- if the human is rerunning setup after a stalled runner, do not reseed the same broad task unchanged; narrow or split the stalled task before the loop resumes
- use the smallest regrouping needed to restore momentum; do not oversplit healthy tasks that are still producing durable progress
- preserve a healthy task unchanged when its family boundary is still correct and only the blocker needs tighter wording
- if a stalled task still says `finish remaining`, `finish the rest`, or otherwise bundles multiple file families, replace it with narrower child tasks instead of preserving the umbrella wording
- if a stalled task mixes hook/store families such as `useAppLayoutStore*` plus `useAppStore*`, split those families before re-enabling the loop
- if the current dirty worktree concentration is already centered on one of those families, seed that real in-flight family first instead of forcing another wide task boundary
- do not seed one task across multiple dominant dependency clusters just because the files are nearby; if coupling notes point to a second family or seam, split at that boundary before setup completes
- if graph boundaries disagree with folder names, favor the active dirty cluster but record the graph conflict explicitly in `coupling_notes`

Model-efficient seeding rules:

- keep active seam wording compact and operational, not historical
- prefer active backlog only: collapse historical done-task diaries out of `TASKS.json` when reseeding and rewire remaining dependencies to the live backlog
- cap seeded acceptance to at most 2 lines
- cap seeded validation to at most 2 lines plus exact `validation_commands`
- prefer at most 12 production files and 8 test files per `mini` slice
- prefer at most 3 explicit `validation_commands` per task
- keep each task understandable from its title plus metadata alone; do not pack multiple backlog items into one task description
- do not carry long closure diaries or repeated slice evidence into active open tasks; keep that in ledger/handoff instead
- keep `TASKS.json`, `RUNNER_EXEC_CONTEXT.json`, and `RUNNER_STATE.json` aligned to the same active slice set after reseeding

For complicated parity, migration, or regression-restoration work:

- seed acceptance as fail-closed, not approximate
- make acceptance fail-closed rather than approximate or implied
- if the user asks for parity with an older baseline, record the exact baseline commit or artifact in the acceptance/validation text
- resolve one explicit safe baseline commit for the objective; default to the current clean `HEAD` at setup time when the human did not name a safer baseline
- never use the current dirty worktree as the parity baseline
- write that baseline plus compact surface metadata into `RUNNER_PARITY.json`
- keep parity metadata small:
  - objective-level `baseline_ref`, `baseline_label`, `policy`, and `surfaces`
  - per-task `parity_surface_ids`, `parity_audit_mode`, and optional `parity_harness_commands`
- derive `parity_surface_ids` from `touch_paths`, graph hints, and the actual UI/layout owners most likely to drift
- prefer the smallest truthful surface set; do not seed repo-wide parity audits
- for desktop layout or native-window-sensitive tasks, prefer `Playwright MCP` plus `Tauri MCP`
- for web-only UI surfaces, prefer `Playwright MCP`
- for non-UI slices, prefer `diff_only` parity mode or no parity surfaces at all
- require explicit side-by-side comparison or equivalent concrete proof for parity tasks
- do not seed vague criteria such as `looks polished`, `matches old styling`, or `restore parity`; name the exact surfaces and the no-known-delta requirement instead
- make successful completion criteria explicit enough that a later runner cycle can tell the difference between exact parity and "closer but still off"
- if a task can regress only one or two named surfaces, cap the parity scope there and include only the exact harness commands needed to re-check those surfaces
- seed validation so later execute slices can start with `git diff` against the baseline commit, then use targeted MCP reads of the affected DOM/component tree/window geometry only where the diff and dependency slice say drift is plausible

For subjective polish or UX work that is not baseline matching:

- keep the task fail-closed by naming the exact surface, evidence path, and no-known-issue bar instead of vague polish language

Task seeding must be narrow enough for a single runner slice:

- `TT-001` must name the first executable surface, blocker, or file cluster to touch first
- never seed a boilerplate generic `TT-001`; if you cannot name the concrete first slice, keep reading repo/conversation evidence until you can
- each seeded task must be completable or decisively re-scopable within one bounded runner iteration
- split umbrella work into ordered tasks instead of one broad task
- group file changes so each task owns one coherent file family and one destination seam
- if a second independent family exists, create a blocked follow-on instead of widening `TT-001`
- if the request clearly contains multiple independent deliverables, seed them as separate tasks with explicit dependency order instead of compressing them into one general objective
- leave exactly one actionable `open` task when one next step is obvious; keep the rest visible but `blocked`
- reject or split tasks that mix seam creation with cleanup/removal, mix app-shell with core-store, or require edits outside their proposed scope
- prefer narrower `touch_paths` over umbrella titles like `finish remaining wrappers`; if a stalled task cannot be explained as one coherent family, split it now
- `time-track` fixture rule: if a stalled task bundles both `desktop/src/app-layout/useAppLayoutStore*` and `desktop/src/app-store/useAppStore*`, reseed them as separate follow-up tasks instead of retrying the mixed slice
- treat `coupling_notes`, `fanout_risk`, and `spillover_paths` as hard evidence, not commentary: if they show another family would likely be touched, keep that family out of the slice or create a separate task for it

For every seeded task:

- set `model_profile` to `mini` or `high`
- include `profile_reason`
- include `touch_paths`
- include `validation_commands`
- include `deprecation_phase`
- include `fanout_risk`
- include `spillover_paths` when adjacent families are explicitly out of scope
- include `coupling_notes` that name the exact seam, file family, or dependency edge most likely to force spillover if the slice is not respected
- include `parity_baseline_ref`, `parity_surface_ids`, and `parity_audit_mode` for risky migrations/refactors that can regress existing UI/UX behavior
- include `parity_harness_commands` only when they are the smallest truthful checks for the touched surfaces
- make `profile_reason` explain why the slice is cheap-model-safe or why it must stay on the stronger model

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
Do not use this prompt as the infinite runner's automatic recovery step; that belongs to `/prompts:run_govern`.

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
