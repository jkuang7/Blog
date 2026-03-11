#!/bin/bash
# callbacks/: shell side-effect implementation invoked by engine wrapper.
# balance.sh - Rebalance column sizing without changing state
# Usage: balance.sh
# Keybinding: ctrl+e

set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/lib.sh"

# Get current workspace
WS=$(aerospace list-workspaces --focused 2>/dev/null | head -n1)
WS=$(normalize_ws "$WS")

# Only handle w1
is_home_ws "$WS" || exit 0

# Acquire lock
acquire_lock || exit 0

log "balance: rebalancing $WS"

# Load existing state (preserve user choices)
read_state "$WS"

# Rebuild with existing state (force to fix column order + sizing)
rebuild_workspace "$WS" force

log "balance: $WS balanced"
