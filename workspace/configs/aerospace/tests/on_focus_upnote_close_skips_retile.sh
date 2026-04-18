#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
MARKER="TEST_ON_FOCUS_UPNOTE_CLOSE_SKIP_$(date +%s)_$$"

echo "$MARKER" >> "$LOG_FILE"

open -a UpNote >/dev/null 2>&1 || true
sleep 4

osascript -e 'tell application "UpNote" to quit' >/dev/null 2>&1 || true
sleep 4

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"

if printf '%s\n' "$SEGMENT" | grep -q 'on_focus: UpNote closed in w1, rebalancing'; then
    echo "FAIL: UpNote close still triggered on_focus retile."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

echo "PASS: UpNote close no longer triggers on_focus retile."
