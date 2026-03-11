#!/bin/bash
# callbacks/: shell side-effect implementation invoked by engine wrapper.
# reset_ws.sh - Reset current workspace to defaults
# Usage: reset_ws.sh
# Keybinding: ctrl+w

set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/lib.sh"

# Get current workspace
WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
WS=$(normalize_ws "$WS")

# Only handle w1
is_home_ws "$WS" || exit 0

# Acquire lock
acquire_lock || exit 0

log "reset_ws: resetting $WS to defaults"

# Reset to workspace defaults (don't read existing state)
STATE_BROWSER="$w1_default_browser"
STATE_UPNOTE_TILED="$w1_default_upnote"

# Launch UpNote if not running (since w1 defaults to UpNote visible)
get_home_windows
if [[ ${#UPNOTE_WIDS[@]} -eq 0 ]]; then
    log "reset_ws: launching UpNote for w1"
    open -a UpNote
    sleep 0.5
    get_home_windows
fi

# Rebuild with default state (force to fix column order)
rebuild_workspace "$WS" force

log "reset_ws: $WS reset complete"
