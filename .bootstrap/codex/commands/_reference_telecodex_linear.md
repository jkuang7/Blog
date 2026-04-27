# Telecodex Linear Control Plane

Use this reference for `/add`, `/run`, and `/review` whenever the work is managed through Linear for Telecodex or any future Codex-driven runner.

## Linear Board Target

Default to the Projects team active board:

- Team key: `PRO`
- Team name: `Projects`
- Board URL: https://linear.app/jkprojects/team/PRO/active

Use this board for `/add`, `/run`, and `/review` unless the user explicitly supplies a different Linear team/project in the current request.

Only Linear issues whose state is exactly `Todo` are executable by `/run`. `Backlog` is a human hold state and must be ignored. If `/run` is invoked with a Linear issue key, for example `/run PRO-123`, that invocation is scoped to that issue only; first verify the scoped issue is exactly `Todo`. Do not select or mutate unrelated tickets.

## Core Rules

- Linear is the durable source of truth. Chat context can help `/add` and `/review`, but `/run` must be able to operate with no chat memory.
- Telecodex SQLite controller context is only a local runner/session hint. Verify it against Linear comments and git state before acting.
- Every feature is ticket-sliced. A feature ticket owns one feature branch.
- Comments carry the execution breakdown: phase markers, progress, leases, review findings, blockers, proof, and next-run setup.
- Acceptance criteria are not complete until proven with the appropriate check, live surface, scenario basket, or regression guard.
- Do not scale an unproven implementation. Prove the idea on the smallest representative slice first, then codify the stable behavior.

## Linear MCP Compatibility

Use the Linear MCP tools directly when available:

- Read ticket context with `mcp__linear__.get_issue`.
- Read phase/progress/review context with `mcp__linear__.list_comments`.
- Create or update tickets with `mcp__linear__.save_issue`.
- Create or update phase/progress/review comments with `mcp__linear__.save_comment`.
- Use `save_comment` with an existing comment `id` to update the machine-readable phase marker.
- Prefer appending progress/review comments over deleting history.

Required mutation pattern:

1. Read issue and comments.
2. Parse current phase markers and progress.
3. Decide the smallest valid update.
4. Update only the machine marker/comment body that represents current state.
5. Append a progress/review comment when useful for human history.
6. Re-read the updated issue/comments if correctness depends on the mutation.

If the Linear MCP cannot create or update the needed issue/comment, stop and report the exact missing capability instead of inventing state locally.

## Ticket Shapes

Single-project request:

- Create one self-contained feature ticket.
- Add ordered phase comments under that ticket.

Multi-project request:

- Create one coordination ticket with the full cross-project context.
- Create one self-contained feature ticket per project or repo.
- Link project tickets to the coordination ticket.
- Record cross-project dependencies explicitly.

Feature ticket required context:

- Original scoped prompt and relevant conversation summary.
- Linear key and URL.
- Final Telegram-facing summaries must include actual Linear URLs for every created or updated issue, formatted as Markdown links such as `[PRO-270](https://linear.app/jkprojects/issue/PRO-270)`.
- Project/repo name and absolute repo path.
- Intended feature branch, for example `feature/PRO-264-short-slug`.
- Goal, non-goals, assumptions, constraints, and risks.
- Scientific proof plan: baseline/control, confounding variables, first isolated slice, realistic workflow to replay, and regression guard to keep.
- Ordered execution plan and definition of done.
- Recovery notes for a future Codex session with no chat memory.

## Phase Comment Contract

Each phase comment starts with one marker:

```md
<!-- telecodex:phase id="phase-01" status="ready" depends="" branch="feature/PRO-264-short-slug" worker="" lease_expires_at="" proof="" commit="" -->
```

Status values:

