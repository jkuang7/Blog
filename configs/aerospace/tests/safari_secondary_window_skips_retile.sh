#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
MARKER="TEST_SAFARI_SECONDARY_$(date +%s)_$$"

# Ensure Safari is running with a primary window first.
open -a Safari
sleep 2
# Ensure Safari is already the active core browser before testing secondary-window behavior.
/Users/jian/Dev/configs/aerospace/on_window.sh com.apple.Safari
sleep 2

echo "$MARKER" >> "$LOG_FILE"

# Open a second Safari window (common popup-like secondary window path).
osascript -e 'tell application "Safari" to make new document' >/dev/null 2>&1 || true
sleep 1
osascript -e 'tell application "Safari" to set URL of front document to "data:text/html,<title>Sign%20in%20required</title><h1>Sign in required</h1>"' >/dev/null 2>&1 || true
sleep 1

# Drive the same callback code path deterministically.
/Users/jian/Dev/configs/aerospace/on_window.sh com.apple.Safari
sleep 3

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"

SKIP_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: secondary window for tiled com.apple.Safari, skipping rebuild" | head -n1 | cut -d: -f1 || true)"
POPUP_SKIP_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: transient popup for com.apple.Safari, skipping rebuild" | head -n1 | cut -d: -f1 || true)"
FLOATING_SKIP_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: floating-intent window for com.apple.Safari, skipping rebuild" | head -n1 | cut -d: -f1 || true)"
SNAPSHOT_SKIP_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: snapshot unchanged, skipping rebuild" | head -n1 | cut -d: -f1 || true)"
BROWSER_SNAPSHOT_SKIP_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "on_window: snapshot unchanged for browser, skipping rebuild" | head -n1 | cut -d: -f1 || true)"
BALANCE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n "balance: rebalancing w1" | head -n1 | cut -d: -f1 || true)"

if [[ -z "$SKIP_LINE" && -z "$POPUP_SKIP_LINE" && -z "$FLOATING_SKIP_LINE" && -z "$SNAPSHOT_SKIP_LINE" && -z "$BROWSER_SNAPSHOT_SKIP_LINE" ]]; then
    echo "FAIL: Missing no-retile skip signal for Safari secondary window."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

if [[ -n "$BALANCE_LINE" ]]; then
    echo "FAIL: Unexpected rebalance for Safari secondary window."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

echo "PASS: Safari secondary window skipped retile/balance."
