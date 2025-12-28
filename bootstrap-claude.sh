#!/bin/bash
# Bootstrap Claude Commands
# Thin wrapper that invokes the smart bootstrap from .claude/config/

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$SCRIPT_DIR/.claude"
BOOTSTRAP="$CLAUDE_DIR/config/bootstrap.sh"
CLAUDE_REPO="https://github.com/jkuang7/claude.git"

# First run: clone claude repo if .claude doesn't exist or isn't a git repo
if [ ! -d "$CLAUDE_DIR/.git" ]; then
    echo "First run - cloning Claude commands..."
    rm -rf "$CLAUDE_DIR"
    git clone "$CLAUDE_REPO" "$CLAUDE_DIR"
fi

# Run the smart bootstrap
exec "$BOOTSTRAP" "$@"
