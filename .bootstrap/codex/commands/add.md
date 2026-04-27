---
model: opus
---

# /add - Create Telecodex Linear Work

Use this command when the user asks to turn context into Linear-controlled work for Telecodex or any reusable Codex-driven execution system.

Reference: read `_reference_telecodex_linear.md` before creating or updating Linear work.

## Purpose

Convert `/add {context}` into durable Linear tickets that future Codex sessions can execute without relying on chat memory.

`/add` plans and materializes work. It does not implement code.

Hard controller contract:

- You MUST create at least one `telecodex:phase` marker comment for every feature ticket.
- You MUST end the final response with the exact `TELECODEX_*` footer.
- Before finalizing, verify the footer is present as the last text in your answer.
- If you cannot satisfy either requirement, return `TELECODEX_STATUS=failed` and `TELECODEX_NEXT=stop`.

Create and update work on the Linear Projects team active board by default:

- Team: `PRO` / `Projects`
- Board: https://linear.app/jkprojects/team/PRO/active

When using `mcp__linear__.save_issue`, set `team` to `PRO` or `Projects` unless the user explicitly requests a different Linear team.

## Orient First

Before creating tickets:

1. Read the slash-command argument and current chat context.
2. Inspect the current workspace/repo path, git branch, and git status.
3. Inspect relevant Linear tickets with `mcp__linear__.get_issue`.
4. Inspect comments with `mcp__linear__.list_comments`.
5. Inspect linked issues and the `PRO` active board/project state with available Linear MCP context.
6. Identify whether the request is single-project or multi-project.
7. Identify any existing active feature ticket that should be updated instead of duplicated.

If the project/repo cannot be inferred safely, ask one concise question and stop.

## Project Splitting

If the context spans multiple projects or repos:

- Create one coordination ticket with the full cross-project context using `mcp__linear__.save_issue`.
- Create one self-contained feature ticket per project/repo using `mcp__linear__.save_issue`.
- Link each feature ticket to the coordination ticket with `relatedTo`, `blocks`, or `blockedBy` through `save_issue`.
- Record cross-project dependencies explicitly.
- Do not hide unrelated projects inside one overloaded ticket.

If the context is one project:

- Create one self-contained feature ticket with `mcp__linear__.save_issue`.
- Put the ordered phase breakdown in comments.

## Feature Ticket Requirements

Every feature ticket must contain all context needed to solve the problem later:

- Original scoped prompt and relevant chat summary.
- Target project/repo name and absolute repo path.
- Linear key and URL once known.
- Intended feature branch, for example `feature/PRO-264-short-slug`.
- Goal, non-goals, assumptions, constraints, risks, and dependencies.
- Definition of done.
- Scientific proof plan.
- Ordered execution plan.
- Recovery notes for a future no-memory `/run`.

## Scientific Proof Plan

For each feature, include:

- Intended outcome.
- How acceptance criteria will be proven.
- Confounding variables.
- First isolated testable slice.
- Realistic workflow to replay.
- Failure observation/localization plan.
- Durable regression guard to add once behavior is proven.

For sensitive algorithms, require a representative scenario basket before locking in the algorithm.

For UI work, require live verification on the real surface before claiming correctness.

For a no-code smoke-test request, create a ticket whose scope explicitly forbids tracked repo edits and whose acceptance criteria can be proven through Linear updates, git clean-state evidence, and Telecodex footer/controller behavior.

## Phase Comments

Slice each feature into ordered phase comments using `mcp__linear__.save_comment`. Each comment must start with:

```md
<!-- telecodex:phase id="phase-01" status="ready" depends="" branch="feature/PRO-264-short-slug" worker="" lease_expires_at="" proof="" commit="" -->
```

Each phase comment includes:

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

Keep phases small enough for one `/run` to execute one coherent slice.

Do not replace this machine-readable marker with prose such as `phase: intake`. The marker is required so `/run` and `/review` can reconstruct phase state with no chat memory.

## Linear Status

After materializing work:

- Set the first executable phase to `ready`.
- Leave blocked phases as `blocked` or ready phases with explicit dependencies.
- Update the feature ticket status to Ready/Planning according to the board’s available statuses using `mcp__linear__.save_issue`.
- On a coordination ticket, list every project ticket and cross-project dependency.

## Final Response

Return:

- Coordination ticket, if created.
- Feature tickets created or updated.
- Actual Linear URLs for every created or updated issue, formatted as Markdown links such as `[PRO-270](https://linear.app/jkprojects/issue/PRO-270)`.
- Phase count per feature.
- First ready phase.
- Feature branch names.
- Any unresolved decisions or blockers.

End every final response with this exact machine-readable footer. Use `-` for unknown or not applicable values.
The footer must be the final block in the response, with no prose after it.

```text
TELECODEX_STATUS=created
TELECODEX_NEXT=stop
TELECODEX_LINEAR_ISSUE=<primary issue key or ->
TELECODEX_PHASE=<first ready phase id or ->
TELECODEX_BRANCH=<first feature branch or ->
```
