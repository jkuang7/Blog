---
model: opus
---

# /run - Execute One Telecodex Linear Slice

Use this command to pick work from the Linear board and execute exactly one ready phase slice.

Reference: read `_reference_telecodex_linear.md` before selecting work.

## Purpose

`/run` is board-driven and must assume no chat memory. It decides what to do from Linear plus git state, not from prior conversation.

If Telecodex provides controller context from SQLite, use it only as an orientation hint. Linear comments and git state are still the source of truth.

Hard controller contract:

- You MUST end the final response with the exact `TELECODEX_*` footer.
- Before finalizing, verify the footer is present as the last text in your answer.
- If the phase is implemented or the no-code smoke proof passes, use `TELECODEX_STATUS=implemented` and `TELECODEX_NEXT=review`.
- If the phase cannot be completed yet but the next step is actionable by Codex, update Linear with a concrete follow-up checklist and use `TELECODEX_STATUS=needs_followup` and `TELECODEX_NEXT=run`.
- If the selected ticket has a true blocker requiring human input or an external dependency, update Linear with the blocker and use `TELECODEX_STATUS=blocked` and `TELECODEX_NEXT=stop`.
- Only use `TELECODEX_STATUS=no_ready_work` after inspecting the board and proving there are no pickable nonterminal tickets/phases.
- Never omit the footer because Telecodex uses it as the only loop-control signal.

Default Linear board:

- Team: `PRO` / `Projects`
- Board: https://linear.app/jkprojects/team/PRO/active

Select work from this board unless the user explicitly supplies a different Linear team/project. Only issues whose Linear state is exactly `Todo` are executable by `/run`. Treat `Backlog` as a human hold state and ignore it completely, even if phase comments look ready.

If the command includes a Linear issue key, for example `/run PRO-123`, restrict this invocation to that issue only. First verify that issue is exactly `Todo`; if it is not `Todo`, do not inspect, claim, implement, review, update, block, or advance its phases. Stop with `TELECODEX_STATUS=no_ready_work`, `TELECODEX_NEXT=stop`, and `TELECODEX_LINEAR_ISSUE=<that issue key>`. Do not pick or continue any other ticket, even if other work is ready on the board. In scoped mode, `blocked + stop` stops the controller because the user explicitly selected that one issue.

Unscoped continuous-run invariant:

- Keep advancing PRO active-board tickets until every ticket is either ready for human review, blocked with a real human/external blocker, canceled/duplicate, or no longer actionable.
- A failed proof with a concrete next action is not terminal. Write the follow-up into Linear and return `needs_followup + run`.
- A blocked ticket on an unscoped board run is not proof the board is drained. Record the blocker, then the next fresh `/run` must look for other tickets.
- Only `no_ready_work + stop` may end the unscoped continuous loop normally.

## Mandatory Orientation

Before doing anything:

1. Read the `PRO` active Linear board/project using available Linear MCP context.
2. Identify candidate feature tickets by status, priority, dependencies, and comments. If a scoped issue key was provided, the candidate set is exactly that issue.
3. Read the chosen feature ticket in full with `mcp__linear__.get_issue`.
4. Read all comments, phase markers, progress logs, blockers, and review notes with `mcp__linear__.list_comments`.
5. Read linked tickets and coordination ticket if present with `get_issue` and `list_comments`.
6. Read git status, current branch, and relevant git diff.
7. Map branch/diff to the active Linear feature and phase.

Stop if:

- The git diff cannot be mapped to the selected Linear ticket/phase.
- Another worker owns an unexpired lease.
- Dependencies are not done.
- The worktree is unsafe or contains unrelated dirty changes.
- Acceptance criteria are ambiguous enough that implementation would be guesswork.

## Selection Rules

If current branch/diff maps to an active phase:

- Continue that phase if the lease/session permits it.
- Prefer `in_progress` or `needs_followup` over starting new work.

If there is no active mapped phase:

- Select the highest-priority feature ticket whose Linear state is exactly `Todo`.
- Claim the first phase with `status="ready"` whose dependencies are `done`.
- If a phase is `needs_followup`, continue it before new ready phases.
- Ignore `Backlog`, `In Progress`, `In Review`, `Done`, and any other non-`Todo` issue state for new execution.
- Do not consider the executable board drained while any `Todo` ticket has a ready phase, follow-up checklist, or unblocked acceptance criteria that Codex can act on.

