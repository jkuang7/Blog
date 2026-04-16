#!/bin/bash
# callbacks/: shell side-effect implementation invoked by keybindings.
# move_and_balance.sh - move focused window, then rebalance managed layouts

set -euo pipefail

source "/Users/jian/Dev/workspace/configs/aerospace/lib.sh"

acquire_lock_with_retry() {
    local attempts="${1:-20}"
    local sleep_sec="${2:-0.05}"
    local i=0
    while (( i < attempts )); do
        if acquire_lock; then
            return 0
        fi
        sleep "$sleep_sec"
        i=$((i + 1))
    done
    return 1
}

DIRECTION="${1:-}"
[[ -n "$DIRECTION" ]] || exit 1

FOCUSED_INFO="$(aerospace list-windows --focused --format '%{window-id}|%{workspace}|%{app-bundle-id}' 2>/dev/null || true)"
FOCUSED_WID="$(echo "$FOCUSED_INFO" | cut -d'|' -f1)"
FOCUSED_WS="$(normalize_ws "$(echo "$FOCUSED_INFO" | cut -d'|' -f2)")"
FOCUSED_BUNDLE="$(echo "$FOCUSED_INFO" | cut -d'|' -f3)"

is_home_ws "$FOCUSED_WS" || exit 0

case "$FOCUSED_BUNDLE" in
    "$VSCODE"|"$CODEX"|"$TERMINAL"|"$TELEGRAM"|"$ZEN"|"$SAFARI"|"$UPNOTE")
        acquire_lock_with_retry 30 0.05 || exit 0
        trap 'release_lock' EXIT

        read_state "$FOCUSED_WS"
        case "$FOCUSED_BUNDLE" in
            "$CODEX"|"$TERMINAL"|"$TELEGRAM")
                STATE_ACTIVE_UTILITY_BUNDLE="$FOCUSED_BUNDLE"
                STATE_ACTIVE_UTILITY_WID="$FOCUSED_WID"
                ;;
            "$ZEN")
                STATE_BROWSER="zen"
                ;;
            "$SAFARI")
                STATE_BROWSER="safari"
                ;;
            "$UPNOTE")
                STATE_UPNOTE_TILED="true"
                ;;
        esac
        write_state "$FOCUSED_WS"

        set_churn_window
        aerospace move "$DIRECTION" 2>/dev/null || true
        rebuild_workspace "$FOCUSED_WS" force
        ;;
    *)
        aerospace move "$DIRECTION" 2>/dev/null || exit 0
        ;;
esac
