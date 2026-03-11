#!/bin/bash
set -euo pipefail

STATE_FILE="/tmp/aerospace_state/w1.state"

open -a "zen" >/dev/null 2>&1 || true
open -a Safari >/dev/null 2>&1 || true
sleep 3

# Force retile through standard path.
aerospace trigger-binding --mode main ctrl-e
sleep 4

ACTIVE_BROWSER=""
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE_BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE")"
fi

TILED_BROWSERS="$(aerospace list-windows --workspace w1 --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' '($1=="app.zen-browser.zen" || $1=="com.apple.Safari" || $1=="com.google.Chrome" || $1=="company.thebrowser.Browser" || $1=="com.brave.Browser" || $1=="org.mozilla.firefox") && $2 ~ /tiles/ { print $1 }')"

TILED_COUNT="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$TILED_COUNT" -ne 1 ]]; then
    echo "FAIL: Expected exactly one tiled browser, found $TILED_COUNT"
    printf '%s\n' "$TILED_BROWSERS"
    exit 1
fi

TILED_BUNDLE="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | head -n1)"
case "$ACTIVE_BROWSER" in
    zen)
        [[ "$TILED_BUNDLE" == "app.zen-browser.zen" ]] || {
            echo "FAIL: Active browser is zen but tiled browser is $TILED_BUNDLE"
            exit 1
        }
        ;;
    safari)
        [[ "$TILED_BUNDLE" == "com.apple.Safari" ]] || {
            echo "FAIL: Active browser is safari but tiled browser is $TILED_BUNDLE"
            exit 1
        }
        ;;
    *)
        echo "FAIL: Missing/unknown active browser state: '$ACTIVE_BROWSER'"
        exit 1
        ;;
esac

echo "PASS: Exactly one browser is tiled and matches active browser state ($ACTIVE_BROWSER)."