Claim by updating the phase marker with:

- `status="claimed"` then `status="in_progress"` once implementation starts.
- worker/session ID.
- branch.
- lease expiration.

Use `mcp__linear__.save_comment` with the existing phase comment `id` to update the phase marker. Append a separate progress comment only when it adds useful history.

## Branch Rules

- Use the feature branch recorded in Linear.
- Create it only if the worktree is safe.
- Do not switch to `main` unless needed to create the feature branch safely and there is no dirty work.
- Do not merge, cherry-pick, or invoke `commit-main`.

## Scientific Execution

Before implementation:

- Establish the smallest proof path.
- Capture baseline/control when behavior is hard to predict.
- For sensitive algorithms, test a representative scenario basket before choosing the algorithm.
- For UI work, inspect the real surface before changing code.

For an explicitly marked no-code smoke-test phase:

- Do not modify tracked repo files.
- Prove the phase with Linear updates, git status, runner footer behavior, and any requested controller-state observations.
- Add or update a Linear progress comment during `/run` so the comment stream reconstructs that execution happened.
- It is valid for the implementation proof to be `git status` showing no tracked changes, as long as the phase acceptance criteria explicitly require no code changes.

Implementation:

- Execute exactly one coherent phase slice.
- Stay inside the phase scope.
- If new scope appears, record it in Linear and stop instead of absorbing it silently.

Verification:

- Run the phase’s proof plan.
- Prefer live/runtime verification when the behavior can be exercised.
- Do not claim acceptance criteria from code inspection alone when a higher-signal proof is available.

## Linear Update Before Stopping

Always update the phase comment with:

- Changed files.
- Checks run and results.
- Proof evidence.
- Remaining gaps.
- Blockers, if any.
- Next expected action.

Use `mcp__linear__.save_comment` to update the phase marker and progress body. Use `mcp__linear__.save_issue` only for feature-level state changes.

Set the phase to one of:

- `implemented`: work is ready for `/review`.
- `needs_followup`: implementation needs more work in the same phase.
- `blocked`: execution cannot continue safely.

Only `/review` can mark `done`.

Do not leave an issue in `Todo` after attempting it. If you start work, move the issue/phase to an in-progress state or write a blocker/follow-up state before returning. A failed proof is not completion; it must become `needs_followup` when Codex can continue, or `blocked` when it cannot.

## Final Stop Check

When a scoped `/run PRO-123` finds all phases already `done` and no ready or follow-up work remains:

- Re-read the issue and all phase comments before concluding there is no work.
- Record a concise stop-check evidence block in Linear as a separate non-phase terminal comment. Do not append final no-ready-work/controller termination evidence into the last phase comment.
- Preserve existing phase comments and do not change `done` phase markers back to another state.
- Move the issue to `In Review` when all acceptance evidence is complete and the ticket is ready for human review.
- Return `TELECODEX_STATUS=no_ready_work`, `TELECODEX_NEXT=stop`, and keep `TELECODEX_LINEAR_ISSUE` set to the scoped issue key.

## Final Response

Return:

- Selected Linear ticket and phase.
- Branch.
- What was implemented.
- Verification run and evidence.
- Linear status written.
- Next expected command.

End every final response with this exact machine-readable footer. Use `-` for unknown or not applicable values.
The footer must be the final block in the response, with no prose after it.

For implemented work ready for review:

```text
TELECODEX_STATUS=implemented
TELECODEX_NEXT=review
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```

When no Linear work can be picked up:

```text
TELECODEX_STATUS=no_ready_work
TELECODEX_NEXT=stop
TELECODEX_LINEAR_ISSUE=<scoped issue key or ->
TELECODEX_PHASE=-
TELECODEX_BRANCH=-
```

For follow-up work that a fresh `/run` should continue:

```text
TELECODEX_STATUS=needs_followup
TELECODEX_NEXT=run
TELECODEX_LINEAR_ISSUE=<issue key>
TELECODEX_PHASE=<phase id>
TELECODEX_BRANCH=<feature branch>
```

For blocked or unsafe states that need human action, use `TELECODEX_STATUS=blocked` and `TELECODEX_NEXT=stop`.
