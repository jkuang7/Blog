#!/bin/bash
set -euo pipefail

STATE_FILE="/tmp/aerospace_state/w1.state"

open -a "zen" >/dev/null 2>&1 || true
open -a Safari >/dev/null 2>&1 || true
sleep 3

# Simulate contender browser launch/intent path.
/Users/jian/Dev/configs/aerospace/on_window.sh com.apple.Safari
for _ in {1..30}; do
    ACTIVE_BROWSER_NOW="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
    [[ "$ACTIVE_BROWSER_NOW" == "safari" ]] && break
    sleep 0.2
done
aerospace trigger-binding --mode main ctrl-e
sleep 2

ACTIVE_BROWSER=""
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE_BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE")"
fi
if [[ "$ACTIVE_BROWSER" != "safari" ]]; then
    echo "FAIL: Expected active browser state to be safari, got '$ACTIVE_BROWSER'"
    exit 1
fi

TILED_BROWSERS="$(aerospace list-windows --workspace w1 --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' '($1=="app.zen-browser.zen" || $1=="com.apple.Safari") && $2 ~ /tiles/ { print $1 }')"
TILED_COUNT="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$TILED_COUNT" -ne 1 ]]; then
    echo "FAIL: Expected exactly one tiled browser after switch, found $TILED_COUNT"
    printf '%s\n' "$TILED_BROWSERS"
    exit 1
fi

TILED_BUNDLE="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | head -n1)"
if [[ "$TILED_BUNDLE" != "com.apple.Safari" ]]; then
    echo "FAIL: Expected Safari to be the only tiled browser, got $TILED_BUNDLE"
    exit 1
fi

ZEN_TILES="$(aerospace list-windows --workspace w1 --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' '$1=="app.zen-browser.zen" && $2 ~ /tiles/ { c++ } END { print c+0 }')"
if [[ "$ZEN_TILES" -ne 0 ]]; then
    echo "FAIL: Zen still has tiled windows after Safari switch"
    exit 1
fi

ZEN_VISIBLE="$(osascript -e 'tell application "System Events" to if exists process "zen" then visible of process "zen" else false' 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo false)"
if [[ "$ZEN_VISIBLE" == "true" ]]; then
    echo "FAIL: Zen process is still visible after Safari took core browser slot"
    exit 1
fi

echo "PASS: Browser switch hid old browser and kept only Safari tiled."
