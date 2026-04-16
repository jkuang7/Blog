#!/bin/bash
# callbacks/: shell side-effect implementation invoked by engine wrapper.
# on_window.sh - Handle home app window opens
# Triggered by: on-window-detected callback in aerospace.toml
# Usage: on_window.sh <bundle-id>
#
# Uses trailing-edge coalesce with SNAPSHOT deduplication.
# - Waits for window-list stability (2+ consecutive identical samples)
# - Compares to last applied snapshot to avoid redundant rebuilds
# - Rebuilds if actual window IDs changed (handles fullscreen exit)

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export HOME="/Users/jian"

source "/Users/jian/Dev/workspace/configs/aerospace/lib.sh"

BUNDLE="${1:-}"
[[ -z "$BUNDLE" ]] && exit 0

# Get current workspace
WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
WS=$(normalize_ws "$WS")

# Only handle w1
is_home_ws "$WS" || exit 0

# Only handle home apps
case "$BUNDLE" in
    "$VSCODE"|"$CODEX"|"$TERMINAL"|"$TELEGRAM"|"$ZEN"|"$SAFARI"|"$UPNOTE") ;;
    *) exit 0 ;;
esac

# === Trailing-edge coalesce with snapshot deduplication ===

JOB_LOCK="$STATE_DIR/pending_rebuild_job_${WS}.lock"
STABILITY_MAX_SAMPLES="${AEROSPACE_STABILITY_MAX_SAMPLES:-8}"
STABILITY_SLEEP_SEC="${AEROSPACE_STABILITY_SLEEP_SEC:-0.05}"
PERF_DISPATCH_START="$(perf_mark_start)"
DISPATCH_RESULT="coalesced"

# Recover from stale scheduler lock (e.g. crashed callback path).
if [[ -f "$JOB_LOCK" ]]; then
    now_s=$(date +%s)
    lock_mtime=$(stat -f %m "$JOB_LOCK" 2>/dev/null || echo 0)
    if [[ "$lock_mtime" =~ ^[0-9]+$ && "$lock_mtime" -gt 0 ]]; then
        lock_age=$(( now_s - lock_mtime ))
    else
        lock_age=0
    fi
    if [[ "$lock_age" -gt 15 ]]; then
        log "on_window: clearing stale job lock (${lock_age}s)"
        rm -f "$JOB_LOCK"
    fi
fi

# Store the bundle for state update
echo "$BUNDLE" > "$STATE_DIR/pending_bundle_${WS}"

