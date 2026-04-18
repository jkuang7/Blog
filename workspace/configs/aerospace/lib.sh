#!/bin/bash
# lib.sh - Core functions for AeroSpace window management
# Source this file from all aerospace scripts

# Hardcode path since symlink resolution is unreliable
AEROSPACE_DIR="/Users/jian/Dev/workspace/configs/aerospace"
source "$AEROSPACE_DIR/config.sh"

# === Global Variables ===
# Initialize variables to avoid unbound variable errors with set -u
UPNOTE_WIDS=()
VSCODE_WID=""
CODEX_WID=""
TERMINAL_WID=""
TELEGRAM_WID=""
ZEN_WID=""
SAFARI_WID=""
STATE_BROWSER=""
STATE_UPNOTE_TILED="false"
STATE_TILED_ORDER=""
STATE_ACTIVE_UTILITY_BUNDLE=""
STATE_ACTIVE_UTILITY_WID=""
POPUP_TITLE_AWK_REGEX='oauth|auth|sign[[:space:]]in|log[[:space:]]in|login|permission|extension|download|save|open[[:space:]]file|alert|dialog|sheet|confirm|prompt|settings|preferences|prefs|rename[[:space:]]+(tab|workspace|surface|pane)'

now_ms() {
    python3 - <<'PY' 2>/dev/null
import time
print(int(time.time() * 1000))
PY
}

perf_enabled() {
    [[ "${AEROSPACE_PERF_LOG:-0}" == "1" ]]
}

perf_mark_start() {
    if perf_enabled; then
        now_ms
    else
        echo ""
    fi
}

perf_log_duration() {
    local label="${1:-callback}"
    local start_ms="${2:-}"
    local meta="${3:-}"
    local end_ms delta

    perf_enabled || return 0
    [[ "$start_ms" =~ ^[0-9]+$ ]] || return 0

    end_ms="$(now_ms)"
    [[ "$end_ms" =~ ^[0-9]+$ ]] || return 0
    delta=$(( end_ms - start_ms ))

    if [[ -n "$meta" ]]; then
        log "perf:${label} ms=${delta} ${meta}"
    else
        log "perf:${label} ms=${delta}"
    fi
}

is_popup_title() {
    local title="${1:-}"
    local title_lc
    title_lc="$(echo "$title" | tr '[:upper:]' '[:lower:]')"
    [[ "$title_lc" =~ $POPUP_TITLE_AWK_REGEX ]]
}

should_allow_browser_snapshot_rebuild() {
    local pending_bundle="$1"
    local active_bundle="$2"
    local has_nonpopup_window="$3"
    local focused_app="$4"
    local focused_is_popup="$5"
    local latest_is_popup="$6"

    # Contender browser with a non-popup window must switch/rebuild.
    if [[ "$pending_bundle" != "$active_bundle" && "$has_nonpopup_window" == "true" ]]; then
        return 0
    fi

    # Active browser main-window intent should still rebuild on snapshot-equal.
    if [[ "$focused_app" == "$pending_bundle" && "$focused_is_popup" != "true" && "$latest_is_popup" != "true" ]]; then
        return 0
    fi

    return 1
}

is_utility_bundle() {
    local bundle="${1:-}"
    case "$bundle" in
        "$CODEX"|"$TERMINAL"|"$TELEGRAM")
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

should_promote_focused_terminal_window() {
    local focused_bundle="${1:-}"
    local focused_wid="${2:-}"
    local focused_is_popup="${3:-false}"
    local active_bundle="${4:-}"
    local active_wid="${5:-}"
    local snapshot="${6:-}"
    local primary_wid=""

    [[ "$focused_bundle" == "$TERMINAL" ]] || return 1
    [[ "$focused_is_popup" != "true" ]] || return 1
    [[ "$focused_wid" =~ ^[0-9]+$ ]] || return 1
    if [[ -n "$snapshot" ]]; then
        primary_wid="$(get_primary_window_for_bundle_from_snapshot "$snapshot" "$TERMINAL")"
        [[ -n "$primary_wid" && "$focused_wid" != "$primary_wid" ]] && return 1
    fi
    [[ "$active_bundle" != "$TERMINAL" || "$focused_wid" != "$active_wid" ]]
}

should_allow_utility_main_window_rebuild() {
    local bundle="${1:-}"
    local focused_app="${2:-}"
    local focused_wid="${3:-}"
    local latest_wid="${4:-}"
    local primary_wid="${5:-}"
    local focused_is_popup="${6:-false}"

    is_utility_bundle "$bundle" || return 1
    [[ "$focused_app" == "$bundle" ]] || return 1
    [[ "$focused_is_popup" != "true" ]] || return 1
    [[ "$focused_wid" =~ ^[0-9]+$ ]] || return 1
    [[ "$focused_wid" == "$latest_wid" ]] || return 1

    if [[ "$bundle" == "$TERMINAL" && -n "$primary_wid" && "$focused_wid" != "$primary_wid" ]]; then
        return 1
    fi

    return 0
}

filter_overlay_candidates_from_lines() {
    local active_browser_bundle="${1:-}"
    local excluded_wids_csv="${2:-}"
    awk -F'|' -v active="$active_browser_bundle" -v excluded="$excluded_wids_csv" '
        function is_excluded(id,  i, a, n) {
            if (excluded == "") return 0
            n = split(excluded, a, ",")
            for (i = 1; i <= n; i++) {
                if (a[i] == id) return 1
            }
            return 0
        }
        {
            wid=$1
            bundle=$2
            layout=$3
            if (is_excluded(wid)) next
            tiled=(layout ~ /tiles/)
            browser=(bundle=="app.zen-browser.zen" || bundle=="com.apple.Safari" || bundle=="com.google.Chrome" || bundle=="company.thebrowser.Browser" || bundle=="com.brave.Browser" || bundle=="org.mozilla.firefox")
            # Keep all non-browser floaters and only active-browser floaters.
            if (!tiled && (!browser || (active != "" && bundle == active))) {
                print wid "|" bundle
            }
        }
    '
}

# === State Management ===

normalize_ws() {
    local ws="${1:-}"
    case "$ws" in
        w1|1) echo "w1" ;;
        *) echo "$ws" ;;
    esac
}

is_home_ws() {
    local ws
    ws=$(normalize_ws "${1:-}")
    [[ "$ws" == "w1" ]]
}

read_state_v2_file() {
    local file_path="$1"
    python3 - "$file_path" <<'PY' 2>/dev/null
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)

if data.get("version") != 2:
    raise SystemExit(1)

workspace = data.get("workspace")
if workspace not in {"w1", "1"}:
    raise SystemExit(1)

browser = data.get("browser", "")
if browser not in {"zen", "safari", ""}:
    browser = ""

upnote_tiled = "true" if bool(data.get("upnoteTiled", False)) else "false"

tiled_order = []
for value in data.get("tiledOrder", []):
    try:
        number = int(value)
    except (TypeError, ValueError):
        continue
    if number >= 0:
        tiled_order.append(str(number))

active_utility_bundle = data.get("activeUtilityBundle", "")
if active_utility_bundle not in {
    "com.openai.codex",
    "com.cmuxterm.app",
    "com.tdesktop.Telegram",
    "",
}:
    active_utility_bundle = ""

active_utility_wid = data.get("activeUtilityWindowId")
if active_utility_wid is None:
    active_utility_wid_str = ""
else:
    try:
        parsed_wid = int(active_utility_wid)
    except (TypeError, ValueError):
        parsed_wid = -1
    active_utility_wid_str = str(parsed_wid) if parsed_wid >= 0 else ""

print(
    f"{browser}|{upnote_tiled}|{','.join(tiled_order)}|"
    f"{active_utility_bundle}|{active_utility_wid_str}"
)
PY
}

