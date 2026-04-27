---
model: opus
---

# /review - Review Telecodex Linear Work

Use this command to review the current feature branch against Linear, update the ticket/comment context, and prepare the next clean `/run`.

Reference: read `_reference_telecodex_linear.md` before reviewing.

## Purpose

`/review` verifies whether the active phase satisfies its acceptance criteria. It can use current chat context to understand what happened, but it must write enough Linear context for a future no-memory `/run`.

`/review` does not merge, cherry-pick, switch to `main`, or invoke `commit-main`. The human will check out the feature branch, test it, and manually run `commit-main` later.

If Telecodex provides controller context from SQLite, use it only as an orientation hint. Linear comments and git state are still the source of truth.

Hard controller contract:

- You MUST end the final response with the exact `TELECODEX_*` footer.
- Before finalizing, verify the footer is present as the last text in your answer.
- If review validates the phase, use `TELECODEX_STATUS=done` and `TELECODEX_NEXT=run`.
- If more work is required, update Linear with the checklist and use `TELECODEX_STATUS=needs_followup` and `TELECODEX_NEXT=run`.
- If no safe follow-up can run, use `TELECODEX_STATUS=blocked` and `TELECODEX_NEXT=stop`.
- Only use `blocked + stop` when a human decision, dependency, credential, or unsafe repo state prevents any safe Codex continuation. A failed acceptance proof with a concrete next step is `needs_followup + run`, not stop.
- Never omit the footer because Telecodex uses it as the only loop-control signal.

Default Linear board:

- Team: `PRO` / `Projects`
- Board: https://linear.app/jkprojects/team/PRO/active

Review against tickets/phases on this board unless the user explicitly supplies a different Linear team/project.

If Telecodex provides `runner_scope_issue`, or the user invokes review after `/run PRO-123`, keep the review scoped to that issue. Do not update or advance unrelated Linear tickets.

Continuous-run invariant:

- `/review` is a transition gate, not the end of the loop.
- If the phase is done or needs follow-up, return `TELECODEX_NEXT=run` so Telecodex closes this Codex session and starts a fresh `/run`.
- The fresh `/run` must re-apply the exact `Todo` gate before reading or claiming work. `Backlog` is a human hold state and must remain untouched.
- Do not return `blocked + stop` for a normal failed proof when a concrete follow-up can be written for `/run`.

## Orient First

Before judging anything:

1. Read current branch, git status, and git diff.
2. Read the active Linear feature ticket in full with `mcp__linear__.get_issue`.
3. Read all phase comments, progress logs, blocker comments, and review comments with `mcp__linear__.list_comments`.
4. Read linked/dependent tickets and coordination ticket on the `PRO` active board if present with `get_issue` and `list_comments`.
5. Use current chat context as additional context when available.
6. Map branch/diff to the active phase marker.

Stop if the branch/diff cannot be mapped to a Linear ticket and phase.

## Review Criteria

Compare the implementation against:

- Phase goal and scope.
- Acceptance criteria.
- Verification/proof plan.
- Follow-up requirements from prior review comments.
- Feature-level definition of done.
- Scientific proof requirements from the ticket.

Acceptance criteria are not complete unless proven.

For sensitive algorithms:

- Confirm evidence covers multiple representative scenarios.
- Refuse `done` if only a single happy-path case was checked and the ticket called for broader proof.

For UI work:

- Verify the real surface when feasible.
- Refuse `done` if the claim is based only on static code inspection and the UI can be exercised.

For an explicitly marked no-code smoke-test phase:

- Verify that the phase did not require tracked repo edits.
- Accept `git status` / git diff evidence showing no tracked code changes when that is the stated acceptance criterion.
- Still update the Linear phase marker and review handoff context before returning `TELECODEX_NEXT=run`.

## Outcomes

Choose exactly one outcome.

`done`:

- Acceptance criteria are met.
- Proof evidence is recorded.
- No unresolved follow-up remains for this phase.
- The issue should be moved to the appropriate review-ready state when the feature/phase is ready for human testing, or the next ready phase must be explicit in Linear.

`needs_followup`:

- The phase is close but needs concrete fixes.
- Write a precise checklist into the phase comment.
- Keep the same feature branch and phase context ready for `/run`.
- Keep or move the issue/phase into an active/in-progress state so the next `/run` can pick it up deterministically.

`blocked`:

- A missing decision, dependency, credential, unsafe repo state, or failed proof prevents progress.
- Write the blocker, required decision, and what `/run` must not attempt.

`new_scope_found`:

- Required work is outside the current phase.
- Create a new phase comment or child ticket with dependencies and full context.
- Do not silently expand the current phase.

## Next Clean `/run` Setup

Before stopping, update Linear so `/run` can resume with no chat memory:

- Current outcome.
- Evidence used for review.
- Changed files reviewed.
- Exact follow-up checklist or blocker.
- Next ready phase, if one exists.
- Next phase branch, dependencies, acceptance criteria, and verification steps.
- Coordination ticket status when cross-project work is affected.
- For no-code smoke tickets, keep phase evidence distinct from final controller stop-check evidence. A phase comment should prove that phase; a later fresh `/run` that finds no remaining work should record a separate non-phase terminal stop-check comment.

Use `mcp__linear__.save_comment` to update the phase marker and append review handoff context. Use `mcp__linear__.save_issue` only for feature-level state or cross-ticket relation updates.

Do not leave a reviewed ticket in `Todo` if work has begun or a proof failed. It must be in a state that reflects reality: ready for human review, needs follow-up, blocked, canceled, or done.

If the feature is ready for human testing:

- Leave the feature branch checked out.
- Record the branch name and current commit state in Linear.
- Tell the human the branch is ready for manual checkout/testing and later `commit-main`.

## Commit Policy

- Do not commit automatically during `/review`.
- Do not switch to `main`.
- Do not merge or cherry-pick.
- Do not run `commit-main`.
- If the user explicitly asks for a commit during review, make a normal feature-branch commit only after staging the intended ticket changes and recording the commit SHA in Linear.

## Final Response

Return:

- Review outcome.
- Linear ticket/phase updated.
- Evidence accepted or missing.
- Next clean `/run` setup.
- Whether the feature branch is ready for human testing.

End every final response with this exact machine-readable footer. Use `-` for unknown or not applicable values.
The footer must be the final block in the response, with no prose after it.

When review updated Linear and the runner should ask a fresh `/run` to find the next pickable ticket:

```text
TELECODEX_STATUS=done
TELECODEX_NEXT=run
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```

Use `TELECODEX_STATUS=needs_followup` with `TELECODEX_NEXT=run` when `/run` should continue the same phase from Linear follow-up context.

Use `TELECODEX_STATUS=blocked` with `TELECODEX_NEXT=stop` when no safe follow-up can be picked without human action.
