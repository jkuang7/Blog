---
model: opus
---

# /review - Review Telecodex Linear Work

Use this command to validate one implemented phase, prepare the next clean `/run`, and perform final PR handoff after audit. Read `_reference_telecodex_linear.md` first; it owns the shared board, phase, proof, footer, and PR-handoff contracts.

Telecodex automation runs `/review` with high reasoning effort.

## Purpose

`/review` is the transition gate after `/run`. It verifies the phase identified by the last Telecodex footer, updates Linear with enough context for a future no-memory `/run`, and decides whether to continue, block, create audit work, or create the final draft PR.

`/review` must not discover unrelated work, claim new implementation work, merge, cherry-pick, switch to `main`, or invoke `commit-main`.

## Orient

Before judging:

1. Read current branch, git status, and git diff.
2. Read the scoped Linear issue and all comments.
3. Read linked/coordination tickets when they affect the phase.
4. Reconstruct the target phase from the last footer and Linear markers.
5. Map branch/diff to the scoped issue and phase.

Stop if the branch/diff cannot be mapped to the scoped issue and phase.

## Review Decision

Compare implementation and proof against:

- phase goal, scope, and acceptance criteria
- follow-up checklist from prior review, if any
- feature-level definition of done
- Context and Reuse Card: run-flow inspection, reuse scan, guard decision, and cleanup
- proof plan and `agent-harness.md`
- recorded proof class and raw evidence

Outcomes:

- `done`: acceptance criteria are met, proof is acceptable, and no follow-up remains.
- `needs_followup`: Codex can continue with a precise checklist.
- `blocked`: human input, external dependency, credential, unsafe repo state, or unresolvable proof gap prevents safe continuation.
- `new_scope_found`: work is outside the current phase and needs a new phase or child ticket.

A failed proof with a concrete next action is `needs_followup + run`, not `blocked + stop`.

## Runner Handoff

Intermediate phase reviews are for the next no-memory `/run`, not human PR review.

Update the existing phase comment with:

- outcome
- evidence accepted/rejected
- proof class accepted/rejected
- Context and Reuse Card accepted/rejected
- raw observations vs interpretation
- changed files reviewed
- exact follow-up checklist or blocker
- next phase/branch/dependencies/verification when progress can continue

Do not add a broad human PR summary before final handoff. Do not create a separate summary-only comment.

If the phase is `done` or has actionable follow-up, return `TELECODEX_NEXT=run` so Telecodex starts a fresh `/run` and re-applies the `Todo` gate.

## Audit Gate

When a normal phase is done and all other non-abandoned normal phases on the feature ticket are done:

- Create or ready `telecodex:phase id="audit"` if it is not already done.
- Write audit instructions into the audit phase comment.
- Return `done + run` so `/run` performs one final audit pass before PR handoff.

The audit phase checks full branch diff against Linear goals, acceptance criteria, proof plan, completed phase evidence, cleanup/refactor gaps, fragile tests, stale docs/comments, scope drift, and unproven claims.

For no-code or no-diff tickets, `/review` may mark audit done immediately only with explicit evidence that a runner audit pass would add no signal.

## Final Draft PR Handoff

Run only when reviewing `TELECODEX_PHASE=audit` and accepting the audit phase as `done`.

Steps:

1. Verify every non-abandoned normal phase and the audit phase are `done`.
2. Verify branch/diff maps only to this Linear ticket.
3. If needed, commit only mapped changes on the feature branch.
4. Push the feature branch.
5. Reuse an existing PR for the branch or create a GitHub draft PR.
6. Update the feature ticket `## Review Links` with PR URL, last reviewed commit, branch, and fallback command.
7. Write a final Linear handoff comment and PR body with the same organized human summary.

Final handoff sections:

```md
## PR Review

## Change Summary

## Feature / Area Breakdown

## Tests and Verification

## Review Notes

## Remaining Risks or Follow-ups
```

Group `Feature / Area Breakdown` by distinct features, subsystems, or user-facing surfaces. Keep tests and verification separate. If there are no remaining risks, state `None known from this review`.

If PR creation is blocked, do not invent a link. Write a Linear fallback note with reason, branch, commit state, and `git checkout <branch> && git diff main...HEAD`.

## Commit Policy

- No commits during intermediate phase review unless the user explicitly asks.
- Final handoff may create a normal feature-branch commit only after audit is done and only for changes mapped to the selected Linear ticket.
- `/review` never switches to `main`, merges, cherry-picks, or invokes `commit-main`.

## Final Response

Return review outcome, Linear issue/phase updated, evidence accepted/missing, next clean `/run` setup, and whether the branch or draft PR is ready for human review.

End with one mandatory footer as the final text:

```text
TELECODEX_STATUS=done|needs_followup|blocked
TELECODEX_NEXT=run|stop
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```
