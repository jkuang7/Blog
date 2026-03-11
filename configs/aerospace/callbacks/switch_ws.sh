#!/bin/bash
# callbacks/: shell side-effect implementation invoked by engine wrapper.
# switch_ws.sh - Switch to workspace and restore its state
# Usage: switch_ws.sh <workspace>
# Keybindings: ctrl+1 -> switch_ws.sh w1

set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/lib.sh"

TARGET_WS=$(normalize_ws "${1:-w1}")

# Validate workspace - only home workspace supported
is_home_ws "$TARGET_WS" || exit 0

# Acquire lock (prevents concurrent operations)
acquire_lock || exit 0

log "switch_ws $TARGET_WS"

# Get current workspace
CURRENT_WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
CURRENT_WS=$(normalize_ws "$CURRENT_WS")

# Save current workspace state if we're leaving w1
if is_home_ws "$CURRENT_WS"; then
    read_state "$CURRENT_WS"
    # Capture current UpNote tiled state before leaving
    get_home_windows
    if [[ ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        upnote_layout=$(aerospace list-windows --workspace "$CURRENT_WS" \
            --format '%{window-id}|%{window-layout}' 2>/dev/null \
            | grep "^${UPNOTE_WIDS[0]}|" | cut -d'|' -f2 || true)
        if [[ "$upnote_layout" == *"tiles"* ]]; then
            STATE_UPNOTE_TILED="true"
        else
            STATE_UPNOTE_TILED="false"
        fi
    fi
    write_state "$CURRENT_WS"
fi

# Set churn window BEFORE switch (so on_focus knows this is intentional)
set_churn_window

# Switch to target workspace
aerospace workspace "$TARGET_WS" 2>/dev/null

# Bring all windows along so context follows workspace switches.
converge_all_windows_to_workspace "$TARGET_WS"

# Update last workspace tracking
set_last_ws "$TARGET_WS"

# Load target workspace state and rebuild
read_state "$TARGET_WS"

# w1 defaults to UpNote visible, ensure it's here
get_home_windows
if [[ "$STATE_UPNOTE_TILED" == "true" ]]; then
    if [[ ${#UPNOTE_WIDS[@]} -eq 0 ]]; then
        # UpNote not running, launch it
        log "switch_ws: launching UpNote for $TARGET_WS"
        open -a UpNote
        sleep 0.15
        get_home_windows
    elif [[ ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        # UpNote running, pull all windows to this workspace
        log "switch_ws: pulling UpNote to $TARGET_WS"
        for upnote_wid in "${UPNOTE_WIDS[@]}"; do
            aerospace move-node-to-workspace --window-id "$upnote_wid" "$TARGET_WS" 2>/dev/null || true
        done
    fi
fi

rebuild_workspace "$TARGET_WS"

log "switch_ws $TARGET_WS complete"
