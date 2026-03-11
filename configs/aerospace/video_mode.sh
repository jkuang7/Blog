#!/bin/bash
# video_mode.sh - Toggle video mode (Zen 100%, others hidden)
# Usage: video_mode.sh [on|off|toggle]

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/jian"

source "/Users/jian/Dev/configs/aerospace/lib.sh"

MODE="${1:-toggle}"
WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
WS=$(normalize_ws "$WS")

# Only work in w1
is_home_ws "$WS" || exit 0

VIDEO_MODE_FILE="$STATE_DIR/video_mode_${WS}"

is_video_mode() {
    [[ -f "$VIDEO_MODE_FILE" ]]
}

enter_video_mode() {
    log "video_mode: entering video mode in $WS"

    # Save current state for restore
    read_state "$WS"
    echo "$STATE_UPNOTE_TILED" > "$VIDEO_MODE_FILE"

    # Hide VSCode and UpNote
    hide_app "Code"
    hide_app "UpNote"

    # Get Zen window
    get_home_windows

    if [[ -n "$ZEN_WID" ]]; then
        # Make Zen fill the workspace
        aerospace layout --window-id "$ZEN_WID" tiling 2>/dev/null || true
        aerospace flatten-workspace-tree 2>/dev/null || true
        aerospace balance-sizes 2>/dev/null || true
        aerospace focus --window-id "$ZEN_WID" 2>/dev/null || true
    fi

    log "video_mode: entered"
}

exit_video_mode() {
    log "video_mode: exiting video mode in $WS"

    # Restore previous upnote state
    local prev_upnote
    prev_upnote=$(cat "$VIDEO_MODE_FILE" 2>/dev/null || echo "false")
    rm -f "$VIDEO_MODE_FILE"

    # Show apps again
    show_app "Code"
    if [[ "$prev_upnote" == "true" ]]; then
        show_app "UpNote"
    fi

    sleep 0.1

    # Rebuild normal layout
    read_state "$WS"
    STATE_UPNOTE_TILED="$prev_upnote"
    rebuild_workspace "$WS"

    log "video_mode: exited"
}

case "$MODE" in
    on)
        enter_video_mode
        ;;
    off)
        exit_video_mode
        ;;
    toggle)
        if is_video_mode; then
            exit_video_mode
        else
            enter_video_mode
        fi
        ;;
esac
