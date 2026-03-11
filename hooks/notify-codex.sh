#!/bin/bash
set -euo pipefail

# Codex notification hook
# Works both locally (Mac sound) and over SSH (tmux message)

# Only read stdin if something is actually piped in
if [ -t 0 ]; then
  JSON_INPUT=""
else
  JSON_INPUT=$(cat)
fi

MESSAGE="Codex: Chat completed"

# Mac sound (fails silently over SSH, works locally)
afplay /System/Library/Sounds/Pop.aiff 2>/dev/null &

# Terminal bell (works over SSH)
printf '\a'

# tmux message (works over SSH if in tmux session)
if [ -n "${TMUX:-}" ]; then
  tmux display-message "$MESSAGE" 2>/dev/null || true
fi

echo "[$(date)] $MESSAGE"
