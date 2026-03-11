#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
STATE_FILE="/tmp/aerospace_state/w1.state"
STATE_FILE_V2="/tmp/aerospace_state/w1.state.v2.json"
LAST_APPLIED_FILE="/tmp/aerospace_state/last_applied_snapshot_w1"
MARKER="TEST_UPNOTE_SNAPSHOT_UNCHANGED_$(date +%s)_$$"

open -a UpNote >/dev/null 2>&1 || true
sleep 2

# Build snapshot exactly like on_window.sh snapshot_windows().
SNAPSHOT="$(aerospace list-windows --workspace w1 --format '%{app-name}|%{window-id}' 2>/dev/null \
    | grep -E '^(Zen|Safari|Code|Codex|UpNote)\|[0-9]+$' \
    | awk -F'|' '
        {
            app=$1
            wid=$2+0
            if (!(app in min) || wid < min[app]) {
                min[app]=wid
            }
        }
        END {
            for (app in min) print app "|" min[app]
        }
    ' | sort)"
printf '%s' "$SNAPSHOT" > "$LAST_APPLIED_FILE"

# Force state to untiled UpNote while keeping browser/order intact.
BROWSER="zen"
TILED_ORDER=""
if [[ -f "$STATE_FILE_V2" ]]; then
    BROWSER="$(python3 - "$STATE_FILE_V2" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
browser = data.get("browser", "zen")
if browser not in {"zen", "safari", ""}:
    browser = "zen"
print(browser)
PY
)"
    TILED_ORDER="$(python3 - "$STATE_FILE_V2" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
numbers = []
for value in data.get("tiledOrder", []):
    try:
        number = int(value)
    except (TypeError, ValueError):
        continue
    if number >= 0:
        numbers.append(str(number))
print(",".join(numbers))
PY
)"
elif [[ -f "$STATE_FILE" ]]; then
    BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || echo zen)"
    TILED_ORDER="$(awk -F= '/^TILED_ORDER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
fi
cat > "$STATE_FILE" <<STATE
BROWSER=${BROWSER}
UPNOTE_TILED=false
TILED_ORDER=${TILED_ORDER}
STATE

python3 - "$STATE_FILE_V2" "$BROWSER" "$TILED_ORDER" <<'PY'
import json
import sys
import time

path = sys.argv[1]
browser = sys.argv[2]
tiled_order_raw = sys.argv[3]
tiled_order = []
for value in tiled_order_raw.split(","):
    value = value.strip()
    if value.isdigit():
        tiled_order.append(int(value))

payload = {
    "version": 2,
    "workspace": "w1",
    "browser": browser if browser in {"zen", "safari", ""} else "zen",
    "upnoteTiled": False,
    "tiledOrder": tiled_order,
    "updatedAtMs": int(time.time() * 1000),
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

echo "$MARKER" >> "$LOG_FILE"
/Users/jian/Dev/configs/aerospace/on_window.sh com.getupnote.desktop
sleep 5

SEGMENT="$(awk -v m="$MARKER" 'f{print} $0==m{f=1}' "$LOG_FILE")"
ALLOW_LINE="$(printf '%s\n' "$SEGMENT" | grep -n 'snapshot unchanged but UpNote untiled, allowing rebuild' | head -n1 | cut -d: -f1 || true)"
DELEGATE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n 'on_window: delegating com.getupnote.desktop layout to ctrl+e' | head -n1 | cut -d: -f1 || true)"
BALANCE_DONE_LINE="$(printf '%s\n' "$SEGMENT" | grep -n 'balance: w1 balanced' | head -n1 | cut -d: -f1 || true)"

if [[ -z "$ALLOW_LINE" || -z "$DELEGATE_LINE" || -z "$BALANCE_DONE_LINE" ]]; then
    echo "FAIL: Missing expected rebuild/delegate signals for snapshot-unchanged UpNote reopen."
    printf '%s\n' "$SEGMENT"
    exit 1
fi

UPNOTE_STATE="$(python3 - "$STATE_FILE_V2" "$STATE_FILE" <<'PY'
import json
import pathlib
import sys

v2_path = pathlib.Path(sys.argv[1])
legacy_path = pathlib.Path(sys.argv[2])

if v2_path.exists():
    with v2_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    print("true" if bool(data.get("upnoteTiled", False)) else "false")
elif legacy_path.exists():
    for line in legacy_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("UPNOTE_TILED="):
            print(line.split("=", 1)[1].strip())
            break
else:
    print("")
PY
)"
if [[ "$UPNOTE_STATE" != "true" ]]; then
    echo "FAIL: Expected UPNOTE_TILED=true after UpNote reopen rebuild, got '$UPNOTE_STATE'"
    exit 1
fi

echo "PASS: Snapshot-unchanged UpNote reopen still retiles into core flow."
