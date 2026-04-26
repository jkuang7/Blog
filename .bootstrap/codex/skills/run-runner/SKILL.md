---
name: run-runner
description: Deprecated. Telecodex owns Telegram-triggered Codex execution; use Linear phase tickets and Telecodex status instead of local tmux-codex loops.
---

# Deprecated Runner Skill

The local tmux-codex runner loop has been removed.

Do not run setup prompts, queue local automation state, launch `cl` loop commands, or call removed local automation paths from this skill. For `/add`, `/run`, and `/review`, use the Telecodex Linear control plane:

```text
Telegram -> Telecodex -> Codex -> Linear
```

`cl` and `clls` are only local tmux session tools. They may show Telecodex sessions as read-only runner status, but they do not own execution.
