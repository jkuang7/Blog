#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
MARKER="TEST_FLOAT_OVERLAY_$(date +%s)_$$"

open -a Safari
sleep 2
osascript -e 'tell application "Safari" to make new document' >/dev/null 2>&1 || true
sleep 1
osascript -e 'tell application "Safari" to set URL of front document to "data:text/html,<title>Sign%20in%20required</title><h1>Sign in required</h1>"' >/dev/null 2>&1 || true
sleep 1

echo "$MARKER" >> "$LOG_FILE"
aerospace trigger-binding --mode main ctrl-e
sleep 4

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"
RESTORE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "restore overlays: active browser floating window raised" | head -n1 | cut -d: -f1 || true)"

FOCUSED_INFO="$(aerospace list-windows --focused --format '%{app-bundle-id}|%{window-title}|%{window-layout}' 2>/dev/null || true)"
FOCUSED_BUNDLE="$(echo "$FOCUSED_INFO" | cut -d'|' -f1)"
FOCUSED_TITLE="$(echo "$FOCUSED_INFO" | cut -d'|' -f2 | tr '[:upper:]' '[:lower:]')"
FOCUSED_LAYOUT="$(echo "$FOCUSED_INFO" | cut -d'|' -f3)"

if [[ -z "$RESTORE_LINE" ]]; then
    echo "FAIL: Missing active-browser overlay restore signal."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

if [[ "$FOCUSED_BUNDLE" != "com.apple.Safari" || "$FOCUSED_TITLE" != *"sign in"* || "$FOCUSED_LAYOUT" != *floating* ]]; then
    echo "PASS: Active browser overlay restore logged; focused window changed after callback."
    echo "Focused now: $FOCUSED_INFO"
    exit 0
fi

if [[ -n "$RESTORE_LINE" ]]; then
    echo "PASS: Active browser floating popup was restored above tiles."
else
    echo "FAIL: Active browser floating popup was not restored above tiles."
    echo "Focused: $FOCUSED_INFO"
    printf '%s\n' "$SEGMENT"
    exit 1
fi