write_state_v2_file() {
    local ws="$1"
    local state_file_v2="$2"
    python3 - "$ws" "$state_file_v2" "$STATE_BROWSER" "$STATE_UPNOTE_TILED" "$STATE_TILED_ORDER" "$STATE_ACTIVE_UTILITY_BUNDLE" "$STATE_ACTIVE_UTILITY_WID" <<'PY'
import json
import sys
import time

workspace = sys.argv[1]
path = sys.argv[2]
browser = sys.argv[3]
upnote = sys.argv[4] == "true"
tiled_order_raw = sys.argv[5]
active_utility_bundle = sys.argv[6]
active_utility_wid_raw = sys.argv[7]

tiled_order = []
for value in tiled_order_raw.split(","):
    value = value.strip()
    if value.isdigit():
        tiled_order.append(int(value))

payload = {
    "version": 2,
    "workspace": workspace,
    "browser": browser if browser in {"zen", "safari", ""} else "",
    "upnoteTiled": upnote,
    "tiledOrder": tiled_order,
    "activeUtilityBundle": (
        active_utility_bundle
        if active_utility_bundle in {"com.openai.codex", "com.cmuxterm.app", "com.tdesktop.Telegram", ""}
        else ""
    ),
    "activeUtilityWindowId": int(active_utility_wid_raw) if active_utility_wid_raw.isdigit() else None,
    "updatedAtMs": int(time.time() * 1000),
}

with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

# Read workspace state, return defaults if not exists
# Usage: read_state ws
# Sets: STATE_BROWSER, STATE_UPNOTE_TILED
read_state() {
    local ws
    ws=$(normalize_ws "$1")
    local state_file="$STATE_DIR/${ws}.state"
    local state_file_v2="$STATE_DIR/${ws}.state.v2.json"
    local v2_parsed

    # Defaults
    STATE_BROWSER="$w1_default_browser"
    STATE_UPNOTE_TILED="$w1_default_upnote"
    STATE_TILED_ORDER=""
    STATE_ACTIVE_UTILITY_BUNDLE=""
    STATE_ACTIVE_UTILITY_WID=""

    # Prefer typed v2 state when available.
    if [[ -f "$state_file_v2" ]]; then
        v2_parsed="$(read_state_v2_file "$state_file_v2" || true)"
        if [[ -n "$v2_parsed" ]]; then
            STATE_BROWSER="$(echo "$v2_parsed" | cut -d'|' -f1)"
            STATE_UPNOTE_TILED="$(echo "$v2_parsed" | cut -d'|' -f2)"
            STATE_TILED_ORDER="$(echo "$v2_parsed" | cut -d'|' -f3)"
            STATE_ACTIVE_UTILITY_BUNDLE="$(echo "$v2_parsed" | cut -d'|' -f4)"
            STATE_ACTIVE_UTILITY_WID="$(echo "$v2_parsed" | cut -d'|' -f5)"
            return 0
        fi
        log "read_state: invalid v2 state for $ws, falling back to legacy state"
    fi

    # Override with saved state if exists
    if [[ -f "$state_file" ]]; then
        source "$state_file"
        STATE_BROWSER="${BROWSER:-$STATE_BROWSER}"
        STATE_UPNOTE_TILED="${UPNOTE_TILED:-$STATE_UPNOTE_TILED}"
        STATE_TILED_ORDER="${TILED_ORDER:-$STATE_TILED_ORDER}"
        STATE_ACTIVE_UTILITY_BUNDLE="${ACTIVE_UTILITY_BUNDLE:-$STATE_ACTIVE_UTILITY_BUNDLE}"
        STATE_ACTIVE_UTILITY_WID="${ACTIVE_UTILITY_WID:-$STATE_ACTIVE_UTILITY_WID}"
    elif [[ "$ws" == "w1" && -f "$STATE_DIR/1.state" ]]; then
        # Backward compatibility with temporary key naming.
        source "$STATE_DIR/1.state"
        STATE_BROWSER="${BROWSER:-$STATE_BROWSER}"
        STATE_UPNOTE_TILED="${UPNOTE_TILED:-$STATE_UPNOTE_TILED}"
        STATE_TILED_ORDER="${TILED_ORDER:-$STATE_TILED_ORDER}"
        STATE_ACTIVE_UTILITY_BUNDLE="${ACTIVE_UTILITY_BUNDLE:-$STATE_ACTIVE_UTILITY_BUNDLE}"
        STATE_ACTIVE_UTILITY_WID="${ACTIVE_UTILITY_WID:-$STATE_ACTIVE_UTILITY_WID}"
    fi

    if ! is_utility_bundle "${STATE_ACTIVE_UTILITY_BUNDLE:-}"; then
        STATE_ACTIVE_UTILITY_BUNDLE=""
        STATE_ACTIVE_UTILITY_WID=""
    fi
}

# Write workspace state
# Usage: write_state ws
write_state() {
    local ws
    ws=$(normalize_ws "$1")
    local state_file="$STATE_DIR/${ws}.state"
    local state_file_v2="$STATE_DIR/${ws}.state.v2.json"
    local write_mode="${AEROSPACE_STATE_WRITE_MODE:-dual}"

    write_legacy_state_file() {
        cat > "$state_file" << EOF
BROWSER=$STATE_BROWSER
UPNOTE_TILED=$STATE_UPNOTE_TILED
TILED_ORDER=$STATE_TILED_ORDER
ACTIVE_UTILITY_BUNDLE=$STATE_ACTIVE_UTILITY_BUNDLE
ACTIVE_UTILITY_WID=$STATE_ACTIVE_UTILITY_WID
EOF
    }

    case "$write_mode" in
        legacy-only)
            write_legacy_state_file
            ;;
        v2-only)
            write_state_v2_file "$ws" "$state_file_v2"
            ;;
        dual)
            write_legacy_state_file
            write_state_v2_file "$ws" "$state_file_v2"
            ;;
        *)
            log "write_state: unknown AEROSPACE_STATE_WRITE_MODE=$write_mode, defaulting to dual"
            write_legacy_state_file
            write_state_v2_file "$ws" "$state_file_v2"
            ;;
    esac
}

get_window_x_map() {
    swift -e '
import CoreGraphics
import Foundation
let idArgs = Array(CommandLine.arguments.dropFirst())
let ids: [Int] = idArgs.compactMap { Int($0) }
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let arr = (CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]]) ?? []
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
' "$@" 2>/dev/null || true
}

window_x_from_map() {
    local map="$1"
    local wid="$2"
    echo "$map" | awk -F'|' -v w="$wid" '$1==w { print $2; exit }'
}

window_is_on_screen() {
    local wid="${1:-}"
    [[ "$wid" =~ ^[0-9]+$ ]] || return 1
    [[ -n "$(get_window_x_map "$wid")" ]]
}

