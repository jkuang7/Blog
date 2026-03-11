#!/bin/bash
# move_to_focused.sh - Move new windows to the focused workspace
# Triggered by: on-window-detected callback in aerospace.toml

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/jian"

source "/Users/jian/Dev/configs/aerospace/lib.sh"

# If another layout mutation is in progress, skip this event.
acquire_lock || exit 0

# Avoid reacting to short event bursts around workspace/focus churn.
if in_churn_window; then
    exit 0
fi

# Get focused workspace
FOCUSED_WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)

# Get the most recently created window (the new one)
LATEST_WID=$(aerospace list-windows --all --format '%{window-id}' 2>/dev/null | sort -n | tail -n1)

# Move it to focused workspace.
# Intentionally do NOT auto-focus here: focusing from this callback can race
# with on-focus handlers and trigger AeroSpace runtime errors in beta builds.
if [[ -n "$LATEST_WID" && -n "$FOCUSED_WS" ]]; then
    CURRENT_WS=$(aerospace list-windows --all --format '%{window-id}|%{workspace}' 2>/dev/null | grep "^$LATEST_WID|" | cut -d'|' -f2 || true)
    if [[ "$CURRENT_WS" != "$FOCUSED_WS" ]]; then
        set_churn_window
        aerospace move-node-to-workspace --window-id "$LATEST_WID" "$FOCUSED_WS" 2>/dev/null || true
    fi
fi
