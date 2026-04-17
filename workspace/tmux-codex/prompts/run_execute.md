# /run_execute - Execute one ORX-managed runner slice

Use this command to execute exactly one medium bounded infinite-runner work slice.

tmux-codex may render and inject this prompt body directly when Codex custom slash commands are unavailable in the live TUI. The instruction contract is the same either way.

Optional runner context from `/run_execute` args:
- optional `PROJECT_ROOT=$PROJECT_ROOT`
- optional `PHASE=$PHASE` (`discover|implement|verify|closeout`)

## Scope First

Resolve target root in this order:
1. current working directory
2. explicit `PROJECT_ROOT`

`cd` to that root before doing anything else.

## Execution Contract

The injected `## ORX Execution Packet` is the live source of truth for this slice.

Use it for:
- issue identity
- repo root
- worktree path
- branch
- lane ownership (`owning_bot`, `assigned_bot`, `feature_lane`) when present
- UI routing state (`ui_mode`, `design_state`, `ui_evidence_required`, `design_reference`)
- execution model / reasoning effort
- execution brief
- latest handoff
- verification commands
- packet/worktree policy

Do not reconstruct intent from local runner planner files.

In the normal ORX path, do not read these files to decide what to do:
- `.memory/runner/OBJECTIVE.json`
- `.memory/runner/SEAMS.json`
- `.memory/runner/GAPS.json`
- `.memory/runner/TASKS.json`
- `.memory/runner/RUNNER_EXEC_CONTEXT.json`
- `.memory/runner/RUNNER_ACTIVE_BACKLOG.json`
- `.memory/runner/graph/RUNNER_GRAPH_ACTIVE_SLICE.json`

Those files may exist for migration or recovery, but they are not the planner of record.

Your job is narrow:
- execute one bounded slice for the active ORX issue
- stay within the declared scope
- verify only on the declared surface unless execution proves a different check is required
- report facts for ORX to interpret

## Working Rules

- Treat the current Linear ticket plus the injected `Latest Handoff` as the complete resume surface.
- Prefer the injected `execution_brief` over broad repo exploration.
- Treat packet lane ownership as authoritative when it is present; do not burn the slice rediscovering which bot or lane owns the work unless the packet and live repo facts disagree.
- Stay within the current worktree and branch intent.
- Do not split the ticket, create new tickets, reroute work, or decide what should happen next.
- If `ui_mode` is `visual` and `design_state` is `pending`, do design prep only: use the `stitch-first-ui` skill, gather design artifacts, and stop for ORX review before production UI implementation edits.
- If `ui_evidence_required` is `true`, verify on the live UI surface with Playwright before claiming success. If that is not possible, return a blocked result with the missing evidence called out explicitly.
- If you hit a blocker, ownership mismatch, or scope mismatch, stop and report it precisely.
- If verification is not run, say why.
- Keep the slice cohesive. One real unit of progress is enough.

## Output

Keep output compact and operational.

End with this exact machine-parsable block:

RUNNER_RESULT_START
{"status":"success","summary":"Implemented the bounded slice and updated the affected files.","verified":false,"next_slice":null,"artifacts":["path/to/file"],"design_artifacts":[],"design_reference":"","design_review_requested":false,"verification_surface":"none","metrics":{},"blockers":[],"risks":[],"lessons":[],"verification_ran":[],"verification_failed":[],"touched_paths":["path/to/file"],"next_step_hint":"","owner_mismatch":false,"scope_mismatch":false,"needs_human_help":false}
RUNNER_RESULT_END

Rules:
- `status` must be `success`, `blocked`, or `failed`
- `summary` must state what changed or why progress stopped
- never leave placeholder literals like `...`, `TODO`, `TBD`, `placeholder`, or `N/A` in any field
- `verified` must be boolean
- `next_slice` may be `null` or a short string if more work remains on the same issue
- `artifacts`, `blockers`, `risks`, `lessons`, `verification_ran`, `verification_failed`, and `touched_paths` must be arrays of strings
- `design_artifacts` must be an array of strings when present
- `verification_surface` may be `none`, `cli`, `playwright`, or `mixed`
- `design_review_requested` must be boolean when present
- `design_reference` may be empty, but when present it should point to the design artifact ORX should review or implement against
- `metrics` must be a JSON object
- `next_step_hint` may be empty, but if present it should be factual and narrow
- `owner_mismatch`, `scope_mismatch`, and `needs_human_help` must be booleans
- terminate this Codex chat session immediately after emitting the block