enforce_precedence_order() {
    local upnote_wid="$1"
    local vscode_wid="$2"
    local codex_wid="$3"
    local terminal_wid="$4"
    local telegram_wid="$5"
    local browser_wid="$6"
    local wids=()

    if [[ -n "$upnote_wid" ]]; then
        wids+=("$upnote_wid")
    fi
    if [[ -n "$vscode_wid" ]]; then
        wids+=("$vscode_wid")
    fi
    if [[ -n "$codex_wid" ]]; then
        wids+=("$codex_wid")
    fi
    if [[ -n "$terminal_wid" ]]; then
        wids+=("$terminal_wid")
    fi
    if [[ -n "$telegram_wid" ]]; then
        wids+=("$telegram_wid")
    fi
    if [[ -n "$browser_wid" ]]; then
        wids+=("$browser_wid")
    fi

    if [[ ${#wids[@]} -lt 2 ]]; then
        return 0
    fi

    local map all_ok left_x right_x left_wid
    for _ in {1..8}; do
        map=$(get_window_x_map "${wids[@]}")
        all_ok="true"
        local i
        for (( i=0; i<${#wids[@]}-1; i++ )); do
            left_x=$(window_x_from_map "$map" "${wids[$i]}")
            right_x=$(window_x_from_map "$map" "${wids[$((i+1))]}")
            if [[ ! "$left_x" =~ ^-?[0-9]+$ || ! "$right_x" =~ ^-?[0-9]+$ ]]; then
                all_ok="false"
                continue
            fi
            if (( left_x >= right_x )); then
                all_ok="false"
                left_wid="${wids[$i]}"
                if [[ -n "$left_wid" ]]; then
                    aerospace focus --window-id "$left_wid" 2>/dev/null || true
                    aerospace swap left 2>/dev/null || true
                fi
            fi
        done
        if [[ "$all_ok" == "true" ]]; then
            break
        fi
    done
}

# === Window Discovery ===

converge_all_windows_to_workspace() {
    local target_ws="${1:-}"
    local moved=0
    local windows

    [[ -z "$target_ws" ]] && return 0

    windows=$(aerospace list-windows --all --format '%{window-id}|%{workspace}|%{app-bundle-id}' 2>/dev/null || true)

    while IFS='|' read -r wid ws bundle; do
        [[ -z "$wid" || -z "$ws" ]] && continue
        if [[ "$ws" != "$target_ws" ]]; then
            aerospace move-node-to-workspace --window-id "$wid" "$target_ws" 2>/dev/null || true
            moved=$((moved + 1))
        fi
    done <<< "$windows"

    if [[ $moved -gt 0 ]]; then
        log "converge: moved $moved window(s) to workspace $target_ws"
    fi
}

visible_owner_name_for_bundle() {
    local bundle="${1:-}"
    case "$bundle" in
        "$VSCODE") echo "Code" ;;
        "$CODEX") echo "Codex" ;;
        "$TERMINAL") echo "cmux" ;;
        "$TELEGRAM") echo "Telegram" ;;
        "$ZEN") echo "Zen" ;;
        "$SAFARI") echo "Safari" ;;
        "$UPNOTE") echo "UpNote" ;;
        *) echo "" ;;
    esac
}

capture_current_tiled_slot_widths() {
    local ws="${1:-}"
    local owner_csv=""
    local bundle layout owner

    while IFS='|' read -r bundle layout; do
        [[ -z "$bundle" || -z "$layout" ]] && continue
        [[ "$layout" == *tiles* ]] || continue
        owner="$(visible_owner_name_for_bundle "$bundle")"
        [[ -n "$owner" ]] || continue
        if [[ -n "$owner_csv" ]]; then
            owner_csv+=","
        fi
        owner_csv+="$owner"
    done < <(aerospace list-windows --workspace "$ws" --format '%{app-bundle-id}|%{window-layout}' 2>/dev/null || true)

    [[ -n "$owner_csv" ]] || return 0

    OWNER_CSV="$owner_csv" swift - <<'SWIFT'
import CoreGraphics
import Foundation

let rawOwners = ProcessInfo.processInfo.environment["OWNER_CSV"] ?? ""
let allowedOwners = Set(rawOwners.split(separator: ",").map(String.init))

guard !allowedOwners.isEmpty else {
  print("")
  exit(0)
}

let options = CGWindowListOption(arrayLiteral: .optionOnScreenOnly, .excludeDesktopElements)
var bestByOwner: [String: (x: Int, width: Int, area: Int)] = [:]

if let info = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] {
  for win in info {
    let owner = win[kCGWindowOwnerName as String] as? String ?? ""
    guard allowedOwners.contains(owner) else { continue }

    let layer = win[kCGWindowLayer as String] as? Int ?? -1
    guard layer == 0 else { continue }

    let bounds = win[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let x = bounds["X"] as? Int ?? 0
    let width = bounds["Width"] as? Int ?? 0
    let height = bounds["Height"] as? Int ?? 0
    guard width > 0, height > 100 else { continue }

    let area = width * height
    let current = bestByOwner[owner]
    if current == nil || area > current!.area {
      bestByOwner[owner] = (x: x, width: width, area: area)
    }
  }
}

let orderedWidths = bestByOwner.values.sorted { $0.x < $1.x }.map { String($0.width) }
print(orderedWidths.joined(separator: ","))
SWIFT
}

