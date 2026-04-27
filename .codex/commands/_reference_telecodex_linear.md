# Telecodex Linear Control Plane

Shared contract for `/add`, `/run`, and `/review`. Command files should stay concise and refer here for shared board, phase, proof, footer, and PR-handoff rules.

Use `~/.codex/docs/agent-harness.md` for proof classes, anti-flake rules, Context and Reuse Card requirements, and durable guard decisions.

## Board and Source of Truth

- Default team: `PRO` / `Projects`
- Default board: https://linear.app/jkprojects/team/PRO/active
- Linear is the durable source of truth. Chat context can help `/add` and `/review`; `/run` must work with no chat memory.
- Telecodex SQLite/controller state is only an orientation hint. Verify against Linear comments and git state.
- Only Linear issues in exactly `Todo` are executable by `/run`.
- `Backlog` is human hold and must be ignored by `/run`.
- Scoped `/run PRO-123` may inspect/mutate only `PRO-123`, and only if it is exactly `Todo`.

## Command Boundaries

- `/add`: creates/updates Linear tickets and phase comments. No implementation, claims, PRs, or runner-state lookup.
- `/run`: selects one `Todo` ticket/phase, implements one slice, records proof, and returns to `/review`. No phase `done`, PRs, or human summaries.
- `/review`: validates the last footer phase, writes next-run context, creates/updates audit, and performs final PR handoff after audit. No unrelated work discovery or `commit-main`.

## Linear MCP Pattern

Use the Linear MCP tools directly when available:

- `get_issue` for ticket context.
- `list_comments` for phase/progress/review context.
- `save_issue` for ticket body/status/relation/metadata updates.
- `save_comment` for phase/progress/review comments.

Mutation pattern:

1. Read issue and comments.
2. Parse phase markers and progress.
3. Decide the smallest valid update.
4. Update the owning issue/comment.
5. Append a progress/review comment only when useful.
6. Re-read when correctness depends on the mutation.

If MCP cannot perform the needed update, stop and report the missing capability. Do not invent local-only state.

## Ticket Shape

Every feature ticket owns one feature branch and must include:

- original scoped prompt and relevant chat summary
- Linear key/URL, target repo/project, and absolute repo path
- intended feature branch
- `## Review Links`
- goal, non-goals, assumptions, constraints, risks, dependencies
- definition of done
- scientific proof plan and expected proof classes
- Context and Reuse Card: run-flow, reuse scan, reuse decision, proof plan, guard decision, and cleanup
- ordered execution plan
- recovery notes for a no-memory `/run`

Required review-link placeholder:

```md
## Review Links

- Feature branch: `feature/PRO-264-short-slug`
- Draft PR: pending final `/review`
- Last reviewed commit: pending
- Local fallback: `git checkout feature/PRO-264-short-slug && git diff main...HEAD`
```

Multi-project work uses one coordination ticket plus one self-contained feature ticket per repo/project/workstream. Link dependencies explicitly; do not hide unrelated projects in one overloaded ticket.

## Phase Contract

Every phase comment starts with one marker:

```md
<!-- telecodex:phase id="phase-01" status="ready" depends="" branch="feature/PRO-264-short-slug" worker="" lease_expires_at="" proof="" commit="" -->
```

Statuses:

- `ready`: `/run` may claim when dependencies are done.
- `claimed`: worker reserved the phase.
- `in_progress`: implementation has started.
- `implemented`: `/run` completed work and recorded evidence.
- `needs_followup`: concrete Codex-actionable fixes remain.
- `blocked`: human/external/unsafe-state blocker.
- `done`: `/review` accepted the phase.
- `abandoned`: intentionally superseded.

Phase bodies should carry goal, scope, non-goals, dependencies, acceptance criteria, Context and Reuse Card, proof plan, expected branch, likely touched areas, progress, blockers/follow-ups, and next clean `/run` setup.

Keep controller stop-check evidence separate from phase evidence. Final no-ready-work stop checks belong in a separate non-phase terminal comment.

## Proof Policy

- Acceptance criteria are not complete until proven.
- Before implementation, inspect the existing run-flow and reusable utilities/patterns relevant to the phase.
- Proof classes: `live`, `harness`, `unit`, `static`, `blocked`.
- Prefer the highest-signal proof available; use static proof only when stronger proof is unnecessary or impractical.
- Keep raw observations separate from interpretation.
- Treat flaky checks as harness defects or follow-up work, not acceptance evidence.
- Add regression guards only when the behavior is stable, likely to recur, deterministic enough to trust, and cheaper than replaying live proof.
- For UI work, inspect and verify the real surface when feasible.
- For sensitive algorithms, use a representative scenario basket before locking behavior.
- For no-code smoke phases, no tracked repo edits are allowed; prove with Linear updates, git clean-state evidence, and Telecodex footer/controller behavior.

## Run and Review Loop

- `/run` returns `implemented + review` after implementing one phase.
- `/review` returns `done + run` after accepting a phase, so Telecodex starts a fresh `/run`.
- `needs_followup + run` means Codex can continue from Linear context.
- `blocked + stop` is only for human/external/unsafe blockers.
- `no_ready_work + stop` is only for a proven drained allowed scope.

Before stopping, `/run` records changed files, commit readiness, Context and Reuse Card outcome, checks, proof class, evidence, gaps, blockers, cleanup, and next action.

Before stopping, `/review` records outcome, accepted/rejected Context and Reuse Card, accepted/rejected evidence, proof judgment, changed files reviewed, exact follow-up/blocker, and next-run setup.

Intermediate `/review` comments are runner handoffs. They must not include broad human PR summaries or separate summary-only comments.

## Audit and PR Handoff

- When `/review` closes the last normal phase, it creates/readies `telecodex:phase id="audit"` and returns `done + run`.
- `/run` executes the audit phase by checking full branch diff against Linear goals, proof, completed evidence, cleanup/refactor gaps, fragile tests, stale docs/comments, scope drift, and unproven claims.
- `/review` may create/reuse a draft PR only when reviewing `TELECODEX_PHASE=audit` and accepting audit as `done`.
- `/run` never creates or updates a PR.
- `/review` never switches to `main`, merges, cherry-picks, or invokes `commit-main`.

Final PR handoff must update the ticket `## Review Links`, write the clickable PR URL and last reviewed commit to Linear, and mirror this structure in the Linear handoff comment and PR body:

```md
## PR Review

## Change Summary

## Feature / Area Breakdown

## Tests and Verification

## Review Notes

## Remaining Risks or Follow-ups
```

Group feature breakdown by feature/subsystem/user-facing surface. Keep tests and verification separate. If no risk remains, say `None known from this review`.

If PR creation is blocked, write a Linear fallback note with reason, branch, commit state, and `git checkout <branch> && git diff main...HEAD`.

## Footer Contract

When run by Telecodex, final assistant text must end with:

```text
TELECODEX_STATUS=created|implemented|done|needs_followup|blocked|no_ready_work|failed
TELECODEX_NEXT=review|run|stop
TELECODEX_LINEAR_ISSUE=<issue key or ->
TELECODEX_PHASE=<phase id or ->
TELECODEX_BRANCH=<branch or ->
```

Telecodex uses only this footer for loop control. Do not put prose after it. If the command cannot complete cleanly, still emit `blocked` or `failed` with `TELECODEX_NEXT=stop`.
