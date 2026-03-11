#!/bin/bash
set -euo pipefail

STATE_FILE="/tmp/aerospace_state/w1.state"

# Start on Safari and create a floating Safari popup overlay.
open -a Safari >/dev/null 2>&1 || true
sleep 2
osascript -e 'tell application "Safari" to make new document' >/dev/null 2>&1 || true
sleep 1
osascript -e 'tell application "Safari" to set URL of front document to "data:text/html,<title>Sign%20in%20required</title><h1>Sign in required</h1>"' >/dev/null 2>&1 || true
sleep 1
/Users/jian/Dev/configs/aerospace/on_window.sh com.apple.Safari
sleep 3

# Open contender core browser (Zen) and switch.
open -a "zen" >/dev/null 2>&1 || true
sleep 2
/Users/jian/Dev/configs/aerospace/on_window.sh app.zen-browser.zen
sleep 4
aerospace trigger-binding --mode main ctrl-e
sleep 2

ACTIVE_BROWSER=""
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE_BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE")"
fi
if [[ "$ACTIVE_BROWSER" != "zen" ]]; then
    echo "FAIL: Expected active browser state zen after switch, got '$ACTIVE_BROWSER'"
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
if [[ "$TILED_BUNDLE" != "app.zen-browser.zen" ]]; then
    echo "FAIL: Expected Zen as only tiled browser after switch, got $TILED_BUNDLE"
    exit 1
fi

SAFARI_VISIBLE="$(osascript -e 'tell application "System Events" to if exists process "Safari" then visible of process "Safari" else false' 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo false)"
if [[ "$SAFARI_VISIBLE" == "true" ]]; then
    echo "FAIL: Old competing browser (Safari) still visible; its overlays should stay hidden."
    exit 1
fi

FOCUSED_BUNDLE="$(aerospace list-windows --focused --format '%{app-bundle-id}' 2>/dev/null || true)"
if [[ "$FOCUSED_BUNDLE" == "com.apple.Safari" ]]; then
    echo "FAIL: Focus returned to old competing browser overlay."
    exit 1
fi

echo "PASS: New core browser switch ignored old competing browser overlays."
