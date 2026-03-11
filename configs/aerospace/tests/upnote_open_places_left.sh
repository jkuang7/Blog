#!/bin/bash
set -euo pipefail

LOG_FILE="/tmp/aerospace.log"
STATE_FILE="/tmp/aerospace_state/w1.state"
MARKER="TEST_UPNOTE_LEFT_$(date +%s)_$$"

echo "$MARKER" >> "$LOG_FILE"

osascript -e 'tell application "UpNote" to quit' >/dev/null 2>&1 || true
sleep 1
open -a UpNote
sleep 6

UPNOTE_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null \
    | awk -F'|' '$2=="com.getupnote.desktop" && $3=="UpNote"{print $1; exit}')"
CODE_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
    | awk -F'|' '$2=="com.microsoft.VSCode"{print $1; exit}')"
CODEX_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
    | awk -F'|' '$2=="com.openai.codex"{print $1; exit}')"

BROWSER="zen"
if [[ -f "$STATE_FILE" ]]; then
    BROWSER="$(awk -F= '/^BROWSER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || echo "zen")"
fi
if [[ "$BROWSER" == "safari" ]]; then
    BROWSER_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | awk -F'|' '$2=="com.apple.Safari"{print $1; exit}')"
else
    BROWSER_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | awk -F'|' '$2=="app.zen-browser.zen"{print $1; exit}')"
fi
if [[ -z "$BROWSER_WID" ]]; then
    BROWSER_WID="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | awk -F'|' '$2=="com.apple.Safari" || $2=="app.zen-browser.zen"{print $1; exit}')"
fi
UPNOTE_TILED_STATE="$(awk -F= '/^UPNOTE_TILED=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
TILED_ORDER="$(awk -F= '/^TILED_ORDER=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || true)"
if [[ -n "$TILED_ORDER" ]]; then
    FIRST_TILED_WID="${TILED_ORDER%%,*}"
    LAST_TILED_WID="${TILED_ORDER##*,}"
    if [[ "$UPNOTE_TILED_STATE" == "true" && -n "$FIRST_TILED_WID" ]]; then
        FIRST_TILED_BUNDLE="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
            | awk -F'|' -v wid="$FIRST_TILED_WID" '$1==wid {print $2; exit}')"
        if [[ "$FIRST_TILED_BUNDLE" == "com.getupnote.desktop" ]]; then
            UPNOTE_WID="$FIRST_TILED_WID"
        fi
    fi
    LAST_TILED_BUNDLE="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | awk -F'|' -v wid="$LAST_TILED_WID" '$1==wid {print $2; exit}')"
    if [[ "$BROWSER" == "safari" && "$LAST_TILED_BUNDLE" == "com.apple.Safari" ]]; then
        BROWSER_WID="$LAST_TILED_WID"
    elif [[ "$BROWSER" == "zen" && "$LAST_TILED_BUNDLE" == "app.zen-browser.zen" ]]; then
        BROWSER_WID="$LAST_TILED_WID"
    fi
fi

POS_LINES="$(swift -e '
import CoreGraphics
import Foundation
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let arr = (CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]]) ?? []
let ids = Array(CommandLine.arguments.dropFirst()).compactMap { Int($0) }
for targetId in ids {
  var minX: Int? = nil
  for w in arr {
    let num = w[kCGWindowNumber as String] as? NSNumber
    if num?.intValue == targetId {
      let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
      if let n = b["X"] as? NSNumber {
        minX = min(minX ?? n.intValue, n.intValue)
      } else if let n = b["X"] as? Int {
        minX = min(minX ?? n, n)
      }
    }
  }
  if let x = minX {
    print("\(targetId)|\(x)")
  }
}
' "${UPNOTE_WID:-0}" "${CODE_WID:-0}" "${CODEX_WID:-0}" "${BROWSER_WID:-0}")"

get_x() {
    local wid="$1"
    echo "$POS_LINES" | awk -F'|' -v w="$wid" '$1==w{print $2; exit}'
}

UPNOTE_X="$(get_x "$UPNOTE_WID")"
CODE_X="$(get_x "$CODE_WID")"
CODEX_X="$(get_x "$CODEX_WID")"
BROWSER_X="$(get_x "$BROWSER_WID")"

if [[ -z "$UPNOTE_X" || -z "$CODE_X" ]]; then
    echo "FAIL: Missing UpNote or Code position."
    echo "$POS_LINES"
    exit 1
fi

if [[ "$BROWSER" == "safari" ]]; then
    BROWSER_OWNER="Safari"
else
    BROWSER_OWNER="Zen"
fi

if [[ -z "$BROWSER_X" ]]; then
    echo "FAIL: Missing browser position."
    echo "$POS_LINES"
    exit 1
fi

if [[ -n "$CODEX_X" ]]; then
    if ! (( UPNOTE_X < CODE_X && CODE_X < CODEX_X && CODEX_X < BROWSER_X )); then
        echo "FAIL: Expected UpNote < Code < Codex < $BROWSER_OWNER."
        echo "$POS_LINES"
        exit 1
    fi
else
    if ! (( UPNOTE_X < CODE_X && CODE_X < BROWSER_X )); then
        echo "FAIL: Expected UpNote < Code < $BROWSER_OWNER."
        echo "$POS_LINES"
        exit 1
    fi
fi

echo "PASS: UpNote is on the far left after open/autotile."
