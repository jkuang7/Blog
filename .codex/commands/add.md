---
model: opus
---

# /add - Create Telecodex Linear Work

Use this command to turn user context into Linear-controlled work for Telecodex. Read `_reference_telecodex_linear.md` first; it owns the shared board, phase, proof, footer, and PR-handoff contracts.

Telecodex automation runs `/add` with high reasoning effort.

## Purpose

`/add` plans and materializes Linear work. It does not implement code, claim runner phases, inspect local tmux runner state, create PRs, or run `commit-main`.

Success means future no-memory `/run` sessions can execute from Linear alone.

## Orient

Before writing Linear:

1. Read the slash-command argument and relevant chat context.
2. Inspect workspace path, git branch, and git status.
3. Read relevant Linear issues/comments and linked coordination work.
4. Decide whether to update existing active work or create new work.
5. Classify the request as single-scope or multi-scope.

Ask one concise question and stop only when the target repo/project cannot be inferred safely.

## Scope Construction

Single-scope request:

- Create or update one self-contained feature ticket.
- Add ordered `telecodex:phase` comments under that ticket.

Multi-scope request:

- Build a scope map before writing Linear:
  - coordination scope, when shared sequencing/context matters
  - one isolated feature-ticket scope per repo/project/workstream
  - explicit dependency edges between feature scopes
- Prefer multi-agent drafting when subagents are available and runtime policy permits it:
  - parent `/add` owns decomposition, validation, and all Linear writes
  - each subagent gets only one scoped prompt excerpt, target repo/project, direct dependencies, and the ticket template
  - subagents return draft ticket/phase plans only
  - subagents must not create/update Linear, PRs, branches, or repo files
- If subagents are unavailable, draft each feature ticket serially from an isolated scope packet.
- Run a contamination guard before saving:
  - no child ticket contains unrelated repo/project implementation details
  - shared context stays in the coordination ticket unless directly required by a child
  - each feature ticket has its own goal, non-goals, proof plan, branch, phase comments, and review-link placeholder
  - dependencies are explicit instead of blended into overloaded scope

## Ticket Materialization

For every feature ticket:

- Include the required feature-ticket context from `_reference_telecodex_linear.md`.
- Include a `## Review Links` placeholder from the start.
- Slice work into small ordered phase comments with canonical `telecodex:phase` markers.
- Make the first executable phase `ready`; block dependent phases explicitly.
- Put new executable feature tickets in `Todo` by default.
- Leave human-held work in `Backlog` only when the user explicitly asks.

For coordination tickets:

- Include the cross-project context and list every child feature ticket.
- Link child tickets with the appropriate Linear relation.
- Record cross-project dependencies and sequencing.

## Proof Planning

Use `~/.codex/docs/agent-harness.md` and the shared reference to write proof plans.

- Include a Context and Reuse Card in each feature ticket and phase plan.
- Require future runners to inspect existing run-flow and reusable utilities/patterns before implementation.
- Ask for the smallest high-signal proof first.
- Do not ask for tests by default.
- Add durable tests/harnesses only when the behavior is stable, likely to regress, deterministic enough to trust, and cheaper than replaying live proof.
- For no-code smoke tickets, explicitly forbid tracked repo edits and use Linear updates, git clean-state evidence, and Telecodex footer behavior as the proof surface.

## Final Response

Return:

- Coordination ticket, if created.
- Feature tickets created/updated.
- Actual Linear URLs for every issue, formatted as Markdown links.
- Phase count, first ready phase, feature branch names, and blockers.

End with the mandatory Telecodex footer as the final text:

```text
TELECODEX_STATUS=created
TELECODEX_NEXT=stop
TELECODEX_LINEAR_ISSUE=<primary issue key or ->
TELECODEX_PHASE=<first ready phase id or ->
TELECODEX_BRANCH=<first feature branch or ->
```