restore_preserved_slot_widths() {
    local slot_csv="${1:-}"
    local width_csv="${2:-}"
    local slots=()
    local widths=()
    local i

    [[ -n "$slot_csv" && -n "$width_csv" ]] || return 0

    IFS=',' read -r -a slots <<< "$slot_csv"
    IFS=',' read -r -a widths <<< "$width_csv"

    if [[ ${#slots[@]} -lt 2 || ${#slots[@]} -ne ${#widths[@]} ]]; then
        log "restore_slot_widths: skipping count mismatch slots=$slot_csv widths=$width_csv"
        return 0
    fi

    local pass_count=3
    local pass
    for (( pass=0; pass<pass_count; pass++ )); do
        for (( i=${#slots[@]}-2; i>=0; i-- )); do
            local wid="${slots[$i]}"
            local target_width="${widths[$i]}"
            [[ "$wid" =~ ^[0-9]+$ && "$target_width" =~ ^[0-9]+$ ]] || continue
            aerospace resize --window-id "$wid" width "$target_width" 2>/dev/null || true
        done
    done

    log "restore_slot_widths: restored widths=$width_csv for slots=$slot_csv"
}

workspace_untile_ids_from_lines() {
    awk -F'|' '
        {
            wid=$1
            if (wid ~ /^[0-9]+$/ && !(wid in seen)) {
                seen[wid]=1
                print wid
            }
        }
    '
}

untile_workspace_windows() {
    local ws="$1"
    local wid

    aerospace list-windows --workspace "$ws" --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | workspace_untile_ids_from_lines \
        | while IFS= read -r wid; do
            [[ -z "$wid" ]] && continue
            aerospace layout --window-id "$wid" floating 2>/dev/null || true
        done
}

build_tiled_slot_order_csv() {
    local upnote_wid="$1"
    local vscode_wid="$2"
    local utility_wid="$3"
    local browser_wid="$4"
    local ordered_wids=()

    if [[ -n "$upnote_wid" ]]; then
        ordered_wids+=("$upnote_wid")
    fi
    if [[ -n "$vscode_wid" ]]; then
        ordered_wids+=("$vscode_wid")
    fi

    if [[ -n "$browser_wid" ]]; then
        if [[ ${#ordered_wids[@]} -lt 2 && -n "$utility_wid" ]]; then
            ordered_wids+=("$utility_wid")
        fi
        ordered_wids+=("$browser_wid")
    elif [[ -n "$utility_wid" ]]; then
        ordered_wids+=("$utility_wid")
    fi

    if [[ ${#ordered_wids[@]} -eq 0 ]]; then
        echo ""
        return 0
    fi

    local ordered_csv
    ordered_csv=$(IFS=,; echo "${ordered_wids[*]}")
    echo "$ordered_csv"
}

build_home_core_order_csv() {
    local upnote_wid="$1"
    local vscode_wid="$2"
    local codex_wid="$3"
    local terminal_wid="$4"
    local telegram_wid="$5"
    local browser_wid="$6"
    local utility_wid=""

    for utility_wid in "$codex_wid" "$terminal_wid" "$telegram_wid"; do
        if [[ -n "$utility_wid" ]]; then
            break
        fi
    done

    build_tiled_slot_order_csv "$upnote_wid" "$vscode_wid" "$utility_wid" "$browser_wid"
}

# Get all home app window IDs
# Sets: VSCODE_WID, CODEX_WID, TERMINAL_WID, TELEGRAM_WID, ZEN_WID, SAFARI_WID, UPNOTE_WIDS (array)
# UPNOTE_WIDS is sorted: main "UpNote" window first, then note windows
get_home_windows() {
    local all_windows
    all_windows=$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-title}' 2>/dev/null)

    local vscode_min=""
    local codex_min=""
    local terminal_min=""
    local telegram_min=""
    local zen_min=""
    local safari_min=""
    UPNOTE_WIDS=()
    local upnote_main=""
    local upnote_notes=()

    while IFS='|' read -r wid bundle title; do
        [[ -z "$wid" ]] && continue
        case "$bundle" in
            "$VSCODE")
                if [[ -z "$vscode_min" || "$wid" -lt "$vscode_min" ]]; then
                    vscode_min="$wid"
                fi
                ;;
            "$CODEX")
                if [[ -z "$codex_min" || "$wid" -lt "$codex_min" ]]; then
                    codex_min="$wid"
                fi
                ;;
            "$TERMINAL")
                if [[ -z "$terminal_min" || "$wid" -lt "$terminal_min" ]]; then
                    terminal_min="$wid"
                fi
                ;;
            "$TELEGRAM")
                if [[ -z "$telegram_min" || "$wid" -lt "$telegram_min" ]]; then
                    telegram_min="$wid"
                fi
                ;;
            "$ZEN")
                if [[ -z "$zen_min" || "$wid" -lt "$zen_min" ]]; then
                    zen_min="$wid"
                fi
                ;;
            "$SAFARI")
                if [[ -z "$safari_min" || "$wid" -lt "$safari_min" ]]; then
                    safari_min="$wid"
                fi
                ;;
            "$UPNOTE")
                # Main window titled "UpNote" goes first, note windows after
                if [[ "$title" == "UpNote" ]]; then
                    upnote_main="$wid"
                else
                    upnote_notes+=("$wid")
                fi
                ;;
        esac
    done <<< "$all_windows"

    VSCODE_WID="$vscode_min"
    CODEX_WID="$codex_min"
    TERMINAL_WID=""
    if [[ "${STATE_ACTIVE_UTILITY_BUNDLE:-}" == "$TERMINAL" && -n "${STATE_ACTIVE_UTILITY_WID:-}" ]]; then
        if printf '%s\n' "$all_windows" | awk -F'|' -v wid="$STATE_ACTIVE_UTILITY_WID" '$1==wid { found=1 } END { exit(found ? 0 : 1) }'; then
            TERMINAL_WID="$STATE_ACTIVE_UTILITY_WID"
        fi
    fi
    if [[ -z "$TERMINAL_WID" ]]; then
        TERMINAL_WID="$terminal_min"
    fi
    TELEGRAM_WID="$telegram_min"
    ZEN_WID="$zen_min"
    SAFARI_WID="$safari_min"

    # Build array: main window first, then notes
    [[ -n "$upnote_main" ]] && UPNOTE_WIDS+=("$upnote_main")
    [[ ${#upnote_notes[@]} -gt 0 ]] && UPNOTE_WIDS+=("${upnote_notes[@]}") || true
}

# Get the browser window ID based on state
# Usage: get_active_browser
# Returns: window ID or empty (caller handles promotion if empty)
get_latest_window_for_bundle_from_snapshot() {
    local snapshot="${1:-}"
    local bundle="${2:-}"
    [[ -z "$bundle" ]] && return 0
    printf '%s\n' "$snapshot" \
        | awk -F'|' -v b="$bundle" '
            $2==b { if ($1+0 > m) m=$1+0 }
            END { if (m>0) print m }
        '
}

get_window_layout_for_id_from_snapshot() {
    local snapshot="${1:-}"
    local wid="${2:-}"
    [[ -z "$wid" ]] && return 0
    printf '%s\n' "$snapshot" \
        | awk -F'|' -v target="$wid" '$1==target { print $3; exit }'
}

get_primary_window_for_bundle_from_snapshot() {
    local snapshot="${1:-}"
    local bundle="${2:-}"
    [[ -z "$bundle" ]] && return 0
    printf '%s\n' "$snapshot" \
        | awk -F'|' -v b="$bundle" -v re="$POPUP_TITLE_AWK_REGEX" '
            $2==b {
                wid=$1+0
                layout=tolower($3)
                title=tolower($4)
                popup=(title ~ re)
                if (!popup) {
                    if (layout ~ /tiles/) {
                        if (best_tiled==0 || wid < best_tiled) best_tiled=wid
                    }
                    if (best_nonpopup==0 || wid < best_nonpopup) best_nonpopup=wid
                }
                if (best_any==0 || wid < best_any) best_any=wid
            }
            END {
                if (best_tiled) print best_tiled
                else if (best_nonpopup) print best_nonpopup
                else if (best_any) print best_any
            }
        '
}

get_primary_window_for_bundle() {
    local bundle="${1:-}"
    local snapshot="${2:-}"
    [[ -z "$bundle" ]] && return 0

    if [[ -z "$snapshot" ]]; then
        snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    fi

    get_primary_window_for_bundle_from_snapshot "$snapshot" "$bundle"
}

get_on_screen_window_for_bundle() {
    local bundle="${1:-}"
    local snapshot="${2:-}"
    local nonpopup_ids=""
    local any_ids=""
    local wid
    [[ -z "$bundle" ]] && return 0

    if [[ -z "$snapshot" ]]; then
        snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    fi

    nonpopup_ids="$(printf '%s\n' "$snapshot" | awk -F'|' -v b="$bundle" -v re="$POPUP_TITLE_AWK_REGEX" '
        $2==b && tolower($4) !~ re { print $1 }
    ' | sort -n)"
    any_ids="$(printf '%s\n' "$snapshot" | awk -F'|' -v b="$bundle" '$2==b { print $1 }' | sort -n)"

    while IFS= read -r wid; do
        [[ -z "$wid" ]] && continue
        if window_is_on_screen "$wid"; then
            echo "$wid"
            return 0
        fi
    done <<< "$nonpopup_ids"

    while IFS= read -r wid; do
        [[ -z "$wid" ]] && continue
        if window_is_on_screen "$wid"; then
            echo "$wid"
            return 0
        fi
    done <<< "$any_ids"
}

window_exists_in_snapshot() {
    local snapshot="${1:-}"
    local wid="${2:-}"
    [[ -z "$wid" ]] && return 1
    printf '%s\n' "$snapshot" | awk -F'|' -v target="$wid" '$1==target { found=1 } END { exit(found ? 0 : 1) }'
}

get_latest_nonpopup_window_for_bundle_from_snapshot() {
    local snapshot="${1:-}"
    local bundle="${2:-}"
    [[ -z "$bundle" ]] && return 0
    printf '%s\n' "$snapshot" \
        | awk -F'|' -v b="$bundle" -v re="$POPUP_TITLE_AWK_REGEX" '
            $2==b {
                wid=$1+0
                title=tolower($4)
                if (title !~ re && wid > latest_nonpopup) {
                    latest_nonpopup=wid
                }
                if (wid > latest_any) {
                    latest_any=wid
                }
            }
            END {
                if (latest_nonpopup) print latest_nonpopup
                else if (latest_any) print latest_any
            }
        '
}

get_latest_on_screen_window_for_bundle_from_snapshot() {
    local snapshot="${1:-}"
    local bundle="${2:-}"
    local nonpopup_ids=""
    local any_ids=""
    local wid=""
    [[ -z "$bundle" ]] && return 0

    nonpopup_ids="$(printf '%s\n' "$snapshot" | awk -F'|' -v b="$bundle" -v re="$POPUP_TITLE_AWK_REGEX" '
        $2==b && tolower($4) !~ re { print $1 }
    ' | sort -nr)"
    any_ids="$(printf '%s\n' "$snapshot" | awk -F'|' -v b="$bundle" '$2==b { print $1 }' | sort -nr)"

    while IFS= read -r wid; do
        [[ -z "$wid" ]] && continue
        if window_is_on_screen "$wid"; then
            echo "$wid"
            return 0
        fi
    done <<< "$nonpopup_ids"

    while IFS= read -r wid; do
        [[ -z "$wid" ]] && continue
        if window_is_on_screen "$wid"; then
            echo "$wid"
            return 0
        fi
    done <<< "$any_ids"
}

get_latest_nonpopup_utility_window_from_snapshot() {
    local snapshot="${1:-}"
    printf '%s\n' "$snapshot" \
        | awk -F'|' -v re="$POPUP_TITLE_AWK_REGEX" '
            function is_utility(bundle) {
                return (bundle=="com.openai.codex" || bundle=="com.cmuxterm.app" || bundle=="com.tdesktop.Telegram")
            }
            {
                wid=$1+0
                bundle=$2
                title=tolower($4)
                if (!is_utility(bundle)) next
                if (title !~ re && wid > latest_nonpopup) {
                    latest_nonpopup=wid
                    latest_nonpopup_bundle=bundle
                }
                if (wid > latest_any) {
                    latest_any=wid
                    latest_any_bundle=bundle
                }
            }
            END {
                if (latest_nonpopup) print latest_nonpopup_bundle "|" latest_nonpopup
                else if (latest_any) print latest_any_bundle "|" latest_any
            }
        '
}

get_latest_on_screen_utility_window_from_snapshot() {
    local snapshot="${1:-}"
    local bundle=""
    local wid=""
    for bundle in "$CODEX" "$TERMINAL" "$TELEGRAM"; do
        wid="$(get_latest_on_screen_window_for_bundle_from_snapshot "$snapshot" "$bundle")"
        if [[ -n "$wid" ]]; then
            echo "${bundle}|${wid}"
            return 0
        fi
    done
}

resolve_active_utility_window() {
    local snapshot="${1:-}"
    local focused_bundle="${2:-}"
    local focused_wid="${3:-}"
    local focused_is_popup="${4:-false}"
    local selected_bundle=""
    local selected_wid=""
    local latest_pair=""

    if [[ -z "$snapshot" ]]; then
        snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    fi

    if [[ -n "$STATE_ACTIVE_UTILITY_WID" ]] && window_exists_in_snapshot "$snapshot" "$STATE_ACTIVE_UTILITY_WID"; then
        local active_title_lc
        active_title_lc="$(printf '%s\n' "$snapshot" | awk -F'|' -v wid="$STATE_ACTIVE_UTILITY_WID" '$1==wid { print tolower($4); exit }')"
        if [[ -z "$active_title_lc" || ! "$active_title_lc" =~ $POPUP_TITLE_AWK_REGEX ]]; then
            echo "${STATE_ACTIVE_UTILITY_BUNDLE}|${STATE_ACTIVE_UTILITY_WID}"
            return 0
        fi
    fi

    latest_pair="$(get_latest_on_screen_utility_window_from_snapshot "$snapshot")"
    if [[ -n "$latest_pair" ]]; then
        selected_bundle="$(echo "$latest_pair" | cut -d'|' -f1)"
        selected_wid="$(echo "$latest_pair" | cut -d'|' -f2)"
    fi

    if [[ -z "$selected_wid" ]] && is_utility_bundle "$focused_bundle" && [[ "$focused_is_popup" != "true" ]] && [[ "$focused_wid" =~ ^[0-9]+$ ]]; then
        selected_bundle="$focused_bundle"
        selected_wid="$focused_wid"
    fi

    if [[ -z "$selected_wid" ]]; then
        for selected_bundle in "$CODEX" "$TERMINAL" "$TELEGRAM"; do
            selected_wid="$(get_latest_nonpopup_window_for_bundle_from_snapshot "$snapshot" "$selected_bundle")"
            if [[ -n "$selected_wid" ]]; then
                break
            fi
        done
        if [[ -z "$selected_wid" ]]; then
            latest_pair="$(get_latest_nonpopup_utility_window_from_snapshot "$snapshot")"
            if [[ -n "$latest_pair" ]]; then
                selected_bundle="$(echo "$latest_pair" | cut -d'|' -f1)"
                selected_wid="$(echo "$latest_pair" | cut -d'|' -f2)"
            fi
        fi
    fi

    echo "${selected_bundle}|${selected_wid}"
}

sync_active_utility_state_with_windows() {
    local snapshot="${1:-}"
    local resolved_bundle=""
    local resolved_wid=""
    local resolved_pair=""

    if [[ -z "$snapshot" ]]; then
        snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    fi

    resolved_pair="$(resolve_active_utility_window "$snapshot")"
    resolved_bundle="$(echo "$resolved_pair" | cut -d'|' -f1)"
    resolved_wid="$(echo "$resolved_pair" | cut -d'|' -f2)"

    STATE_ACTIVE_UTILITY_BUNDLE="$resolved_bundle"
    STATE_ACTIVE_UTILITY_WID="$resolved_wid"
}

get_active_browser() {
    local resolved=""
    local snapshot=""
    snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    if [[ "$STATE_BROWSER" == "zen" ]]; then
        resolved="$(get_on_screen_window_for_bundle "$ZEN" "$snapshot")"
        if [[ -n "$resolved" ]]; then
            echo "$resolved"
            return 0
        fi
    elif [[ "$STATE_BROWSER" == "safari" ]]; then
        resolved="$(get_on_screen_window_for_bundle "$SAFARI" "$snapshot")"
        if [[ -n "$resolved" ]]; then
            echo "$resolved"
            return 0
        fi
    fi

    # Fallback promotion path if state/browser windows diverged.
    resolved="$(get_on_screen_window_for_bundle "$ZEN" "$snapshot")"
    if [[ -n "$resolved" ]]; then
        STATE_BROWSER="zen"
        echo "$resolved"
        return 0
    fi
    resolved="$(get_on_screen_window_for_bundle "$SAFARI" "$snapshot")"
    if [[ -n "$resolved" ]]; then
        STATE_BROWSER="safari"
        echo "$resolved"
        return 0
    fi
}

# Sync state with actual window availability
# Call after get_home_windows() to handle closed apps
sync_state_with_windows() {
    local snapshot=""
    local visible_zen=""
    local visible_safari=""
    snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    visible_zen="$(get_on_screen_window_for_bundle "$ZEN" "$snapshot")"
    visible_safari="$(get_on_screen_window_for_bundle "$SAFARI" "$snapshot")"

    # Browser: promote if active one closed
    if [[ "$STATE_BROWSER" == "zen" && -z "$visible_zen" && -n "$visible_safari" ]]; then
        STATE_BROWSER="safari"
        log "Promoted Safari (Zen closed)"
    elif [[ "$STATE_BROWSER" == "safari" && -z "$visible_safari" && -n "$visible_zen" ]]; then
        STATE_BROWSER="zen"
        log "Promoted Zen (Safari closed)"
    elif [[ "$STATE_BROWSER" == "zen" && -z "$visible_zen" && -z "$visible_safari" ]]; then
        STATE_BROWSER=""
        log "No browser available"
    elif [[ "$STATE_BROWSER" == "safari" && -z "$visible_safari" && -z "$visible_zen" ]]; then
        STATE_BROWSER=""
        log "No browser available"
    fi

    # UpNote: clear tiled state if all closed
    if [[ "$STATE_UPNOTE_TILED" == "true" && ${#UPNOTE_WIDS[@]} -eq 0 ]]; then
        STATE_UPNOTE_TILED="false"
        log "UpNote closed, untiling"
    fi

    sync_active_utility_state_with_windows
}

# Get the "other" browser that should be hidden
get_inactive_browser() {
    local snapshot=""
    snapshot="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    if [[ "$STATE_BROWSER" == "zen" ]]; then
        get_on_screen_window_for_bundle "$SAFARI" "$snapshot"
    elif [[ "$STATE_BROWSER" == "safari" ]]; then
        get_on_screen_window_for_bundle "$ZEN" "$snapshot"
    fi
}

get_window_count_for_bundle() {
    local bundle="${1:-}"
    local snapshot="${2:-}"
    [[ -z "$bundle" ]] && {
        echo 0
        return 0
    }

    if [[ -z "$snapshot" ]]; then
        aerospace list-windows --all --format '%{app-bundle-id}' 2>/dev/null \
            | awk -v b="$bundle" '$0 == b { c++ } END { print c+0 }'
        return 0
    fi

    printf '%s\n' "$snapshot" \
        | awk -F'|' -v b="$bundle" '$2 == b { c++ } END { print c+0 }'
}

is_browser_bundle() {
    local bundle="${1:-}"
    case "$bundle" in
        "app.zen-browser.zen"|"com.apple.Safari"|"com.google.Chrome"|"company.thebrowser.Browser"|"com.brave.Browser"|"org.mozilla.firefox")
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

browser_bundle_to_app_name() {
    local bundle="${1:-}"
    case "$bundle" in
        "app.zen-browser.zen") echo "zen" ;;
        "com.apple.Safari") echo "Safari" ;;
        "com.google.Chrome") echo "Google Chrome" ;;
        "company.thebrowser.Browser") echo "Arc" ;;
        "com.brave.Browser") echo "Brave Browser" ;;
        "org.mozilla.firefox") echo "Firefox" ;;
        *) echo "" ;;
    esac
}

enforce_single_browser_window() {
    local ws="$1"
    local primary_browser_wid="$2"
    local active_browser_bundle="$3"
    local browser_lines line wid bundle app_name

    browser_lines="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
        | awk -F'|' '
            $2=="app.zen-browser.zen" || $2=="com.apple.Safari" || $2=="com.google.Chrome" || $2=="company.thebrowser.Browser" || $2=="com.brave.Browser" || $2=="org.mozilla.firefox" {
                print $1 "|" $2
            }
        ')"

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        wid="${line%%|*}"
        bundle="${line#*|}"
        [[ -z "$wid" || -z "$bundle" ]] && continue

        if [[ -n "$active_browser_bundle" && "$bundle" == "$active_browser_bundle" && "$wid" == "$primary_browser_wid" ]]; then
            continue
        fi

        aerospace layout --window-id "$wid" floating 2>/dev/null || true

        if [[ -n "$active_browser_bundle" && "$bundle" != "$active_browser_bundle" ]]; then
            app_name="$(browser_bundle_to_app_name "$bundle")"
            hide_bundle_app "$bundle"
            if [[ -n "$app_name" ]]; then
                hide_app "$app_name"
            fi
        fi
    done <<< "$browser_lines"
}

enforce_single_browser_window_in_workspace() {
    local ws="$1"
    local primary_browser_wid="$2"
    local active_browser_bundle="$3"
    local lines line wid bundle layout app_name

    lines="$(aerospace list-windows --workspace "$ws" --format '%{window-id}|%{app-bundle-id}|%{window-layout}' 2>/dev/null \
        | awk -F'|' '
            $2=="app.zen-browser.zen" || $2=="com.apple.Safari" || $2=="com.google.Chrome" || $2=="company.thebrowser.Browser" || $2=="com.brave.Browser" || $2=="org.mozilla.firefox" {
                print $1 "|" $2 "|" $3
            }
        ')"

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        wid="$(echo "$line" | cut -d'|' -f1)"
        bundle="$(echo "$line" | cut -d'|' -f2)"
        layout="$(echo "$line" | cut -d'|' -f3)"
        [[ -z "$wid" || -z "$bundle" ]] && continue

        if [[ -n "$active_browser_bundle" && "$bundle" == "$active_browser_bundle" && "$wid" == "$primary_browser_wid" ]]; then
            if [[ "$layout" != *tiles* ]]; then
                aerospace layout --window-id "$wid" tiling 2>/dev/null || true
            fi
            continue
        fi

        aerospace layout --window-id "$wid" floating 2>/dev/null || true
    done <<< "$lines"

    # Keep only active browser app visible; hide all other browser apps.
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        bundle="${line#*|}"
        if [[ -n "$active_browser_bundle" && "$bundle" == "$active_browser_bundle" ]]; then
            continue
        fi
        app_name="$(browser_bundle_to_app_name "$bundle")"
        hide_bundle_app "$bundle"
        if [[ -n "$app_name" ]]; then
            hide_app "$app_name"
        fi
    done <<< "$(echo "$lines" | awk -F'|' '{print $1 "|" $2}' | sort -u)"
}

capture_non_browser_floating_windows() {
    local ws="$1"
    local active_browser_bundle="${2:-}"
    local excluded_wids_csv="${3:-}"
    aerospace list-windows --workspace "$ws" --format '%{window-id}|%{app-bundle-id}|%{window-layout}' 2>/dev/null \
        | filter_overlay_candidates_from_lines "$active_browser_bundle" "$excluded_wids_csv"
}

restore_non_browser_floating_windows() {
    local ws="$1"
    local overlay_wids="$2"
    local active_browser_bundle="${3:-}"
    local core_wids_csv="${4:-}"
    local preferred_focus_wid="${5:-}"
    local preferred_focus_bundle="${6:-}"
    local line overlay_top_wid="" overlay_top_bundle="" overlay_top_active_wid="" wid bundle
    [[ -z "$overlay_wids" ]] && return 0

    local ws_windows_snapshot
    ws_windows_snapshot="$(aerospace list-windows --workspace "$ws" --format '%{window-id}|%{app-bundle-id}|%{window-layout}' 2>/dev/null || true)"

    overlay_wids="$(subtract_core_tiles_from_overlay_lines "$overlay_wids" "$core_wids_csv" "$ws_windows_snapshot" "$active_browser_bundle")"
    [[ -z "$overlay_wids" ]] && return 0

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        wid="${line%%|*}"
        bundle="${line#*|}"
        [[ -z "$wid" ]] && continue
        overlay_top_wid="$wid"
        overlay_top_bundle="$bundle"
        if [[ -n "$active_browser_bundle" && "$bundle" == "$active_browser_bundle" ]]; then
            overlay_top_active_wid="$wid"
        fi
    done <<< "$overlay_wids"

    if [[ -n "$overlay_top_active_wid" ]]; then
        set_churn_window
        focus_window_stably "$overlay_top_active_wid" "$active_browser_bundle"
        log "restore overlays: active browser floating window raised"
    elif [[ -n "$overlay_top_wid" ]]; then
        set_churn_window
        focus_window_stably "$overlay_top_wid" "$overlay_top_bundle"
        log "restore overlays: non-browser floating windows raised"
    elif [[ -n "$preferred_focus_wid" ]]; then
        set_churn_window
        focus_window_stably "$preferred_focus_wid" "$preferred_focus_bundle"
        log "restore overlays: kept core tiled window focused"
    fi
}

subtract_core_tiles_from_overlay_lines() {
    local overlay_wids="$1"
    local core_wids_csv="${2:-}"
    local ws_windows_snapshot="${3:-}"
    local active_browser_bundle="${4:-}"
    [[ -z "$overlay_wids" ]] && return 0

    awk -F'|' -v core="$core_wids_csv" -v active="$active_browser_bundle" '
        function in_core(id,  i, a, n) {
            if (core == "") return 0
            n = split(core, a, ",")
            for (i = 1; i <= n; i++) {
                if (a[i] == id) return 1
            }
            return 0
        }
        function is_browser(bundle) {
            return (bundle=="app.zen-browser.zen" || bundle=="com.apple.Safari" || bundle=="com.google.Chrome" || bundle=="company.thebrowser.Browser" || bundle=="com.brave.Browser" || bundle=="org.mozilla.firefox")
        }
        NR==FNR {
            ws_layout[$1]=$3
            next
        }
        {
            wid=$1
            bundle=$2
            if (wid == "") next
            if (in_core(wid)) next
            if (!(wid in ws_layout)) next
            if (ws_layout[wid] ~ /tiles/) next
            if (is_browser(bundle) && active != "" && bundle != active) next
            print wid "|" bundle
        }
    ' <(printf '%s\n' "$ws_windows_snapshot") <(printf '%s\n' "$overlay_wids")
}

# === Visibility Control ===

hide_app() {
    local app_name="$1"
    osascript -e "tell application \"System Events\" to set visible of process \"$app_name\" to false" 2>/dev/null &
}

hide_bundle_app() {
    local bundle_id="$1"
    osascript -e "tell application id \"$bundle_id\" to hide" 2>/dev/null &
}

focus_window_stably() {
    local wid="${1:-}"
    local bundle="${2:-}"
    [[ -z "$wid" ]] && return 0

    if [[ "$bundle" == "com.apple.Terminal" ]]; then
        osascript <<APPLESCRIPT >/dev/null 2>&1 || true
tell application "Terminal"
    activate
    set index of window id ${wid} to 1
end tell
APPLESCRIPT
        return 0
    fi

    aerospace focus --window-id "$wid" 2>/dev/null || true
}

show_app() {
    local app_name="$1"
    osascript -e "tell application \"System Events\" to set visible of process \"$app_name\" to true" 2>/dev/null &
}

# === Core Layout Engine ===

# Rebuild workspace layout based on state
# Usage: rebuild_workspace ws [force]
# force: if "force", always do full rebuild (use for user-triggered actions)
rebuild_workspace() {
    local ws="$1"
    local force="${2:-}"
    local requested_force="$force"

    log "rebuild_workspace $ws (browser=$STATE_BROWSER, upnote=$STATE_UPNOTE_TILED)"

    # Get all home app windows
    get_home_windows

    # Sync state with actual window availability (handle closed apps)
    local prev_active_utility_bundle="$STATE_ACTIVE_UTILITY_BUNDLE"
    local prev_active_utility_wid="$STATE_ACTIVE_UTILITY_WID"
    sync_state_with_windows
    if [[ "$requested_force" != "force" && ("$STATE_ACTIVE_UTILITY_BUNDLE" != "$prev_active_utility_bundle" || "$STATE_ACTIVE_UTILITY_WID" != "$prev_active_utility_wid") ]]; then
        force="force"
        log "rebuild_workspace: forcing full retile for utility owner repair (${prev_active_utility_bundle}:${prev_active_utility_wid} -> ${STATE_ACTIVE_UTILITY_BUNDLE}:${STATE_ACTIVE_UTILITY_WID})"
    fi

    # w1 defaults UpNote visible (but user can close it)
    # Note: UpNote launch on workspace entry handled in switch_ws.sh

    local browser_wid
    browser_wid=$(get_active_browser)
    local active_browser_bundle=""
    if [[ "$STATE_BROWSER" == "zen" ]]; then
        active_browser_bundle="$ZEN"
    elif [[ "$STATE_BROWSER" == "safari" ]]; then
        active_browser_bundle="$SAFARI"
    fi
    # If state and selected window disagree, trust the selected window bundle.
    if [[ -n "$browser_wid" ]]; then
        local browser_wid_bundle
        browser_wid_bundle="$(aerospace list-windows --all --format '%{window-id}|%{app-bundle-id}' 2>/dev/null \
            | awk -F'|' -v wid="$browser_wid" '$1==wid { print $2; exit }')"
        if [[ -n "$browser_wid_bundle" ]]; then
            active_browser_bundle="$browser_wid_bundle"
        fi
    fi
    local active_utility_bundle="$STATE_ACTIVE_UTILITY_BUNDLE"
    local active_utility_wid="$STATE_ACTIVE_UTILITY_WID"
    local codex_wid=""
    local terminal_wid=""
    local telegram_wid=""
    case "$active_utility_bundle" in
        "$CODEX")
            codex_wid="$active_utility_wid"
            ;;
        "$TERMINAL")
            terminal_wid="$active_utility_wid"
            ;;
        "$TELEGRAM")
            telegram_wid="$active_utility_wid"
            ;;
    esac
    local primary_upnote_wid=""
    if [[ "$STATE_UPNOTE_TILED" == "true" && ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        primary_upnote_wid="${UPNOTE_WIDS[0]}"
    fi
    local preferred_focus_wid=""
    local preferred_focus_bundle=""
    if [[ -n "$active_utility_wid" ]]; then
        preferred_focus_wid="$active_utility_wid"
        preferred_focus_bundle="$active_utility_bundle"
    else
        preferred_focus_wid="$browser_wid"
        preferred_focus_bundle="$active_browser_bundle"
    fi
    local target_order_csv=""
    target_order_csv="$(build_tiled_slot_order_csv "$primary_upnote_wid" "$VSCODE_WID" "$active_utility_wid" "$browser_wid")"
    local ordered_wids=()
    if [[ -n "$target_order_csv" ]]; then
        IFS=',' read -r -a ordered_wids <<< "$target_order_csv"
    fi
    local inactive_browser
    inactive_browser=$(get_inactive_browser)

    # Step 0: Capture floating overlays in current workspace BEFORE any
    # normalize/untile/retile work. Include floating windows for the active
    # browser bundle, but ignore floating windows from inactive browsers.
    local captured_overlay_wids=""
    captured_overlay_wids="$(capture_non_browser_floating_windows "$ws" "$active_browser_bundle" "$target_order_csv")"
    # Normalized master object for this rebuild cycle:
    # 1) ordered core apps to tile left->right
    # 2) floating overlays to restore (competing browser overlays excluded)
    local master_core_order_csv="$target_order_csv"
    local master_floating_overlay_wids="$captured_overlay_wids"

    # Step 1: Show all apps that should be visible (sync, not async)
    if [[ -n "$browser_wid" ]]; then
        log "showing $STATE_BROWSER"
        if [[ "$STATE_BROWSER" == "zen" ]]; then
            osascript -e 'tell application "System Events" to set visible of process "zen" to true' 2>/dev/null
        else
            osascript -e 'tell application "System Events" to set visible of process "Safari" to true' 2>/dev/null
        fi
    fi
    if [[ "$STATE_UPNOTE_TILED" == "true" && ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        log "showing UpNote"
        osascript -e 'tell application "System Events" to set visible of process "UpNote" to true' 2>/dev/null
    fi

    # Step 1.5: Enforce single-browser invariant before any retile/rebalance.
    # Keep exactly one primary browser window eligible for tiling; every other
    # browser window is forced floating and inactive browser apps are hidden.
    enforce_single_browser_window "$ws" "$browser_wid" "$active_browser_bundle"

    # Step 2: Hide inactive apps
    if [[ -n "$inactive_browser" ]]; then
        local inactive_name
        local inactive_bundle
        if [[ "$inactive_browser" == "$ZEN_WID" ]]; then
            inactive_name="zen"
            inactive_bundle="$ZEN"
        else
            inactive_name="Safari"
            inactive_bundle="$SAFARI"
        fi

        log "hiding inactive $inactive_name"
        hide_bundle_app "$inactive_bundle"
        hide_app "$inactive_name"
    fi
    if [[ "$STATE_UPNOTE_TILED" != "true" && ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        log "hiding UpNote"
        hide_app "UpNote"
    fi

    # Step 3: Check what we actually need to do
    # Only full rebuild if apps are on wrong workspace
    # NOTE: --all is required, without it list-windows returns nothing
    local all_ws_info
    all_ws_info=$(aerospace list-windows --all --format '%{window-id}|%{workspace}' 2>/dev/null)

    local needs_rebuild="false"

    # Check if required apps are on this workspace
    local vscode_ws
    vscode_ws=$(echo "$all_ws_info" | grep "^$VSCODE_WID|" | cut -d'|' -f2 || true)
    if [[ -n "$VSCODE_WID" && "$vscode_ws" != "$ws" ]]; then
        needs_rebuild="true"
        log "rebuild: VSCode on wrong workspace ($vscode_ws != $ws)"
    fi
    local active_utility_ws
    active_utility_ws=$(echo "$all_ws_info" | grep "^$active_utility_wid|" | cut -d'|' -f2 || true)
    if [[ -n "$active_utility_wid" && "$active_utility_ws" != "$ws" ]]; then
        needs_rebuild="true"
        log "rebuild: active utility on wrong workspace ($active_utility_ws != $ws)"
    fi
    if [[ -n "$browser_wid" ]]; then
        local browser_ws
        browser_ws=$(echo "$all_ws_info" | grep "^$browser_wid|" | cut -d'|' -f2 || true)
        if [[ "$browser_ws" != "$ws" ]]; then
            needs_rebuild="true"
            log "rebuild: browser on wrong workspace ($browser_ws != $ws)"
        fi
    fi
    if [[ "$STATE_UPNOTE_TILED" == "true" && ${#UPNOTE_WIDS[@]} -gt 0 ]]; then
        for upnote_wid in "${UPNOTE_WIDS[@]}"; do
            local upnote_ws
            upnote_ws=$(echo "$all_ws_info" | grep "^$upnote_wid|" | cut -d'|' -f2 || true)
            if [[ "$upnote_ws" != "$ws" ]]; then
                needs_rebuild="true"
                log "rebuild: UpNote $upnote_wid on wrong workspace ($upnote_ws != $ws)"
                break
            fi
        done
    fi

    if [[ "$force" == "force" ]]; then
        needs_rebuild="true"
        log "rebuild: forced by user action"
    fi
    if [[ "$STATE_TILED_ORDER" != "$master_core_order_csv" ]]; then
        needs_rebuild="true"
        log "rebuild: tiled order changed ($STATE_TILED_ORDER -> $master_core_order_csv)"
    fi

    if [[ "$needs_rebuild" == "true" ]]; then
        log "full rebuild - untile then retile by precedence"

        # Reset the entire workspace before reconstructing the core layout.
        untile_workspace_windows "$ws"

        # Retile one-by-one in strict left-to-right slot order.
        for wid in "${ordered_wids[@]-}"; do
            if [[ "$wid" == "$browser_wid" ]]; then
                log "tiling browser $STATE_BROWSER (wid=$browser_wid)"
            fi
            aerospace move-node-to-workspace --window-id "$wid" "$ws" 2>/dev/null || true
            aerospace layout --window-id "$wid" tiling 2>/dev/null || true
        done

        # Hard-stop invariant in workspace: exactly one browser tile.
        enforce_single_browser_window_in_workspace "$ws" "$browser_wid" "$active_browser_bundle"

        # Normalize tree and then enforce precedence order via swaps.
        aerospace flatten-workspace-tree 2>/dev/null || true
        aerospace balance-sizes 2>/dev/null || true
        # Apply sizing only after actual rebuild (not on focus changes)
        apply_sizing "$ws" "$master_core_order_csv" "$browser_wid" "$primary_upnote_wid"
        enforce_precedence_order "$primary_upnote_wid" "$VSCODE_WID" "$codex_wid" "$terminal_wid" "$telegram_wid" "$browser_wid"

        # Restore captured floating overlays (excluding inactive browsers).
        if [[ -n "$master_floating_overlay_wids" ]]; then
            restore_non_browser_floating_windows "$ws" "$master_floating_overlay_wids" "$active_browser_bundle" "$master_core_order_csv" "$preferred_focus_wid" "$preferred_focus_bundle"
        elif [[ -n "$browser_wid" ]]; then
            # No overlay windows, keep browser front-most.
            focus_window_stably "$browser_wid" "$active_browser_bundle"
        fi

        # Re-apply invariant after focus churn.
        enforce_single_browser_window_in_workspace "$ws" "$browser_wid" "$active_browser_bundle"
    else
        log "rebalance - apps already on workspace"
        enforce_single_browser_window_in_workspace "$ws" "$browser_wid" "$active_browser_bundle"
        aerospace flatten-workspace-tree 2>/dev/null || true
        aerospace balance-sizes 2>/dev/null || true
        apply_sizing "$ws" "$master_core_order_csv" "$browser_wid" "$primary_upnote_wid"
        enforce_precedence_order "$primary_upnote_wid" "$VSCODE_WID" "$codex_wid" "$terminal_wid" "$telegram_wid" "$browser_wid"

        restore_non_browser_floating_windows "$ws" "$master_floating_overlay_wids" "$active_browser_bundle" "$master_core_order_csv" "$preferred_focus_wid" "$preferred_focus_bundle"
        enforce_single_browser_window_in_workspace "$ws" "$browser_wid" "$active_browser_bundle"
    fi

    STATE_TILED_ORDER="$master_core_order_csv"

    # Save state
    write_state "$ws"
}

# Apply column sizing based on layout
apply_sizing() {
    local ws="$1"
    local slot_csv="${2:-}"
    local slots=()

    if [[ -z "$slot_csv" ]]; then
        log "apply_sizing: no slots, skipping"
        return 0
    fi

    IFS=',' read -r -a slots <<< "$slot_csv"

    case "${#slots[@]}" in
        0|1)
            log "apply_sizing: <=1 tiled slot, skipping explicit widths"
            return 0
            ;;
        *)
            log "apply_sizing: keeping current widths for slots=$slot_csv"
            return 0
            ;;
    esac
}

# === Lock Management ===

LOCK_FILE="$STATE_DIR/rebuild.lock"
LOCK_STALE_SECONDS=20

acquire_lock() {
    if mkdir "$LOCK_FILE" 2>/dev/null; then
        trap 'release_lock' EXIT
        return 0
    fi

    # Recover from stale lock left by crashed callback paths.
    if [[ -d "$LOCK_FILE" ]]; then
        local now_s lock_mtime lock_age
        now_s=$(date +%s)
        lock_mtime=$(stat -f %m "$LOCK_FILE" 2>/dev/null || echo 0)
        if [[ "$lock_mtime" =~ ^[0-9]+$ && "$lock_mtime" -gt 0 ]]; then
            lock_age=$(( now_s - lock_mtime ))
        else
            lock_age=0
        fi
        if [[ "$lock_age" -gt "$LOCK_STALE_SECONDS" ]]; then
            log "acquire_lock: clearing stale rebuild lock (${lock_age}s)"
            rmdir "$LOCK_FILE" 2>/dev/null || true
            if mkdir "$LOCK_FILE" 2>/dev/null; then
                trap 'release_lock' EXIT
                return 0
            fi
        fi
    fi

    return 1
}

release_lock() {
    rmdir "$LOCK_FILE" 2>/dev/null || true
}

# Check if we're in churn window (after workspace switch)
CHURN_FILE="$STATE_DIR/churn_until"
CHURN_DURATION_MS=350

in_churn_window() {
    [[ ! -f "$CHURN_FILE" ]] && return 1

    local until_ms now_ms
    until_ms=$(cat "$CHURN_FILE" 2>/dev/null || echo 0)
    now_ms=$(now_ms 2>/dev/null || echo 0)

    [[ "$now_ms" -lt "$until_ms" ]]
}

set_churn_window() {
    local now_ms
    now_ms=$(now_ms 2>/dev/null || echo 0)
    echo "$(( now_ms + CHURN_DURATION_MS ))" > "$CHURN_FILE"
}

# === Workspace Tracking ===

LAST_WS_FILE="$STATE_DIR/last_ws"

get_last_ws() {
    local ws
    ws=$(cat "$LAST_WS_FILE" 2>/dev/null || echo "")
    normalize_ws "$ws"
}

set_last_ws() {
    local ws
    ws=$(normalize_ws "$1")
    echo "$ws" > "$LAST_WS_FILE"
}
