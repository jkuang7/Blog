#!/bin/bash
# callbacks/: shell side-effect implementation invoked by engine wrapper.
# on_focus.sh - Handle focus changes and workspace switches
# Triggered by: on-focus-changed callback in aerospace.toml

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/jian"

source "/Users/jian/Dev/workspace/configs/aerospace/lib.sh"

# Get current workspace
WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
WS=$(normalize_ws "$WS")

# Only handle w1
is_home_ws "$WS" || exit 0

PERF_START="$(perf_mark_start)"
NEEDS_REBUILD="false"
IS_WS_CHANGE="false"
FOCUSED_INFO=""

# Acquire lock early to prevent parallel execution chaos
acquire_lock || exit 0
trap 'release_lock; perf_log_duration "on_focus" "$PERF_START" "ws=$WS ws_change=${IS_WS_CHANGE:-false} rebuild=${NEEDS_REBUILD:-false}"' EXIT

# Check for workspace change
LAST_WS=$(get_last_ws)

if [[ "$WS" != "$LAST_WS" && -n "$LAST_WS" ]]; then
    # Check if this is a "pull app" scenario:
    # - No churn window (means not triggered by switch_ws keybinding)
    # - Focused app is a home app
    # - User was in w1 before
    if ! in_churn_window; then
        FOCUSED_INFO=$(aerospace list-windows --focused --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null || echo "")
        PULL_APP=$(echo "$FOCUSED_INFO" | cut -d'|' -f2)
        PULL_WID=$(echo "$FOCUSED_INFO" | cut -d'|' -f1)

        # If focused app is a home app, pull it to previous workspace
        if [[ "$PULL_APP" == "$UPNOTE" || "$PULL_APP" == "$VSCODE" || "$PULL_APP" == "$CODEX" || "$PULL_APP" == "$TERMINAL" || "$PULL_APP" == "$TELEGRAM" || "$PULL_APP" == "$ZEN" || "$PULL_APP" == "$SAFARI" ]]; then
            if is_home_ws "$LAST_WS"; then
                log "on_focus: pulling $PULL_APP from $WS to $LAST_WS"

                # Pull app and switch back
                aerospace move-node-to-workspace --window-id "$PULL_WID" "$LAST_WS" 2>/dev/null

                # Set churn BEFORE switch to prevent cascade
                set_churn_window
                aerospace workspace "$LAST_WS" 2>/dev/null

                # Update state for pulled app
                read_state "$LAST_WS"
                if [[ "$PULL_APP" == "$UPNOTE" ]]; then
                    STATE_UPNOTE_TILED="true"
                elif [[ "$PULL_APP" == "$ZEN" ]]; then
                    STATE_BROWSER="zen"
                elif [[ "$PULL_APP" == "$SAFARI" ]]; then
                    STATE_BROWSER="safari"
                fi

                set_last_ws "$LAST_WS"
                get_home_windows
                rebuild_workspace "$LAST_WS"
                exit 0
            fi
        fi
    fi

    IS_WS_CHANGE="true"
    set_last_ws "$WS"
    set_churn_window
    log "on_focus: workspace change $LAST_WS -> $WS"
fi

# Get focused window info
if [[ -z "${FOCUSED_INFO:-}" || "$IS_WS_CHANGE" == "true" ]]; then
    FOCUSED_INFO=$(aerospace list-windows --focused --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null || echo "")
fi
FOCUSED_WID=$(echo "$FOCUSED_INFO" | cut -d'|' -f1)
FOCUSED_APP=$(echo "$FOCUSED_INFO" | cut -d'|' -f2)

# If just a focus change within workspace, check churn window
if [[ "$IS_WS_CHANGE" == "false" ]]; then
    if in_churn_window; then
        exit 0  # Ignore focus churn after workspace switch
    fi
fi

# Load current workspace state
read_state "$WS"
get_home_windows

# Resolve browser primary windows directly from live windows to avoid stale
# TILED_ORDER ids misclassifying which browser should drive retile decisions.
PRIMARY_ZEN_WID="$ZEN_WID"
PRIMARY_SAFARI_WID="$SAFARI_WID"
if [[ "$FOCUSED_APP" == "$ZEN" || "$FOCUSED_APP" == "$SAFARI" ]]; then
    ALL_WINDOWS_FOCUS="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    PRIMARY_ZEN_WID="$(get_primary_window_for_bundle "$ZEN" "$ALL_WINDOWS_FOCUS")"
    PRIMARY_SAFARI_WID="$(get_primary_window_for_bundle "$SAFARI" "$ALL_WINDOWS_FOCUS")"