- `ready`: dependencies are satisfied and `/run` may claim it.
- `claimed`: a worker has reserved it but has not yet changed code.
- `in_progress`: branch/diff or progress log shows active implementation.
- `implemented`: `/run` completed the slice and recorded evidence; `/review` must validate it.
- `needs_followup`: `/review` found concrete fixes for the same phase.
- `review`: waiting for human or Codex review.
- `blocked`: missing decision, dependency, credential, unsafe repo state, or failed proof.
- `done`: `/review` verified acceptance criteria and proof.
- `abandoned`: intentionally superseded.

Phase comment body:

- Goal
- Scope
- Non-goals
- Dependencies
- Acceptance criteria
- Verification/proof plan
- Expected branch
- Likely touched areas
- Progress log
- Blockers/follow-ups
- Next clean `/run` setup

Each phase comment must be sufficient for a later no-memory Codex session to continue that phase or decide it is already closed. Keep controller-level stop-check evidence distinct from phase implementation/review evidence. A final no-ready-work stop check should be recorded as a separate non-phase terminal comment, not appended into the last phase comment.

## Scientific Work Contract

Before implementation:

- State the intended outcome.
- Capture the baseline or control when behavior is hard to predict.
- Identify confounding variables.
- Isolate the first testable slice.
- Define the realistic workflow that proves the outcome.

For sensitive algorithms:

- Build a representative scenario basket before extracting the algorithm.
- Compare candidate behavior across multiple scenarios.
- Promote the algorithm only after the representative set improves or remains stable.

For UI work:

- Inspect the real surface before changing it.
- Verify the resulting surface live with browser/app automation or screenshots.
- Do not claim correctness from code inspection alone when the UI can be exercised.

For no-code smoke-test phases:

- Do not modify tracked repo files.
- Use Linear comment updates, `git status` / diff evidence, and Telecodex footer/controller behavior as the proof surface.
- The phase may be marked done only if the ticket explicitly states that no code changes are expected.

After proof:

- Record raw evidence separately from interpretation.
- Add the smallest useful regression guard if the behavior is likely to recur.
- Keep disposable logs and artifacts out of commits unless they are intentional reusable verification assets.

## `/run` Orientation Contract

`/run` is board-driven. It always orients before work:

- Read the relevant Linear board/project using available Linear MCP context.
- Select the correct feature ticket from Linear state, priority, dependencies, and comments. Only exact `Todo` issues are executable.
- Read the full feature ticket with `get_issue`.
- Read all comments, linked tickets, blockers, review notes, and coordination ticket if present with `list_comments` and related issue reads.
- Read git status, current branch, and relevant diff.
- Refuse to continue if git diff cannot be mapped to the active Linear feature/phase.

## `/review` Handoff Contract

`/review` can use current chat context to understand what just happened, but it must write enough Linear context for the next no-memory `/run`.

Before stopping, `/review` must update Linear with:

- Outcome: `done`, `needs_followup`, `blocked`, or `new_scope_found`.
- Evidence used to prove or reject acceptance criteria.
- Exact follow-up checklist when more work is needed.
- Next ready phase, branch, dependencies, acceptance criteria, and verification steps when progress can continue.

Use `save_comment` to update the phase marker and append the review handoff. Use `save_issue` only for feature-level state, title/body, branch/link metadata, labels, priority, relations, or project/status changes.

`/review` does not switch to `main`, merge, cherry-pick, or invoke `commit-main`. Humans check out and test the feature branch, then manually run `commit-main` when ready.

## Telecodex Footer Contract

When these commands are run by Telecodex, end the final response with:

```text
TELECODEX_STATUS=created|implemented|done|needs_followup|blocked|no_ready_work|failed
TELECODEX_NEXT=review|run|stop
TELECODEX_LINEAR_ISSUE=<issue key or ->
TELECODEX_PHASE=<phase id or ->
TELECODEX_BRANCH=<branch or ->
```

Telecodex uses only this footer to advance the local `/run -> /review -> fresh /run` loop. Do not rely on prose for loop control.

The footer is mandatory. If you cannot complete the requested command cleanly, still emit a footer with `TELECODEX_STATUS=blocked` or `TELECODEX_STATUS=failed` and `TELECODEX_NEXT=stop`. No prose may appear after the footer.
