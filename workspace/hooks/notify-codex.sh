#!/bin/bash
set -euo pipefail

# Codex notification hook
# Works both locally (macOS notification + sound) and over SSH (tmux + bell)

if [ -t 0 ]; then
  JSON_INPUT=""
else
  JSON_INPUT=$(cat)
fi

EVENT_INFO=$(
  PAYLOAD="$JSON_INPUT" /usr/bin/python3 <<'PY'
import json
import os

raw = os.environ.get("PAYLOAD", "").strip()

if not raw:
    print("notify\tCodex\tCodex finished a turn.")
    raise SystemExit(0)

try:
    data = json.loads(raw)
except Exception:
    print("notify\tCodex\tCodex needs attention.")
    raise SystemExit(0)

def pick(obj, *names):
    for name in names:
        value = obj.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

def nested_pick(obj, *paths):
    for path in paths:
        current = obj
        for part in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if isinstance(current, str) and current.strip():
            return current.strip()
    return ""

event = pick(
    data,
    "event",
    "type",
    "event_name",
    "notification",
    "notification_type",
    "kind",
)
title = pick(data, "title") or nested_pick(
    data,
    ("question", "title"),
    ("question", "header"),
    ("request", "title"),
)
message = (
    pick(data, "message", "subtitle")
    or nested_pick(
        data,
        ("question", "prompt"),
        ("question", "question"),
        ("request", "prompt"),
        ("request", "question"),
    )
)

event_key = event.lower().replace("_", "-").replace("/", "-")
text_haystack = " ".join(part for part in (event_key, title.lower(), message.lower()) if part)

completion_events = {
    "agent-turn-complete",
    "turn-complete",
    "turn-completed",
    "assistant-turn-complete",
}
attention_events = {
    "approval-requested",
    "approval-required",
    "input-requested",
    "user-input-requested",
    "question-asked",
    "question-requested",
    "question.asked",
}

looks_like_attention = any(
    token in text_haystack
    for token in (
        "approval",
        "question",
        "select",
        "selection",
        "choose",
        "input",
        "waiting for your input",
    )
)

if event_key in completion_events:
    print(f"notify\t{title or 'Codex Complete'}\t{message or 'Codex finished a task.'}")
elif event_key in attention_events or looks_like_attention:
    print(f"notify\t{title or 'Codex Needs Attention'}\t{message or 'Codex is waiting for your input.'}")
elif not event_key:
    print(f"notify\t{title or 'Codex'}\t{message or 'Codex needs attention.'}")
else:
    print(f"notify\t{title or 'Codex'}\t{message or 'Codex activity needs review.'}")
PY
)

IFS=$'\t' read -r ACTION TITLE MESSAGE <<<"$EVENT_INFO"

if [ "$ACTION" = "skip" ]; then
  exit 0
fi

SOUND_FILE="/System/Library/Sounds/Pop.aiff"
if [[ "$TITLE" == *"Attention"* ]] || [[ "$MESSAGE" == *"waiting for your input"* ]]; then
  SOUND_FILE="/System/Library/Sounds/Glass.aiff"
fi

IS_APPLE_TERMINAL=0
if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" ]]; then
  IS_APPLE_TERMINAL=1
fi

# macOS desktop notification (fails silently in headless/SSH cases)
ESCAPED_TITLE=${TITLE//\\/\\\\}
ESCAPED_TITLE=${ESCAPED_TITLE//\"/\\\"}
ESCAPED_MESSAGE=${MESSAGE//\\/\\\\}
ESCAPED_MESSAGE=${ESCAPED_MESSAGE//\"/\\\"}
/usr/bin/osascript -e "display notification \"$ESCAPED_MESSAGE\" with title \"$ESCAPED_TITLE\"" >/dev/null 2>&1 || true

# macOS sound
afplay "$SOUND_FILE" 2>/dev/null &

# Avoid terminal-local activity signals in Apple Terminal. They can steal focus
# to the tab that finished, which is the opposite of what we want for Codex
# sessions running in background tabs.
if [[ "$IS_APPLE_TERMINAL" -ne 1 ]]; then
  printf '\a'
fi

# tmux message
if [ -n "${TMUX:-}" ]; then
  tmux display-message "$TITLE: $MESSAGE" 2>/dev/null || true
fi

if [[ "$IS_APPLE_TERMINAL" -ne 1 ]]; then
  echo "[$(date)] $TITLE: $MESSAGE"
fi
