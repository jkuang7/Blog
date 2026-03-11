#!/bin/bash
# on_focus.sh - mode-aware wrapper for on_focus callback

set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/engine_runtime.sh"
dispatch_callback "on_focus" "$@"
