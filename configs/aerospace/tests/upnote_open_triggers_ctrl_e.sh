#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
MARKER="TEST_UPNOTE_CTRL_E_$(date +%s)_$$"

echo "$MARKER" >> "$LOG_FILE"

osascript -e 'tell application "UpNote" to quit' >/dev/null 2>&1 || true
sleep 1
open -a UpNote
sleep 6

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"

OPEN_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: com.getupnote.desktop opened in w1 (snapshot changed)" | head -n1 | cut -d: -f1 || true)"
DELEGATE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: delegating com.getupnote.desktop layout to ctrl+e" | head -n1 | cut -d: -f1 || true)"
BALANCE_START_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "balance: rebalancing w1" | head -n1 | cut -d: -f1 || true)"
BALANCE_DONE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "balance: w1 balanced" | head -n1 | cut -d: -f1 || true)"

if [[ -z "$OPEN_LINE" || -z "$DELEGATE_LINE" || -z "$BALANCE_START_LINE" || -z "$BALANCE_DONE_LINE" ]]; then
    echo "FAIL: Missing expected log lines."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

if (( DELEGATE_LINE > BALANCE_START_LINE )); then
    echo "FAIL: balance started before UpNote delegated to ctrl+e."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

echo "PASS: UpNote open delegated to ctrl+e and completed balance."