# Start exactly ONE scheduler per workspace (noclobber = atomic)
if ( set -o noclobber; echo "$$" > "$JOB_LOCK" ) 2>/dev/null; then
    DISPATCH_RESULT="scheduled"
    (
        # Source lib.sh again in subshell
        source "/Users/jian/Dev/workspace/configs/aerospace/lib.sh"
        PERF_SCHED_START="$(perf_mark_start)"
        PERF_DECISION="pending"
        STABILITY_SAMPLE_COUNT=0

        finalize_perf() {
            perf_log_duration "on_window.scheduler" "$PERF_SCHED_START" \
                "ws=${ws:-$WS} bundle=$BUNDLE decision=$PERF_DECISION stability_samples=$STABILITY_SAMPLE_COUNT"
        }

        cleanup_scheduler() {
            rm -f "$JOB_LOCK"
            finalize_perf
        }

        cleanup_scheduler_with_lock() {
            release_lock
            rm -f "$JOB_LOCK"
            finalize_perf
        }

        # Always cleanup scheduler lock, even on unexpected failures.
        trap 'cleanup_scheduler' EXIT

        # Replace bootstrap PID with scheduler shell PID for diagnostics.
        echo "$$" > "$JOB_LOCK"

        ws="$WS"
        LAST_APPLIED_FILE="$STATE_DIR/last_applied_snapshot_${ws}"

        # Get snapshot of home app windows (first per app only, matching layout engine)
        # Extra windows (SSO popups, etc.) are excluded so they don't trigger rebuilds
        snapshot_windows() {
            aerospace list-windows --workspace "$1" --format '%{app-name}|%{window-id}' 2>/dev/null \
                | grep -E '^(Zen|Safari|Code|Codex|UpNote|Terminal|Telegram)\|[0-9]+$' \
                | awk -F'|' '
                    {
                        app=$1
                        wid=$2+0
                        if (!(app in min) || wid < min[app]) {
                            min[app]=wid
                        }
                    }
                    END {
                        for (app in min) {
                            print app "|" min[app]
                        }
                    }
                ' \
                | sort
        }

        # Wait for stability: 2 consecutive identical samples.
        # Latency tuned for app-open responsiveness while still suppressing churn.
        last="" cur="" stable=0
        i=0
        while (( i < STABILITY_MAX_SAMPLES )); do
            cur="$(snapshot_windows "$ws")"
            if [[ -n "$cur" && "$cur" == "$last" ]]; then
                stable=$((stable+1))
            else
                stable=0
            fi
            last="$cur"
            [[ $stable -ge 1 ]] && break
            sleep "$STABILITY_SLEEP_SEC"
            i=$((i + 1))
        done
        STABILITY_SAMPLE_COUNT=$((i + 1))

        # Gather focused info early for intent checks.
        FINFO_EARLY="$(aerospace list-windows --focused --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null || true)"
        FOCUSED_WID_EARLY="$(echo "$FINFO_EARLY" | cut -d'|' -f1)"
        FOCUSED_APP_EARLY="$(echo "$FINFO_EARLY" | cut -d'|' -f2)"
        FOCUSED_TITLE_EARLY="$(echo "$FINFO_EARLY" | cut -d'|' -f3-)"
        FOCUSED_EARLY_POPUP="false"
        if is_popup_title "$FOCUSED_TITLE_EARLY"; then
            FOCUSED_EARLY_POPUP="true"
        fi

        ALL_WINDOWS_EARLY="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-title}|%{window-layout}' 2>/dev/null || true)"
        LATEST_BUNDLE_WID_EARLY="$(echo "$ALL_WINDOWS_EARLY" \
            | awk -F'|' -v b="$BUNDLE" '$2==b { if ($1+0 > m) m=$1+0 } END { if (m>0) print m }')"
        LATEST_TITLE_EARLY=""
        LATEST_POPUP_EARLY="false"
        BUNDLE_HAS_NONPOPUP_WINDOW_EARLY="false"
        if [[ -n "$LATEST_BUNDLE_WID_EARLY" ]]; then
            LATEST_TITLE_EARLY="$(echo "$ALL_WINDOWS_EARLY" \
                | awk -F'|' -v wid="$LATEST_BUNDLE_WID_EARLY" '$1==wid { print $3; exit }')"
            if is_popup_title "$LATEST_TITLE_EARLY"; then
                LATEST_POPUP_EARLY="true"
            fi
            if echo "$ALL_WINDOWS_EARLY" | awk -F'|' -v b="$BUNDLE" -v re="$POPUP_TITLE_AWK_REGEX" '
                $2==b && tolower($3) !~ re { found=1 }
                END { exit(found ? 0 : 1) }
            '; then
                BUNDLE_HAS_NONPOPUP_WINDOW_EARLY="true"
            fi
        fi

        # Load state early for browser-switch decisions in dedup path.
        read_state "$ws"
        ACTIVE_BROWSER_BUNDLE_EARLY=""
        if [[ "${STATE_BROWSER:-}" == "zen" ]]; then
            ACTIVE_BROWSER_BUNDLE_EARLY="$ZEN"
        elif [[ "${STATE_BROWSER:-}" == "safari" ]]; then
            ACTIVE_BROWSER_BUNDLE_EARLY="$SAFARI"
        fi

        # Dedup: if snapshot unchanged since last successful rebuild, skip
        prev="$(cat "$LAST_APPLIED_FILE" 2>/dev/null || true)"
        PENDING_BUNDLE="$(cat "$STATE_DIR/pending_bundle_${ws}" 2>/dev/null || echo "")"
        if [[ -n "$cur" && "$cur" == "$prev" ]]; then
            case "$PENDING_BUNDLE" in
                "$ZEN"|"$SAFARI")
                    if should_allow_browser_snapshot_rebuild \
                        "$PENDING_BUNDLE" \
                        "$ACTIVE_BROWSER_BUNDLE_EARLY" \
                        "$BUNDLE_HAS_NONPOPUP_WINDOW_EARLY" \
                        "$FOCUSED_APP_EARLY" \
                        "$FOCUSED_EARLY_POPUP" \
                        "$LATEST_POPUP_EARLY"; then
                        :
                    else
                        if [[ -n "$LATEST_BUNDLE_WID_EARLY" && "$PENDING_BUNDLE" == "$ACTIVE_BROWSER_BUNDLE_EARLY" ]]; then
                            aerospace layout --window-id "$LATEST_BUNDLE_WID_EARLY" floating 2>/dev/null || true
                            set_churn_window
                            aerospace focus --window-id "$LATEST_BUNDLE_WID_EARLY" 2>/dev/null || true
                            log "on_window: raised floating browser window above tiles"
                        elif [[ "$PENDING_BUNDLE" != "$ACTIVE_BROWSER_BUNDLE_EARLY" ]]; then
                            if [[ "$PENDING_BUNDLE" == "$ZEN" ]]; then
                                hide_bundle_app "$ZEN"
                                hide_app "zen"
                            elif [[ "$PENDING_BUNDLE" == "$SAFARI" ]]; then
                                hide_bundle_app "$SAFARI"
                                hide_app "Safari"
                            fi
                            log "on_window: kept inactive browser hidden"
                        fi
                        log "on_window: snapshot unchanged for browser, skipping rebuild"
                        PERF_DECISION="snapshot_unchanged_browser_skip"
                        exit 0
                    fi
                    ;;
                "$UPNOTE")
                    # UpNote may reopen from hidden state with the same window-id
                    # (snapshot unchanged). If it's currently untiled in state,
                    # still run the normalize/rebuild path so core order becomes
                    # UpNote -> VSCode -> Codex -> Terminal -> Telegram -> Browser.
                    if [[ "${STATE_UPNOTE_TILED:-false}" == "true" ]]; then
                        log "on_window: snapshot unchanged for UpNote, skipping rebuild"
                        PERF_DECISION="snapshot_unchanged_upnote_skip"
                        exit 0
                    fi
                    log "on_window: snapshot unchanged but UpNote untiled, allowing rebuild"
                    ;;
                *)
                    log "on_window: snapshot unchanged, skipping rebuild"
                    PERF_DECISION="snapshot_unchanged_skip"
                    exit 0
                    ;;
            esac
        fi

        # Snapshot changed or first run - proceed with rebuild
        acquire_lock || {
            PERF_DECISION="lock_contended"
            exit 0
        }
        trap 'cleanup_scheduler_with_lock' EXIT

        # Get the last bundle that triggered
        LAST_BUNDLE="$(cat "$STATE_DIR/pending_bundle_${ws}" 2>/dev/null || echo "")"

        log "on_window: $LAST_BUNDLE opened in $ws (snapshot changed)"

        read_state "$ws"
        get_home_windows
        ACTIVE_BROWSER_BUNDLE=""
        if [[ "${STATE_BROWSER:-}" == "zen" ]]; then
            ACTIVE_BROWSER_BUNDLE="$ZEN"
        elif [[ "${STATE_BROWSER:-}" == "safari" ]]; then
            ACTIVE_BROWSER_BUNDLE="$SAFARI"
        fi

        # Guard 1: opening transient popup windows must not trigger rebuild.
        FOCUSED_INFO="$(aerospace list-windows --focused --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null || true)"
        FOCUSED_WID="$(echo "$FOCUSED_INFO" | cut -d'|' -f1)"
        FOCUSED_APP="$(echo "$FOCUSED_INFO" | cut -d'|' -f2)"
        FOCUSED_TITLE="$(echo "$FOCUSED_INFO" | cut -d'|' -f3-)"
        FOCUSED_LOOKS_POPUP="false"
        if is_popup_title "$FOCUSED_TITLE"; then
            FOCUSED_LOOKS_POPUP="true"
        fi
        ALL_WINDOWS_LATE="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
        RESOLVED_UTILITY_PAIR="$(resolve_active_utility_window "$ALL_WINDOWS_LATE" "$FOCUSED_APP" "$FOCUSED_WID" "$FOCUSED_LOOKS_POPUP")"
        RESOLVED_UTILITY_BUNDLE="$(echo "$RESOLVED_UTILITY_PAIR" | cut -d'|' -f1)"
        RESOLVED_UTILITY_WID="$(echo "$RESOLVED_UTILITY_PAIR" | cut -d'|' -f2)"
        PRIMARY_WID=""
        case "$LAST_BUNDLE" in
            "$VSCODE") PRIMARY_WID="$VSCODE_WID" ;;
            "$CODEX"|"$TERMINAL"|"$TELEGRAM")
                if [[ "$RESOLVED_UTILITY_BUNDLE" == "$LAST_BUNDLE" ]]; then
                    PRIMARY_WID="$RESOLVED_UTILITY_WID"
                fi
                ;;
            "$ZEN") PRIMARY_WID="$(get_primary_window_for_bundle "$ZEN" "$ALL_WINDOWS_LATE")" ;;
            "$SAFARI") PRIMARY_WID="$(get_primary_window_for_bundle "$SAFARI" "$ALL_WINDOWS_LATE")" ;;
            "$UPNOTE") PRIMARY_WID="${UPNOTE_WIDS[0]:-}" ;;
        esac
        LATEST_BUNDLE_WID="$(get_latest_window_for_bundle_from_snapshot "$ALL_WINDOWS_LATE" "$LAST_BUNDLE")"
        BROWSER_MAIN_INTENT="false"
        case "$LAST_BUNDLE" in
            "$ZEN"|"$SAFARI")
                if [[ "$FOCUSED_APP" == "$LAST_BUNDLE" && "$FOCUSED_WID" == "$LATEST_BUNDLE_WID" && "$FOCUSED_LOOKS_POPUP" != "true" ]]; then
                    BROWSER_MAIN_INTENT="true"
                fi
                ;;
        esac

        # If this app already has a tiled primary window, any additional windows
        # from the same app are float-intent (OAuth/download/login popups).
        BUNDLE_WINDOW_COUNT="$(get_window_count_for_bundle "$LAST_BUNDLE" "$ALL_WINDOWS_LATE")"
        PRIMARY_LAYOUT=""
        if [[ -n "$PRIMARY_WID" ]]; then
            PRIMARY_LAYOUT="$(get_window_layout_for_id_from_snapshot "$ALL_WINDOWS_LATE" "$PRIMARY_WID")"
        fi
        if [[ -n "$PRIMARY_WID" && "$PRIMARY_LAYOUT" == *tiles* && "$BUNDLE_WINDOW_COUNT" -gt 1 ]]; then
            IS_BROWSER_BUNDLE="false"
            case "$LAST_BUNDLE" in
                "$ZEN"|"$SAFARI") IS_BROWSER_BUNDLE="true" ;;
            esac

            if [[ "$IS_BROWSER_BUNDLE" == "true" && "$BROWSER_MAIN_INTENT" == "true" ]]; then
                log "on_window: browser main-window intent for $LAST_BUNDLE, allowing retile"
            elif is_utility_bundle "$LAST_BUNDLE" && [[ "$FOCUSED_APP" == "$LAST_BUNDLE" && "$FOCUSED_WID" == "$LATEST_BUNDLE_WID" && "$FOCUSED_LOOKS_POPUP" != "true" ]]; then
                log "on_window: utility main-window intent for $LAST_BUNDLE, allowing retile"
            else
                log "on_window: secondary window for tiled $LAST_BUNDLE, skipping rebuild"
                if [[ -n "$LATEST_BUNDLE_WID" && "$LATEST_BUNDLE_WID" != "$PRIMARY_WID" && "$LAST_BUNDLE" == "$ACTIVE_BROWSER_BUNDLE" ]]; then
                    aerospace layout --window-id "$LATEST_BUNDLE_WID" floating 2>/dev/null || true
                    set_churn_window
                    aerospace focus --window-id "$LATEST_BUNDLE_WID" 2>/dev/null || true
                elif [[ "$LAST_BUNDLE" == "$ZEN" && "$ACTIVE_BROWSER_BUNDLE" != "$ZEN" ]]; then
                    hide_bundle_app "$ZEN"
                    hide_app "zen"
                elif [[ "$LAST_BUNDLE" == "$SAFARI" && "$ACTIVE_BROWSER_BUNDLE" != "$SAFARI" ]]; then
                    hide_bundle_app "$SAFARI"
                    hide_app "Safari"
                fi
                printf "%s" "$cur" > "$LAST_APPLIED_FILE"
                PERF_DECISION="secondary_window_skip"
                exit 0
            fi
        fi

        if ! is_utility_bundle "$LAST_BUNDLE" && [[ "$FOCUSED_APP" == "$LAST_BUNDLE" && -n "$PRIMARY_WID" && "$FOCUSED_WID" != "$PRIMARY_WID" && "$BROWSER_MAIN_INTENT" != "true" ]]; then
            log "on_window: transient popup for $LAST_BUNDLE, skipping rebuild"
            if [[ "$LAST_BUNDLE" == "$ACTIVE_BROWSER_BUNDLE" ]]; then
                aerospace layout --window-id "$FOCUSED_WID" floating 2>/dev/null || true
                set_churn_window
                aerospace focus --window-id "$FOCUSED_WID" 2>/dev/null || true
            elif [[ "$LAST_BUNDLE" == "$ZEN" ]]; then
                hide_bundle_app "$ZEN"
                hide_app "zen"
            elif [[ "$LAST_BUNDLE" == "$SAFARI" ]]; then
                hide_bundle_app "$SAFARI"
                hide_app "Safari"
            fi
            printf "%s" "$cur" > "$LAST_APPLIED_FILE"
            PERF_DECISION="transient_popup_skip"
            exit 0
        fi

        # Update state based on which app opened
        case "$LAST_BUNDLE" in
            "$ZEN")
                STATE_BROWSER="zen"
                if [[ -n "$LATEST_BUNDLE_WID" && -n "${STATE_TILED_ORDER:-}" ]]; then
                    STATE_TILED_ORDER="$(echo "$STATE_TILED_ORDER" | sed -E "s/[^,]+$/$LATEST_BUNDLE_WID/")"
                fi
                ;;
            "$SAFARI")
                STATE_BROWSER="safari"
                if [[ -n "$LATEST_BUNDLE_WID" && -n "${STATE_TILED_ORDER:-}" ]]; then
                    STATE_TILED_ORDER="$(echo "$STATE_TILED_ORDER" | sed -E "s/[^,]+$/$LATEST_BUNDLE_WID/")"
                fi
                ;;
            "$UPNOTE")
                STATE_UPNOTE_TILED="true"
                show_app "UpNote"
                ;;
            "$CODEX"|"$TERMINAL"|"$TELEGRAM")
                STATE_ACTIVE_UTILITY_BUNDLE="$RESOLVED_UTILITY_BUNDLE"
                STATE_ACTIVE_UTILITY_WID="$RESOLVED_UTILITY_WID"
                ;;
        esac

        # Persist state/snapshot and delegate layout to the standard ctrl+e path.
        write_state "$ws"
        printf "%s" "$cur" > "$LAST_APPLIED_FILE"
        set_churn_window
        PERF_DECISION="delegate_balance"
        release_lock
        trap 'cleanup_scheduler' EXIT
        log "on_window: delegating $LAST_BUNDLE layout to ctrl+e"
        sleep 0.05
        "$AEROSPACE_DIR/balance.sh" 2>/dev/null || true

        log "on_window: $LAST_BUNDLE handled"
        PERF_DECISION="handled"
        exit 0
    ) &
fi

# Exit immediately - scheduler handles the actual rebuild
perf_log_duration "on_window.dispatch" "$PERF_DISPATCH_START" "ws=$WS bundle=$BUNDLE result=$DISPATCH_RESULT"
exit 0
