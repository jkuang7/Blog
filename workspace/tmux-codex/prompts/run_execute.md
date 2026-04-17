# /run_execute - Execute one deterministic ORX/Linear runner slice

Use this command to execute exactly one medium bounded infinite-runner work slice.

tmux-codex may render and inject this prompt body directly when Codex custom slash commands are unavailable in the live TUI. The instruction contract is the same either way.

Optional runner context from `/run_execute` args:
- optional `PROJECT_ROOT=$PROJECT_ROOT`
- optional `PHASE=$PHASE` (`discover|implement|verify|closeout`)

In normal runner dispatch, the command is intentionally compact:
- `/run_execute PHASE=<phase>`
- the active session cwd and runner state are the primary source of truth

## Scope First

Resolve target root in this order:
1. current working directory
2. explicit `PROJECT_ROOT`

`cd` to that root before doing anything else.

## Execution Contract

Read:
- `<target_root>/.memory/runner/OBJECTIVE.json`
- `<target_root>/.memory/runner/SEAMS.json`
- `<target_root>/.memory/runner/GAPS.json`
- `<target_root>/.memory/runner/runtime/RUNNER_STATE.json`
- `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json`
- `<target_root>/.memory/runner/RUNNER_ACTIVE_BACKLOG.json`
- `<target_root>/.memory/runner/graph/RUNNER_GRAPH_ACTIVE_SLICE.json` when it exists

Resolve phase from:
1. explicit `PHASE=<phase>`
2. `RUNNER_EXEC_CONTEXT.json.phase`

Load only the compact `context_sources` and `context_delta` from `RUNNER_EXEC_CONTEXT.json` plus `RUNNER_ACTIVE_BACKLOG.json` before extra repo reads.
Treat that context as sufficient unless the declared `touch_paths` or `validation_commands` prove otherwise.
Use `RUNNER_GRAPH_ACTIVE_SLICE.json` only as local structural context for the current task neighborhood.
Treat `RUNNER_HANDOFF.md` as human/manual recovery context, not default per-cycle input.
Do not load `RUNNER_DEP_GRAPH.json`, `RUNNER_GRAPH_BOUNDARIES.json`, or `RUNNER_GRAPH_HOTSPOTS.json` during normal execute.
Use the active seam metadata in `RUNNER_EXEC_CONTEXT.json` as hard scope:

- `model_profile`
- `touch_paths`
- `validation_commands`
- `deprecation_phase`
- `spillover_paths`
- `profile_reason`
- `coupling_notes`
- `parity_baseline_ref`
- `parity_surface_ids`
- `parity_surfaces`
- `parity_audit_mode`
- `parity_harness_commands`
- `parity_trigger_reason`
- `graph_slice_reason`
- `graph_boundary_warnings_top3`
- `graph_adjacent_families_top3`

Work within:
- the current `phase_goal`
- one coherent implementation surface
- one bounded validation surface for that phase
- one independent file family unless the seam explicitly says it is a split/reseed candidate
- one seam slice that is detailed enough to be actionable, but narrow enough that a second family or seam would be a scope failure

Target shape for the slice:
- larger than a tiny "next smallest step"
- smaller than a sprawling open-ended migration
- typically a focused subsystem change, a small vertical slice, or a feature spanning a few files with tests

Rules:
- do not do setup/clear behavior here
- do not run `runctl --setup` or `runctl --prepare-cycle` here; the controller scripts deterministic refresh itself after this prompt
- do not expand into broad preflight unless closeout truly requires it
- do not widen beyond `touch_paths` unless you first prove the slice is blocked or must be split
- use `validation_commands` as the default verification surface for this slice before inventing wider checks
- if the repo already has unrelated dirty files outside `touch_paths`, leave them alone and keep them out of the slice
- do not treat read-only inspection or prompt restatement as completed work
- do not advance just because code changed, one test passed, or the result feels closer
- treat the current active seam as the stable unit of ownership; medium slices are execution chunks, not reasons to advance to a different seam
- keep the active seam open until its acceptance criteria are actually satisfied
- stay on the same active seam across cycles by default while acceptance remains unmet, even when the next slice attacks a narrower sub-problem on that same seam
- if the current seam is actually complete, say so explicitly in the structured runner update
- if you fully completed a seam in this slice, include its canonical seam id in the structured runner update
- if this is a closeout slice, do not infer overall completion from zero open seams alone; the objective is only complete after the explicit done-closeout seam passes final `run_gates`
- if no concrete progress happened, rewrite the blocker precisely before handoff
- if the same `phase / active_seam_id / blocker state` would survive unchanged, rewrite it to the exact failing surface or mark it blocked
- if the active seam still has a known blocker after validation, keep the same seam active and rewrite the blocker more precisely
- if new in-scope acceptance criteria are discovered while working the seam, strengthen the same seam's acceptance or validation contract before handoff instead of leaving the requirement implicit
- only create or switch to a different seam when the remaining work is genuinely independent of the active seam's acceptance target or when one umbrella seam must be split into explicit bounded blockers
- do not move to a different seam while the current seam's acceptance is still unmet
- if the active seam is marked `mini`, keep the slice dependency-contained and fail closed instead of silently turning it into a `high` seam
- if a `mini` slice needs a second subsystem, a broader file family, or a seam decision to finish, stop and carry that forward as a blocker or `high` escalation instead of widening locally
- if the active seam is marked `high`, keep the work bounded to that one seam anyway; stronger reasoning is not permission to sprawl across the backlog
- treat `coupling_notes` as fail-closed warnings about where the slice is likely to spill; if one of those edges activates, stop and report it instead of improvising across the boundary
- if the slice turns out to depend on a partially migrated adjacent family, stop at that seam and report the coupling instead of normalizing both families in one pass
- if the seam is really two independent families, keep the active seam only when one family is clearly primary; otherwise report `scope_status=split` so govern can create bounded child seams
- if the slice would need a second independent family to finish, that second independent family would be a scope failure; hand it to `run_govern` instead of widening locally
- if the slice starts demanding extra unrelated changes to make progress, stop and report the exact spillover instead of accumulating "one more thing"
- if `RUNNER_GRAPH_ACTIVE_SLICE.json` shows a direct boundary crossing into an adjacent family, treat that as early spillover evidence and fail closed before broad repo exploration
- treat `graph_slice_reason` as the compact rationale for why this frontier is open; do not re-broaden the slice unless execution proves that rationale false
- for any seam, passing tests alone is not enough when the stated acceptance still requires behavior, UX, completeness, or polish evidence
- if the active seam acceptance mentions parity, baseline matching, or restoring prior behavior/styling, treat that as fail-closed: do not claim completion on approximate similarity
- if a new discovery would require a second family or seam, stop and let `run_govern` split the backlog instead of widening the current slice
- for parity-style seams, require an explicit comparison against the recorded baseline; if any known delta remains, keep the seam open and name the exact remaining surface/blocker
- use the recorded baseline commit in `parity_baseline_ref` as the first comparison point; inspect `git diff` or `git show` against that baseline before broad repo reads
- do not run broad parity audits every cycle; use `parity_audit_mode` plus the current slice to choose the smallest truthful check
- `diff_only` parity mode is the default for non-UI slices where the touched ownership path does not overlap a declared parity surface
- `targeted` parity mode means:
  1. inspect the baseline diff first
  2. infer which declared parity surfaces could be affected from `touch_paths`, `spillover_paths`, and graph-local adjacency
  3. run MCP only on those affected surfaces
