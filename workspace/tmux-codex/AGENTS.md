# tmux-codex Agent Rules

## Role

- tmux-codex is a local tmux viewer/launcher for interactive Codex sessions.
- It does not own Telegram execution or start background runner loops.
- Telecodex owns Telegram-triggered Codex work.

## Runner View

- In tmux-codex, "runner" means a Telecodex Telegram session that is busy or automation-active.
- Read runner status from Telecodex SQLite profile databases under `workspace/telecodex/.telecodex/`.
- Runner rows in `cl`/`clls` are status-only; do not try to attach to them as tmux sessions.

## Durable Work State

- Linear issues and `telecodex:phase` comments are the durable execution brief.
- Telecodex SQLite state is local session/controller state.
- `.memory/**` is cache or recovery context unless a deeper repo explicitly documents a checked-in guidance file.

## Avoid

- Do not recreate tmux-codex background execution loops.
- Do not add `runner-*` tmux session launch paths.
- Do not treat local runner files as the planner of record.
