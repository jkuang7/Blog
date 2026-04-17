# tmux-codex Agent Rules

## Role

- tmux-codex is the execute-only worker for ORX-managed work.
- `runner-<project>` sessions are the canonical execution sessions for ORX-managed projects.

## Execution Contract

- The ORX execution packet is the only live objective source.
- Report factual slice results:
  - what changed
  - what was verified
  - blockers, risks, and lessons
- Do not decide routing, follow-up creation, packet resequencing, or model-tier changes locally.

## Runner Memory

- Treat `.memory/runner` as cache and recovery breadcrumbs, not truth.
- Stale local runner files must never override fresh ORX state.
- Keep `.memory/**` ephemeral except for intentional checked-in guidance such as `lessons.md`.

## Avoid

- Do not rebuild a competing planner from local task files for ORX-managed work.
- Do not force model-tier authority back into local prompts or local runner state.