- cap targeted MCP parity reads unless the task explicitly says otherwise:
  - `mini`: at most 1 surface
  - `high`: at most 2 surfaces
- use targeted reads of the few affected files plus MCP to double-check the implementation:
  - read the exact component/view-model files named in `touch_paths`
  - inspect the DOM or component tree only for the affected surfaces
  - for desktop geometry/window/layout behavior, use both `Playwright MCP` and `Tauri MCP`
  - for web-only UI, use `Playwright MCP`
- when using MCP for parity, prefer direct audit reads over broad screenshots:
  - DOM structure
  - computed geometry
  - the component ownership tree
  - the specific interaction flow touched by the slice
- if the diff suggests a changed planner/layout/store seam but not a user-visible surface, keep the audit at diff/read level and skip MCP
- do not use `phase_done=yes` for a parity-style seam unless the current slice actually cleared the recorded parity delta for the audited surface
- never mark a parity/regression-restoration seam complete just because tests pass; tests are necessary but not sufficient when acceptance requires baseline matching
- for subjective polish or UX seams, require direct evidence on the touched surface; if rough edges remain, keep going on the same seam
- for any slice, prefer a tight implementation plus one focused proof path over broad multi-area validation that slows model performance and obscures ownership

## Handoff

Do not refresh runner state from this prompt.

When the bounded work slice is done:
- stop after one coherent work surface
- review your work by asking: `Any problems with the current implementation?`
- name every real remaining issue, regression, rough edge, or acceptance gap explicitly
- if the slice was forced to stop because the declared scope was too narrow, say so explicitly so `run_govern` can split or escalate it cleanly
- set `needs_update=yes` only when semantic seam metadata must change: new blocker family, scope creep, seam split, mini->high escalation, or acceptance/validation rewrite
- default `needs_update=no` when deterministic refresh plus prepared-marker handoff is enough
- add `scope_status` so the runner can tell whether the current seam shape is still valid:
  - `scope_status=ok` when the seam shape still matches the real work surface
  - `scope_status=narrow` when the same seam should stay active but its blocker needs tighter wording
  - `scope_status=split` when the seam bundles multiple file families or seams and should be split
- `scope_status=reseed` when the active open backlog is broad enough that fresh `run_govern` on the stronger model must reseed it in place before the loop should continue
- choose the least disruptive truthful value: prefer `narrow` over `split`, and `split` over `reseed`, unless the seam boundary is genuinely wrong
- do not overuse `scope_status=reseed`; use it only when the seam shape is wrong, not merely because the current work is hard or validation failed once
- if the active seam bundles multiple real file families such as `useAppLayoutStore*` plus `useAppStore*`, do not keep pushing implementation and call that out with `scope_status=split` or `scope_status=reseed`
- if the current seam cannot produce durable progress without widening across a seam or second subsystem, prefer `scope_status=reseed` over another approximate retry so the next fresh `run_govern` can reseed the active backlog
- if implementation starts pulling in files named in `spillover_paths` or implied by `coupling_notes`, stop and report that boundary instead of absorbing the coupled work into the same slice
- report compact operational output
- terminate this Codex chat session immediately

The runner controller will script deterministic refresh itself after this prompt completes. It will invoke `/run_govern` in the same Codex session when your output explicitly says semantic backlog changes are needed or when scripted refresh cannot produce the prepared marker, unless the required update profile is incompatible with the current session.

## Output

Keep output compact and operational.

End with:
- `phase_done=<yes|no>`
- `validation=<pass|fail>`
- `needs_update=<yes|no>`
- `scope_status=<ok|narrow|split|reseed>`
- if `needs_update=yes`, optionally add `update_profile=<mini|high>`; omit it unless the update truly needs `high`
- `exiting=<yes>`
