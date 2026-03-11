#!/bin/bash
set -euo pipefail

AEROSPACE_FIX="/Users/jian/Dev/configs/aerospace/fullscreen_fix.sh"
SYMLINK_FIX="/Users/jian/.config/aerospace/fullscreen_fix.sh"
REPO_HS_CONFIG="/Users/jian/Dev/.opencode/config/hammerspoon/init.lua"
SETUP_SCRIPT="/Users/jian/Dev/scripts/setup-aerospace.sh"
fail() {
    echo "FAIL: $1"
    exit 1
}

if [[ -e "$AEROSPACE_FIX" ]]; then
    fail "fullscreen fix script still exists: $AEROSPACE_FIX"
fi

if [[ -e "$SYMLINK_FIX" || -L "$SYMLINK_FIX" ]]; then
    fail "fullscreen fix symlink still exists: $SYMLINK_FIX"
fi

if [[ -e "$REPO_HS_CONFIG" ]]; then
    fail "legacy repo hammerspoon config still exists: $REPO_HS_CONFIG"
fi

if rg -n "hammerspoon|Hammerspoon|hs\\." "$SETUP_SCRIPT" >/dev/null 2>&1; then
    fail "setup script still contains hammerspoon dependency text: $SETUP_SCRIPT"
fi

echo "PASS: Fullscreen auto-retile path is disabled and hammerspoon dependency removed."
