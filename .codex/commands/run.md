---
model: opus
---

# /run - Execute One Telecodex Linear Slice

Use this command to pick one executable Linear phase and implement exactly one coherent slice. Read `_reference_telecodex_linear.md` first; it owns the shared board, phase, proof, footer, and PR-handoff contracts.

Telecodex automation runs `/run` with medium reasoning effort.

## Purpose

`/run` is board-driven and assumes no chat memory. It selects work from Linear plus git state, implements one phase, records evidence, and hands control to `/review`.

`/run` must not mark phases `done`, create/update PRs, write human PR summaries, merge, cherry-pick, switch to `main` for completion, or invoke `commit-main`.

## Todo Gate

- Only Linear issues whose state is exactly `Todo` are executable.
- `Backlog` is a human hold state and must be ignored completely.
- If invoked as `/run PRO-123`, scope this turn to `PRO-123` only.
- If the scoped issue is not exactly `Todo`, do not inspect or mutate its phases; stop with `TELECODEX_STATUS=no_ready_work`.
- In unscoped mode, a blocked ticket does not drain the board. Record the blocker and let the next fresh `/run` look for other `Todo` work.

## Orient

Before implementation:

1. Read the relevant Linear board or scoped issue.
2. Read the chosen feature ticket and all comments.
3. Read linked/coordination tickets when they affect dependencies.
4. Read git branch, status, and relevant diff.
5. Map branch/diff to the active Linear feature and phase.

Stop before changing code if:

- git diff cannot be mapped to the selected ticket/phase
- another worker owns an unexpired lease
- dependencies are not done
- worktree state is unsafe or contains unrelated dirty changes
- acceptance criteria are too ambiguous to implement safely

## Selection

If current branch/diff maps to an active phase:

- Continue it when the lease/session permits.
- Prefer `in_progress` or `needs_followup` over starting new work.

Otherwise:

- Select the highest-priority `Todo` feature ticket with actionable phase work.
- Claim the first `ready` phase whose dependencies are done.
- Continue `needs_followup` before new ready phases.

Claim by updating the existing phase comment marker to `claimed`, then `in_progress` once implementation starts. Include worker/session, branch, and lease expiration.

## Execution

- Use the feature branch recorded in Linear; create it only when the worktree is safe.
- Complete the Context and Reuse Card before editing: inspect existing run-flow, reusable utilities/patterns, proof plan, guard decision, and cleanup target.
- Execute exactly one coherent phase slice inside scope.
- If new scope appears, record it in Linear and stop instead of absorbing it.
- Follow the phase proof plan and `agent-harness.md`.
- Prefer live/runtime proof when behavior can be exercised.
- Keep raw observations separate from interpretation.
- Treat flaky checks as harness defects or follow-up work.
- Add tests/harnesses only when they are stable, deterministic, and useful regression guards.

No-code smoke phases must not modify tracked repo files. Prove them with Linear updates, git clean-state evidence, controller/footer behavior, and requested controller-state observations.

## Audit Phase

When claiming `phase="audit"`:

- Review the full branch diff against Linear goals, non-goals, acceptance criteria, proof plan, and completed phase evidence.
- Look for refactor/cleanup gaps, missing edge cases, fragile tests, stale docs/comments, scope drift, and unproven claims.
- Make only small same-ticket polish edits supported by audit findings.
- Route larger refactors, new behavior, or ambiguous scope to `needs_followup`.
- Record audit findings, polish changes, verification, proof class, remaining risk, and final readiness.
- Return `implemented + review` for `/review` to validate the audit.

## Linear Update

Before stopping, update the active phase comment with:

- status: `implemented`, `needs_followup`, or `blocked`
- changed files
- commit readiness
- Context and Reuse Card outcome
- checks and results
- proof class
- raw observations and interpretation
- cleanup performed or intentionally deferred
- remaining gaps, blockers, or next action

Only `/review` can mark a phase `done`.

For final no-ready-work stop checks, create a separate non-phase terminal comment. Do not append controller termination evidence to the last phase comment.

## Final Response

Return selected ticket/phase, branch, implementation summary, verification evidence, Linear status written, and next expected command.

End with one mandatory footer as the final text.

Implemented work:

```text
TELECODEX_STATUS=implemented
TELECODEX_NEXT=review
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```

No pickable work:

```text
TELECODEX_STATUS=no_ready_work
TELECODEX_NEXT=stop
TELECODEX_LINEAR_ISSUE=<scoped issue key or ->
TELECODEX_PHASE=-
TELECODEX_BRANCH=-
```

Follow-up or blocked work:

```text
TELECODEX_STATUS=needs_followup|blocked
TELECODEX_NEXT=run|stop
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```
