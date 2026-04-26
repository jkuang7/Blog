# tmux-codex (`cl` / `clls`)

tmux-codex is a small tmux wrapper for local interactive Codex sessions.

Telegram-triggered Codex work is owned by Telecodex. tmux-codex only shows that
work as read-only runner status by reading Telecodex SQLite profile databases.

## Commands

- `cl` - interactive menu
- `cl ls` / `clls` - same interactive entrypoint
- `cl ls <number>` - attach directly to a numbered tmux Codex session
- `cl <prompt...>` - create a new interactive Codex session with prompt args

Removed commands:

- `cl loop`, `cl loop-bg`, `cl runner`, `cl run`, `cl r`
- `cl stop`, `cl k`, `cl ka`, `cl kb`, `cl k*`

Use Telegram/Telecodex commands such as `/add`, `/run`, `/review`, `/sessions`,
and `/loop_status` for Telegram-controlled work.

## Runner View

The `Runners` section in `cl`/`clls` is a Telecodex status view:

- it reads `workspace/telecodex/.telecodex/profiles/*/data/telecodex.sqlite3`
- it shows sessions with `sessions.busy = 1`
- it also shows the controller session when Telecodex `runner_state` is active
- rows include profile, title or chat/thread id, cwd, step, and scoped issue when present

Runner rows are not tmux sessions and cannot be attached from tmux-codex.

## Model Mapping

- Plain interactive `cl` sessions inherit the global Codex default profile.
- Planning/execution prompt launches such as `/plan`, `/kanban`, `/integrate`,
  `/spec`, `/refactor`, `/enhance`, `/prune`, and `/review` are promoted to
  `gpt-5.4` with high reasoning at launch time.

## Local State

- tmux-codex session tags live under the local Codex home.
- Telecodex session state lives under `workspace/telecodex/.telecodex/`.
- `.memory/**`, logs, caches, and generated debug output are local runtime state
  unless a deeper repo explicitly documents a checked-in guidance file.

## Test

```bash
cd /Users/jian/Dev/workspace/tmux-codex
python3 -m unittest discover -s tests -p 'test_*.py'
```
