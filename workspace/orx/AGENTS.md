# ORX Agent Rules

## Role

- ORX is the deterministic control plane.
- Use Codex inside ORX for contextual interpretation, but keep ORX authoritative for state, routing, mutations, and workflow transitions.

## Ownership

- ORX owns intake, decomposition, project routing, dependency checks, queueing, recovery, packet policy, follow-up ticket creation, and execution-tier choice.
- ORX, not tmux-codex, decides what runs next after each slice.
- `Latest Handoff` is the authoritative resume surface; raw executor facts are inputs, not decisions.

## Contracts

- Treat execution packets as the only live objective source for ORX-managed work.
- Keep Linear tickets stateless enough for medium-tier execution and update their mutable handoff sections through ORX.
- Preserve revision- and dedupe-safety on slice results, handoffs, and follow-up creation.
- For visual work, keep the control-plane gate order explicit: design review first, then `/ui-contracts` contract review, then implementation closeout with live UI evidence.

## Avoid

- Do not reintroduce local task-file selection or runner-local planning as a source of truth.
- Do not let Codex mutate workflow state outside ORX validation and mutation paths.
