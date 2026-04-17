#!/bin/bash
set -euo pipefail

source "/Users/jian/Dev/workspace/configs/aerospace/config.sh"

STATE_FILE="/tmp/aerospace_state/w1.state"

ACTIVE_UTILITY_WID="$(awk -F= '/^ACTIVE_UTILITY_WID=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
ACTIVE_UTILITY_BUNDLE="$(awk -F= '/^ACTIVE_UTILITY_BUNDLE=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"

if [[ -z "$ACTIVE_UTILITY_WID" || "$ACTIVE_UTILITY_BUNDLE" != "$TERMINAL" ]]; then
    echo "FAIL: Expected a Terminal utility owner in $STATE_FILE"
    exit 1
fi

OTHER_TERMINAL_WID="$(aerospace list-windows --workspace w1 --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
    | awk -F'|' -v owner="$ACTIVE_UTILITY_WID" -v terminal="$TERMINAL" '$2==terminal && $1!=owner { print $1; exit }')"

if [[ -z "$OTHER_TERMINAL_WID" ]]; then
    echo "FAIL: Need at least one non-owner Terminal window to validate drift repair"
    exit 1
fi

aerospace layout --window-id "$ACTIVE_UTILITY_WID" floating 2>/dev/null || true
sleep 0.5
aerospace focus --window-id "$OTHER_TERMINAL_WID" 2>/dev/null || true
sleep 0.5
/Users/jian/Dev/workspace/configs/aerospace/on_focus.sh
sleep 2

OWNER_LAYOUT="$(aerospace list-windows --workspace w1 --format '%{window-id}|%{window-layout}' 2>/dev/null \
    | awk -F'|' -v owner="$ACTIVE_UTILITY_WID" '$1==owner { print $2; exit }')"

if [[ "$OWNER_LAYOUT" != *tiles* ]]; then
    echo "FAIL: Expected Terminal owner $ACTIVE_UTILITY_WID to retile after drift, got layout '$OWNER_LAYOUT'"
    exit 1
fi

echo "PASS: Terminal utility owner retiles after drift even when another Terminal window is focused."
