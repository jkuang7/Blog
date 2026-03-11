#!/bin/bash
set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/lib.sh"

TEST_STATE_DIR="/tmp/aerospace_state_migration_test_$$"
rm -rf "$TEST_STATE_DIR"
mkdir -p "$TEST_STATE_DIR"
STATE_DIR="$TEST_STATE_DIR"
LOG_FILE="$TEST_STATE_DIR/test.log"

# Default dual-write path.
STATE_BROWSER="safari"
STATE_UPNOTE_TILED="false"
STATE_TILED_ORDER="11,22,33"
write_state "w1"

[[ -f "$STATE_DIR/w1.state" ]] || { echo "FAIL: missing legacy state"; exit 1; }
[[ -f "$STATE_DIR/w1.state.v2.json" ]] || { echo "FAIL: missing v2 state"; exit 1; }

read_state "w1"
[[ "$STATE_BROWSER" == "safari" ]] || { echo "FAIL: expected safari from dual-read"; exit 1; }
[[ "$STATE_UPNOTE_TILED" == "false" ]] || { echo "FAIL: expected upnote false from dual-read"; exit 1; }
[[ "$STATE_TILED_ORDER" == "11,22,33" ]] || { echo "FAIL: expected tiled order from dual-read"; exit 1; }

# v2-only read should work when legacy file is absent.
rm -f "$STATE_DIR/w1.state"
read_state "w1"
[[ "$STATE_BROWSER" == "safari" ]] || { echo "FAIL: expected v2 fallback read"; exit 1; }

# Invalid v2 should fall back to legacy.
cat > "$STATE_DIR/w1.state.v2.json" <<'EOF'
{"version":999}
EOF
cat > "$STATE_DIR/w1.state" <<'EOF'
BROWSER=zen
UPNOTE_TILED=true
TILED_ORDER=4,5,6
EOF
read_state "w1"
[[ "$STATE_BROWSER" == "zen" ]] || { echo "FAIL: expected legacy fallback browser"; exit 1; }
[[ "$STATE_UPNOTE_TILED" == "true" ]] || { echo "FAIL: expected legacy fallback upnote"; exit 1; }
[[ "$STATE_TILED_ORDER" == "4,5,6" ]] || { echo "FAIL: expected legacy fallback tiled order"; exit 1; }

# write mode controls.
STATE_BROWSER="safari"
STATE_UPNOTE_TILED="true"
STATE_TILED_ORDER="1,2"
AEROSPACE_STATE_WRITE_MODE="legacy-only" write_state "w1"
[[ -f "$STATE_DIR/w1.state" ]] || { echo "FAIL: legacy-only must write legacy"; exit 1; }

rm -f "$STATE_DIR/w1.state.v2.json"
AEROSPACE_STATE_WRITE_MODE="v2-only" write_state "w1"
[[ -f "$STATE_DIR/w1.state.v2.json" ]] || { echo "FAIL: v2-only must write v2"; exit 1; }

echo "PASS: state migration dual read/write behavior"
