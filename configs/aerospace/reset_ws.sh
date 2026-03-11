#!/bin/bash
# reset_ws.sh - mode-aware wrapper for reset_ws callback

set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/engine_runtime.sh"
dispatch_callback "reset_ws" "$@"
