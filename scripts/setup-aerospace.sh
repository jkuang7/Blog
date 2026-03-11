#!/bin/bash
# AeroSpace Setup Script
# Intelligent, idempotent setup for AeroSpace-only window management.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_DIR="$DEV_DIR/configs/aerospace"
AEROSPACE_CONFIG="$CONFIG_DIR/aerospace.toml"
HOME_CONFIG_DIR="$HOME/.config/aerospace"
ENGINE_DIR="$CONFIG_DIR/engine"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}→${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "AeroSpace Setup"
echo "==============="
echo ""

if [ ! -f "$AEROSPACE_CONFIG" ]; then
    log_error "AeroSpace config not found: $AEROSPACE_CONFIG"
    exit 1
fi
log_info "AeroSpace config found"

echo ""
echo "Phase 1: Installation"
echo "---------------------"

if ! command -v brew >/dev/null 2>&1; then
    log_error "Homebrew not installed"
    echo "  Install Homebrew first: https://brew.sh"
    exit 1
fi
log_info "Homebrew installed"

if ! command -v aerospace >/dev/null 2>&1; then
    log_warn "Installing AeroSpace..."
    brew install --cask nikitabobko/tap/aerospace
    log_info "AeroSpace installed"
else
    log_info "AeroSpace already installed"
fi

echo ""
echo "Phase 2: Launch and Permissions"
echo "-------------------------------"

if ! pgrep -x "AeroSpace" >/dev/null; then
    log_warn "Opening AeroSpace.app..."
    open -a "AeroSpace"
    sleep 2
    log_info "AeroSpace launched"
else
    log_info "AeroSpace already running"
fi

if aerospace list-apps >/dev/null 2>&1; then
    log_info "AeroSpace accessibility granted"
else
    log_warn "AeroSpace needs Accessibility permissions"
    echo "  System Settings -> Privacy & Security -> Accessibility -> Enable AeroSpace"
fi

echo ""
echo "Phase 3: Symlinks and Reload"
echo "----------------------------"

chmod +x "$CONFIG_DIR/"*.sh 2>/dev/null || true
log_info "AeroSpace scripts are executable"

mkdir -p "$HOME_CONFIG_DIR"

for script_name in balance.sh config.sh lib.sh move_to_focused.sh on_focus.sh on_window.sh reset_ws.sh switch_ws.sh video_mode.sh; do
    target="$HOME_CONFIG_DIR/$script_name"
    source_path="$CONFIG_DIR/$script_name"
    if [ -L "$target" ]; then
        current_target="$(readlink "$target" || true)"
        if [ "$current_target" != "$source_path" ]; then
            ln -sfn "$source_path" "$target"
            log_info "Updated $target symlink"
        fi
    elif [ -e "$target" ]; then
        backup="$target.bak.$(date +%Y%m%d%H%M%S)"
        mv "$target" "$backup"
        ln -s "$source_path" "$target"
        log_warn "Backed up existing $target to $backup"
    else
        ln -s "$source_path" "$target"
        log_info "Created $target symlink"
    fi
done

for stale_name in center_codex.sh tile_codex_center.sh fullscreen_fix.sh; do
    stale_path="$HOME_CONFIG_DIR/$stale_name"
    if [ -L "$stale_path" ] || [ -f "$stale_path" ]; then
        rm -f "$stale_path"
        log_info "Removed stale $stale_path"
    fi
done

if [ ! -d "$ENGINE_DIR/node_modules" ]; then
    log_warn "Installing AeroSpace engine dependencies..."
    (cd "$ENGINE_DIR" && npm install)
    log_info "AeroSpace engine dependencies installed"
fi

if [ -L ~/.aerospace.toml ]; then
    current_target="$(readlink ~/.aerospace.toml || true)"
    if [ "$current_target" != "$AEROSPACE_CONFIG" ]; then
        ln -sfn "$AEROSPACE_CONFIG" ~/.aerospace.toml
        log_info "Updated ~/.aerospace.toml symlink"
    else
        log_info "~/.aerospace.toml symlink already correct"
    fi
elif [ -f ~/.aerospace.toml ]; then
    backup="$HOME/.aerospace.toml.bak.$(date +%Y%m%d%H%M%S)"
    mv ~/.aerospace.toml "$backup"
    ln -s "$AEROSPACE_CONFIG" ~/.aerospace.toml
    log_warn "Backed up existing ~/.aerospace.toml to $backup"
    log_info "Created ~/.aerospace.toml symlink"
else
    ln -s "$AEROSPACE_CONFIG" ~/.aerospace.toml
    log_info "Created ~/.aerospace.toml symlink"
fi

aerospace reload-config >/dev/null 2>&1 || true
log_info "AeroSpace config reloaded"

echo ""
echo "================================"
log_info "AeroSpace setup complete!"
echo "================================"
echo ""
echo "AeroSpace Hotkeys:"
echo "  ctrl-1    : Switch to w1"
echo "  ctrl-w    : Reset to defaults"
echo "  ctrl-e    : Rebalance columns"
echo "  ctrl-f    : Toggle fullscreen"
echo "  ctrl-shift-v : Toggle video mode"
echo ""
echo "Config:"
echo "  AeroSpace: $AEROSPACE_CONFIG"
echo "  Hooks:     $HOME_CONFIG_DIR"
echo ""
