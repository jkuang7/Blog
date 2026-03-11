#!/bin/bash
# gate.sh - single migration gate for shell callbacks + typed engine + parity

set -euo pipefail

AEROSPACE_DIR="/Users/jian/Dev/configs/aerospace"
ENGINE_DIR="$AEROSPACE_DIR/engine"
TEST_DIR="$AEROSPACE_DIR/tests"
GATE_LOG="/tmp/aerospace-gate.log"

run_with_retry() {
    local attempts="$1"
    shift
    local cmd=("$@")
    local attempt=1

    while (( attempt <= attempts )); do
        if "${cmd[@]}"; then
            return 0
        fi
        if (( attempt == attempts )); then
            return 1
        fi
        echo "retry($attempt/$attempts): ${cmd[*]}"
        sleep 1
        attempt=$((attempt + 1))
    done
}

{
    echo "=== GATE START $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "--- shell callback regression suite ---"
    for test_script in "$TEST_DIR"/*.sh; do
        echo "running $(basename "$test_script")"
        run_with_retry 2 bash "$test_script"
    done

    echo "--- typed engine gates ---"
    (
        cd "$ENGINE_DIR"
        npm run typecheck
        npm run test
    )

    echo "--- semantic parity replay ---"
    (
        cd "$ENGINE_DIR"
        npm run test:parity
    )
    echo "=== GATE PASS $(date '+%Y-%m-%d %H:%M:%S') ==="
} 2>&1 | tee -a "$GATE_LOG"
