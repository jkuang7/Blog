#!/bin/bash
# config.sh - Centralized configuration for AeroSpace window management
# Source this file from all aerospace scripts

# === Home App Bundle IDs ===
export VSCODE="com.microsoft.VSCode"
export CODEX="com.openai.codex"
export TERMINAL="com.apple.Terminal"
export TELEGRAM="com.tdesktop.Telegram"
export ZEN="app.zen-browser.zen"
export SAFARI="com.apple.Safari"
export UPNOTE="com.getupnote.desktop"

# === State Directory ===
export STATE_DIR="/tmp/aerospace_state"
export LOG_FILE="/tmp/aerospace.log"

# === Slot Sizes (percentages) ===
# At most 3 tiled columns are allowed. Everything else floats.
export SLOT2_LEFT_PCT=45
export SLOT2_RIGHT_PCT=55

# 3-column layout when UpNote owns the left anchor.
export SLOT3_UPNOTE_LEFT_PCT=22
export SLOT3_UPNOTE_MIDDLE_PCT=33
export SLOT3_UPNOTE_RIGHT_PCT=45

# 3-column layout without UpNote in the left slot.
export SLOT3_STANDARD_LEFT_PCT=30
export SLOT3_STANDARD_MIDDLE_PCT=35
export SLOT3_STANDARD_RIGHT_PCT=35

# === Workspace Defaults ===
# w1: Reference mode - UpNote always visible
w1_default_browser="zen"
w1_default_upnote="true"

# === Utility Functions ===

get_screen_width() {
    local width
    width=$(displayplacer list 2>/dev/null | grep "^Resolution:" | head -1 | grep -o '[0-9]*' | head -1)
    echo "${width:-3440}"
}

log() {
    echo "$(date '+%H:%M:%S'): $*" >> "$LOG_FILE"
}

# Initialize state directory
mkdir -p "$STATE_DIR"
