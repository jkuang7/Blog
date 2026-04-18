#!/bin/bash
set -euo pipefail

measure_windows() {
    swift - <<'SWIFT'
import CoreGraphics
import Foundation

let options = CGWindowListOption(arrayLiteral: .optionOnScreenOnly, .excludeDesktopElements)
let owners = Set(["Zen", "cmux", "Code", "Codex", "UpNote", "Safari", "Telegram"])
var rows: [[String: Any]] = []

if let info = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] {
  for win in info {
    let owner = win[kCGWindowOwnerName as String] as? String ?? ""
    guard owners.contains(owner) else { continue }
    let layer = win[kCGWindowLayer as String] as? Int ?? -1
    guard layer == 0 else { continue }
    let bounds = win[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let width = bounds["Width"] as? Int ?? 0
    let height = bounds["Height"] as? Int ?? 0
    let x = bounds["X"] as? Int ?? 0
    let y = bounds["Y"] as? Int ?? 0
    if height > 100 {
      rows.append(["owner": owner, "x": x, "y": y, "width": width, "height": height])
    }
  }
}

let data = try! JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
SWIFT
}

browser_window_id() {
    aerospace list-windows --workspace w1 --format '%{window-id}|%{app-bundle-id}|%{window-layout}' 2>/dev/null \
        | awk -F'|' '($2=="app.zen-browser.zen" || $2=="com.apple.Safari") && $3 ~ /tiles/ { print $1; exit }'
}

assert_widths_preserved() {
    local before_payload="$1"
    local after_payload="$2"
    local label="$3"
    BEFORE_PAYLOAD="$before_payload" AFTER_PAYLOAD="$after_payload" LABEL="$label" python3 - <<'PY'
import json
import os

before = sorted(json.loads(os.environ["BEFORE_PAYLOAD"]), key=lambda row: row["x"])
after = sorted(json.loads(os.environ["AFTER_PAYLOAD"]), key=lambda row: row["x"])
label = os.environ["LABEL"]

if len(before) != len(after):
    print(f"FAIL: {label}: tile count changed from {len(before)} to {len(after)}")
    raise SystemExit(1)

for index, (left, right) in enumerate(zip(before, after), start=1):
    delta = abs(left["width"] - right["width"])
    if delta > 60:
        print(f"FAIL: {label}: slot {index} width changed by {delta}px ({left['width']} -> {right['width']})")
        print("before:", before)
        print("after:", after)
        raise SystemExit(1)
PY
}

osascript -e 'tell application "UpNote" to quit' >/dev/null 2>&1 || true
sleep 2

aerospace trigger-binding --mode main ctrl-e
sleep 3

BROWSER_WID="$(browser_window_id)"
if [[ -z "$BROWSER_WID" ]]; then
    echo "FAIL: Could not find tiled browser window in 2-column case."
    exit 1
fi

aerospace resize --window-id "$BROWSER_WID" width 953
sleep 2

TWO_COL_BEFORE="$(measure_windows)"
aerospace trigger-binding --mode main ctrl-e
sleep 3
TWO_COL_AFTER="$(measure_windows)"
assert_widths_preserved "$TWO_COL_BEFORE" "$TWO_COL_AFTER" "2-column"

open -a UpNote >/dev/null 2>&1 || true
sleep 4

aerospace trigger-binding --mode main ctrl-e
sleep 4

BROWSER_WID="$(browser_window_id)"
if [[ -z "$BROWSER_WID" ]]; then
    echo "FAIL: Could not find tiled browser window in 3-column case."
    exit 1
fi

aerospace resize --window-id "$BROWSER_WID" width 1400
sleep 2

THREE_COL_BEFORE="$(measure_windows)"
aerospace trigger-binding --mode main ctrl-e
sleep 4
THREE_COL_AFTER="$(measure_windows)"
assert_widths_preserved "$THREE_COL_BEFORE" "$THREE_COL_AFTER" "3-column"

echo "PASS: ctrl+e preserves current tiled widths."
