#!/bin/bash
# config.sh - Centralized configuration for AeroSpace window management
# Source this file from all aerospace scripts

# === Home App Bundle IDs ===
export VSCODE="com.microsoft.VSCode"
export CODEX="com.openai.codex"
export ZEN="app.zen-browser.zen"
export SAFARI="com.apple.Safari"
export UPNOTE="com.getupnote.desktop"

# === State Directory ===
export STATE_DIR="/tmp/aerospace_state"
export LOG_FILE="/tmp/aerospace.log"

# === Column Sizes (percentages) ===
# 2-column layout (w1 without UpNote)
export COL2_VSCODE_PCT=45
export COL2_BROWSER_PCT=55

# 3-column layout (w1 with UpNote, or w2)
export COL3_UPNOTE_PCT=22
export COL3_VSCODE_PCT=33
export COL3_CODEX_PCT=33
export COL3_BROWSER_PCT=45

# 3-column layout (w1 without UpNote, with VSCode + Codex + Browser)
export COL3_VSCODE_CODEX_VSCODE_PCT=30
export COL3_VSCODE_CODEX_CODEX_PCT=35
export COL3_VSCODE_CODEX_BROWSER_PCT=35

# 4-column layout (UpNote + VSCode + Codex + Browser)
# Calibrated from current live layout.
export COL4_UPNOTE_PCT=25
export COL4_VSCODE_PCT=25
export COL4_CODEX_PCT=24
export COL4_BROWSER_PCT=26

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
