# Lessons

## ORX-Managed Runner Contract

- For ORX-managed work, the active execution packet is the only live objective source.
- `OBJECTIVE.json`, `SEAMS.json`, `TASKS.json`, `GAPS.json`, `KANBAN_STATE.json`, and `RUNNER_ACTIVE_BACKLOG.json` are cache or recovery surfaces only; they must not override a fresh ORX selection.
- If the active issue in ORX and the local runner files disagree, trust ORX and treat the local files as stale until refreshed.

## Ticket And Handoff Contract

- Runnable Linear leaf tickets should be stateless enough for a fresh medium-tier Codex worker:
  - objective
  - why / goal
  - scope and constraints
  - ordered steps
  - verification
  - stopping conditions
  - blocked / escalation guidance
- The mutable `Latest Handoff` section is the resume surface for ongoing work.
- `Latest Handoff` should capture:
  - what changed
  - what was verified
  - blockers, risks, and lessons
  - the next direction
  - the execution tier and why ORX selected it

## ORX Interpretation Boundary

- tmux-codex executes one bounded slice and reports facts.
- ORX interprets the handoff and decides:
  - continue
  - block
  - reroute
  - replan
  - complete
  - create follow-up work
- The executor should not decide workflow transitions from local prompt logic.

## Tier Routing

- Use `medium` by default for runnable leaves.
- Escalate to `high` when execution surfaces owner mismatch, blocker sets, verification failures, or multiple live risks.
- Escalate to `xhigh` when execution surfaces scope mismatch, packet resequencing need, or ambiguity large enough that ORX must replan before another slice.
- Tier changes should be recorded in ORX and reflected back into the ticket handoff.

## Follow-Up Ticket Policy

- ORX creates follow-up tickets, not the executor.
- Follow-ups should be linked and deduped using stable origin + relationship + class + title identity, so replaying the same blocker does not create duplicate children.
- If a blocker is small and tightly coupled to the current leaf, ORX may keep it inline; otherwise it should create or route follow-up work explicitly.

## Packet / Worktree Policy

- Use one runner session per project.
- Prefer one shared packet worktree for tightly related tickets that should land together.
- Keep final integration explicit under HIL:
  - merge to `main`
  - or cherry-pick from the packet branch into `main`