fi

# Treat non-primary windows (auth popups/dialogs) as transient.
# They should stay floating and not trigger browser/layout state switches.
FOCUSED_IS_PRIMARY="true"
if [[ "$FOCUSED_APP" == "$VSCODE" && -n "$VSCODE_WID" && "$FOCUSED_WID" != "$VSCODE_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$CODEX" && -n "$CODEX_WID" && "$FOCUSED_WID" != "$CODEX_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$TERMINAL" && -n "$TERMINAL_WID" && "$FOCUSED_WID" != "$TERMINAL_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$TELEGRAM" && -n "$TELEGRAM_WID" && "$FOCUSED_WID" != "$TELEGRAM_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$ZEN" && -n "$PRIMARY_ZEN_WID" && "$FOCUSED_WID" != "$PRIMARY_ZEN_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$SAFARI" && -n "$PRIMARY_SAFARI_WID" && "$FOCUSED_WID" != "$PRIMARY_SAFARI_WID" ]]; then
    FOCUSED_IS_PRIMARY="false"
elif [[ "$FOCUSED_APP" == "$UPNOTE" && ${#UPNOTE_WIDS[@]} -gt 0 && "$FOCUSED_WID" != "${UPNOTE_WIDS[0]}" ]]; then
    FOCUSED_IS_PRIMARY="false"
fi

# Case 1: Workspace switch - always rebuild to restore state
if [[ "$IS_WS_CHANGE" == "true" ]]; then
    # Converge all windows into home workspace on entry, regardless of source ws.
    converge_all_windows_to_workspace "$WS"
    NEEDS_REBUILD="true"
    log "on_focus: rebuild for workspace switch"
fi

# Case 2: Browser focused - might need to swap active browser
if [[ "$FOCUSED_IS_PRIMARY" == "true" && "$FOCUSED_APP" == "$ZEN" && "$STATE_BROWSER" != "zen" ]]; then
    STATE_BROWSER="zen"
    NEEDS_REBUILD="true"
    log "on_focus: switching to Zen browser"
elif [[ "$FOCUSED_IS_PRIMARY" == "true" && "$FOCUSED_APP" == "$SAFARI" && "$STATE_BROWSER" != "safari" ]]; then
    STATE_BROWSER="safari"
    NEEDS_REBUILD="true"
    log "on_focus: switching to Safari browser"
fi

# Case 3: UpNote focused in w1 - tile it
if is_home_ws "$WS" && [[ "$FOCUSED_IS_PRIMARY" == "true" && "$FOCUSED_APP" == "$UPNOTE" && "$STATE_UPNOTE_TILED" != "true" ]]; then
    STATE_UPNOTE_TILED="true"
    NEEDS_REBUILD="true"
    log "on_focus: UpNote focused in w1, tiling"
fi

if [[ "$IS_WS_CHANGE" == "false" && "$FOCUSED_IS_PRIMARY" == "false" ]]; then
    log "on_focus: transient window focused ($FOCUSED_APP:$FOCUSED_WID), skipping rebuild"
    exit 0
fi

# Case 4: Detect closed apps - rebalance if layout changed
# Active browser closed but other browser available
if [[ "$STATE_BROWSER" == "zen" && -z "$ZEN_WID" && -n "$SAFARI_WID" ]]; then
    STATE_BROWSER="safari"
    NEEDS_REBUILD="true"
    log "on_focus: Zen closed, promoting Safari"
elif [[ "$STATE_BROWSER" == "safari" && -z "$SAFARI_WID" && -n "$ZEN_WID" ]]; then
    STATE_BROWSER="zen"
    NEEDS_REBUILD="true"
    log "on_focus: Safari closed, promoting Zen"
fi

# Case 5: UpNote closed in w1 - rebalance to 2-col
if is_home_ws "$WS" && [[ "$STATE_UPNOTE_TILED" == "true" && ${#UPNOTE_WIDS[@]} -eq 0 ]]; then
    STATE_UPNOTE_TILED="false"
    NEEDS_REBUILD="true"
    log "on_focus: UpNote closed in w1, rebalancing"
fi


# Rebuild if needed
# Use "force" when layout state changed (tiling toggle, browser swap)
# to ensure windows get re-tiled, not just rebalanced
if [[ "$NEEDS_REBUILD" == "true" ]]; then
    rebuild_workspace "$WS" force
else
    log "on_focus: no state/layout delta, skipping rebuild"
fi
