#!/bin/bash
set -euo pipefail

STATE_FILE="/tmp/aerospace_state/w1.state"

# Start with Safari as active core browser.
open -a Safari >/dev/null 2>&1 || true
sleep 2
/Users/jian/Dev/configs/aerospace/on_window.sh com.apple.Safari
sleep 3

# Bring Zen to front (can be existing floating window) and run focus callback path.
open -a "zen" >/dev/null 2>&1 || true
sleep 2
osascript -e 'tell application "zen" to activate' >/dev/null 2>&1 || true
sleep 1
/Users/jian/Dev/configs/aerospace/on_focus.sh
sleep 3

ACTIVE_BROWSER=""
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE_BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE")"
fi
if [[ "$ACTIVE_BROWSER" != "zen" ]]; then
    echo "FAIL: Expected focus on Zen primary window to switch browser state to zen, got '$ACTIVE_BROWSER'"
    exit 1
fi

TILED_BROWSERS="$(aerospace list-windows --workspace w1 --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' '($1=="app.zen-browser.zen" || $1=="com.apple.Safari") && $2 ~ /tiles/ { print $1 }')"
TILED_COUNT="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$TILED_COUNT" -ne 1 ]]; then
    echo "FAIL: Expected exactly one tiled browser after focus switch, found $TILED_COUNT"
    printf '%s\n' "$TILED_BROWSERS"
    exit 1
fi

TILED_BUNDLE="$(printf '%s\n' "$TILED_BROWSERS" | sed '/^$/d' | head -n1)"
if [[ "$TILED_BUNDLE" != "app.zen-browser.zen" ]]; then
    echo "FAIL: Expected Zen to be tiled after focus switch, got $TILED_BUNDLE"
    exit 1
fi

echo "PASS: Focusing Zen primary window switched core browser and retiled."
