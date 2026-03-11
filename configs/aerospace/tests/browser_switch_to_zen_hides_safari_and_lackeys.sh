#!/bin/bash
set -euo pipefail

STATE_FILE="/tmp/aerospace_state/w1.state"

open -a Safari >/dev/null 2>&1 || true
open -a "zen" >/dev/null 2>&1 || true
sleep 3

# Simulate contender browser launch/intent path.
/Users/jian/Dev/configs/aerospace/on_window.sh app.zen-browser.zen
for _ in {1..30}; do
    ACTIVE_BROWSER_NOW="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
    [[ "$ACTIVE_BROWSER_NOW" == "zen" ]] && break
    sleep 0.2
done
aerospace trigger-binding --mode main ctrl-e
sleep 2

ACTIVE_BROWSER=""
if [[ -f "$STATE_FILE" ]]; then
    ACTIVE_BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE")"
fi
if [[ "$ACTIVE_BROWSER" != "zen" ]]; then
    echo "FAIL: Expected active browser state to be zen, got '$ACTIVE_BROWSER'"
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
    echo "FAIL: Expected Zen to be the only tiled browser, got $TILED_BUNDLE"
    exit 1
fi

SAFARI_TILES="$(aerospace list-windows --workspace w1 --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' '$1=="com.apple.Safari" && $2 ~ /tiles/ { c++ } END { print c+0 }')"
if [[ "$SAFARI_TILES" -ne 0 ]]; then
    echo "FAIL: Safari still has tiled windows after Zen switch"
    exit 1
fi

SAFARI_VISIBLE="$(osascript -e 'tell application "System Events" to if exists process "Safari" then visible of process "Safari" else false' 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo false)"
if [[ "$SAFARI_VISIBLE" == "true" ]]; then
    echo "FAIL: Safari process is still visible after Zen took core browser slot"
    exit 1
fi

echo "PASS: Browser switch hid old browser and kept only Zen tiled."
