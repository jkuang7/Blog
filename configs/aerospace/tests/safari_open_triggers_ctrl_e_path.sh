#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
MARKER="TEST_SAFARI_CTRL_E_$(date +%s)_$$"

echo "$MARKER" >> "$LOG_FILE"

osascript -e 'tell application "Safari" to quit' >/dev/null 2>&1 || true
sleep 1
open -a Safari
sleep 7

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"

OPEN_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: com.apple.Safari opened in w1 (snapshot changed)" | head -n1 | cut -d: -f1 || true)"
DELEGATE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: delegating com.apple.Safari layout to ctrl+e" | head -n1 | cut -d: -f1 || true)"
BALANCE_START_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "balance: rebalancing w1" | head -n1 | cut -d: -f1 || true)"
BALANCE_DONE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "balance: w1 balanced" | head -n1 | cut -d: -f1 || true)"

if [[ -z "$OPEN_LINE" || -z "$DELEGATE_LINE" || -z "$BALANCE_START_LINE" || -z "$BALANCE_DONE_LINE" ]]; then
    echo "FAIL: Missing expected Safari delegate/balance lines."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

if (( DELEGATE_LINE > BALANCE_START_LINE )); then
    echo "FAIL: balance started before Safari delegate log."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

echo "PASS: Safari open delegated to ctrl+e path and completed balance."
