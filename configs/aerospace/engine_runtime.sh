#!/bin/bash
# engine_runtime.sh - callback wrapper runtime mode dispatcher

set -euo pipefail

AEROSPACE_DIR="/Users/jian/Dev/configs/aerospace"
source "$AEROSPACE_DIR/config.sh"

normalize_engine_mode() {
    local mode="${AEROSPACE_ENGINE_MODE:-ts-active}"
    case "$mode" in
        ts-shadow|ts-active)
            echo "$mode"
            ;;
        *)
            log "engine_mode: unknown mode '$mode', defaulting to ts-active"
            echo "ts-active"
            ;;
    esac
}

dispatch_callback() {
    local callback="$1"
    shift || true

    local mode
    mode="$(normalize_engine_mode)"
    local callback_script="$AEROSPACE_DIR/callbacks/${callback}.sh"

    if [[ ! -x "$callback_script" ]]; then
        log "engine_mode: missing callback script $callback_script"
        exit 1
    fi

    case "$mode" in
        ts-shadow)
            "$AEROSPACE_DIR/engine_callback.sh" "$mode" "$callback" "$@" || true
            exec "$callback_script" "$@"
            ;;
        ts-active)
            if "$AEROSPACE_DIR/engine_callback.sh" "$mode" "$callback" "$@"; then
                exit 0
            fi
            log "engine_mode: callback=$callback mode=$mode fell back to callbacks implementation"
            exec "$callback_script" "$@"
            ;;
    esac
}
