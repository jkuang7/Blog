#!/bin/bash
# engine_callback.sh - typed engine callback entrypoint (progressive promotion)

set -euo pipefail

AEROSPACE_DIR="/Users/jian/Dev/workspace/configs/aerospace"
source "$AEROSPACE_DIR/lib.sh"

MODE="${1:-ts-active}"
CALLBACK="${2:-}"
shift 2 || true

if [[ -z "$CALLBACK" ]]; then
    log "engine_callback: missing callback argument"
    exit 1
fi

is_managed_bundle() {
    local bundle="${1:-}"
    case "$bundle" in
        "$VSCODE"|"$CODEX"|"$TERMINAL"|"$TELEGRAM"|"$ZEN"|"$SAFARI"|"$UPNOTE")
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_promoted_callback() {
    local callback="$1"
    case "$callback" in
        switch_ws|reset_ws|balance|on_focus|on_window)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

run_planner() {
    local context_json="$1"
    printf "%s" "$context_json" \
        | npm --prefix "$AEROSPACE_DIR/engine" run --silent plan:callback 2>/dev/null
}

build_planner_context_json() {
    local callback="$1"
    shift || true

    local ws
    ws="$(aerospace list-workspaces --focused 2>/dev/null | head -n1)"
    ws="$(normalize_ws "$ws")"

    if [[ "$callback" == "switch_ws" && $# -gt 0 ]]; then
        ws="$(normalize_ws "$1")"
    fi

    local callback_bundle=""
    if [[ "$callback" == "on_window" && $# -gt 0 ]]; then
        callback_bundle="$1"
    fi

    read_state "$ws"
    get_home_windows

    local focused_info focused_wid focused_bundle focused_layout focused_title focused_ws popup_intent
    focused_info="$(aerospace list-windows --focused --format '%{window-id}|%{workspace}|%{app-bundle-id}|%{window-layout}|%{window-title}' 2>/dev/null || true)"
    focused_wid="$(echo "$focused_info" | cut -d'|' -f1)"
    focused_ws="$(normalize_ws "$(echo "$focused_info" | cut -d'|' -f2)")"
    focused_bundle="$(echo "$focused_info" | cut -d'|' -f3)"
    focused_layout="$(echo "$focused_info" | cut -d'|' -f4)"
    focused_title="$(echo "$focused_info" | cut -d'|' -f5-)"
    popup_intent="false"
    if is_popup_title "$focused_title"; then
        popup_intent="true"
    fi

    local is_home="false"
    is_home_ws "$ws" && is_home="true"

    local managed_bundle="false"
    if is_managed_bundle "$callback_bundle"; then
        managed_bundle="true"
    elif [[ -n "$focused_bundle" ]] && is_managed_bundle "$focused_bundle"; then
        managed_bundle="true"
    fi

    local callback_argv_csv
    callback_argv_csv="$(printf "%s\n" "$@")"

    ENGINE_CALLBACK_KIND="$callback" \
    ENGINE_CALLBACK_WS="$ws" \
    ENGINE_CALLBACK_BUNDLE="$callback_bundle" \
    ENGINE_CALLBACK_ARGV="$callback_argv_csv" \
    ENGINE_STATE_BROWSER="$STATE_BROWSER" \
    ENGINE_STATE_UPNOTE_TILED="$STATE_UPNOTE_TILED" \
    ENGINE_STATE_TILED_ORDER="$STATE_TILED_ORDER" \
    ENGINE_VSCODE_WID="${VSCODE_WID:-}" \
    ENGINE_CODEX_WID="${CODEX_WID:-}" \
    ENGINE_TERMINAL_WID="${TERMINAL_WID:-}" \
    ENGINE_TELEGRAM_WID="${TELEGRAM_WID:-}" \
    ENGINE_ZEN_WID="${ZEN_WID:-}" \
    ENGINE_SAFARI_WID="${SAFARI_WID:-}" \
    ENGINE_UPNOTE_WID="${UPNOTE_WIDS[0]:-}" \
    ENGINE_FOCUSED_WID="$focused_wid" \
    ENGINE_FOCUSED_WS="$focused_ws" \
    ENGINE_FOCUSED_BUNDLE="$focused_bundle" \
    ENGINE_FOCUSED_LAYOUT="$focused_layout" \
    ENGINE_FOCUSED_TITLE="$focused_title" \
    ENGINE_GUARD_IS_HOME="$is_home" \
    ENGINE_GUARD_IS_MANAGED="$managed_bundle" \
    ENGINE_GUARD_IS_POPUP="$popup_intent" \
    python3 - <<'PY'
import json
import os
import time

callback_kind = os.environ.get("ENGINE_CALLBACK_KIND", "")
workspace = os.environ.get("ENGINE_CALLBACK_WS", "")
bundle = os.environ.get("ENGINE_CALLBACK_BUNDLE", "")
argv_raw = os.environ.get("ENGINE_CALLBACK_ARGV", "")
state_browser = os.environ.get("ENGINE_STATE_BROWSER", "")
state_upnote = os.environ.get("ENGINE_STATE_UPNOTE_TILED", "false") == "true"
tiled_order_raw = os.environ.get("ENGINE_STATE_TILED_ORDER", "")
guard_home = os.environ.get("ENGINE_GUARD_IS_HOME", "false") == "true"
guard_managed = os.environ.get("ENGINE_GUARD_IS_MANAGED", "false") == "true"
guard_popup = os.environ.get("ENGINE_GUARD_IS_POPUP", "false") == "true"

def wid(name):
    raw = os.environ.get(name, "")
    if raw.isdigit():
        return int(raw)
    return None

def layout_or_default(layout):
    allowed = {"floating", "h_tiles", "v_tiles", "stacked", "accordion"}
    return layout if layout in allowed else "floating"

windows = []
for window_id, bundle_id, title in [
    (wid("ENGINE_VSCODE_WID"), "com.microsoft.VSCode", "VSCode"),
    (wid("ENGINE_CODEX_WID"), "com.openai.codex", "Codex"),
    (wid("ENGINE_TERMINAL_WID"), "com.apple.Terminal", "Terminal"),
    (wid("ENGINE_TELEGRAM_WID"), "com.tdesktop.Telegram", "Telegram"),
    (wid("ENGINE_ZEN_WID"), "app.zen-browser.zen", "Zen"),
    (wid("ENGINE_SAFARI_WID"), "com.apple.Safari", "Safari"),
    (wid("ENGINE_UPNOTE_WID"), "com.getupnote.desktop", "UpNote")
]:
    if window_id is not None:
        windows.append(
            {
                "windowId": window_id,
                "workspace": "w1",
                "bundleId": bundle_id,
                "layout": "h_tiles",
                "title": title,
            }
        )

tiled_order = []
for value in tiled_order_raw.split(","):
    value = value.strip()
    if value.isdigit():
        tiled_order.append(int(value))

focused = None
focused_wid = wid("ENGINE_FOCUSED_WID")
focused_ws = os.environ.get("ENGINE_FOCUSED_WS", "")
if focused_wid is not None and focused_ws in {"w1", "1"}:
    focused = {
        "windowId": focused_wid,
        "workspace": "w1",
        "bundleId": os.environ.get("ENGINE_FOCUSED_BUNDLE", "NULL-APP-BUNDLE-ID") or "NULL-APP-BUNDLE-ID",
        "layout": layout_or_default(os.environ.get("ENGINE_FOCUSED_LAYOUT", "floating")),
        "title": os.environ.get("ENGINE_FOCUSED_TITLE", ""),
    }

argv = [value for value in argv_raw.splitlines() if value]

payload = {
    "callback": {
        "kind": callback_kind,
        "workspace": workspace or "w1",
        "argv": argv,
        "timestampMs": int(time.time() * 1000),
    },
    "workspaceState": {
        "workspace": workspace or "w1",
        "browser": state_browser if state_browser in {"zen", "safari", ""} else "",
        "upnoteTiled": state_upnote,
        "tiledOrder": tiled_order,
    },
    "focusedWindow": focused,
    "windows": windows,
    "guards": {
        "isHomeWorkspace": guard_home,
        "isManagedBundle": guard_managed,
        "isPopupIntent": guard_popup,
    },
}

if bundle:
    payload["callback"]["bundleId"] = bundle

print(json.dumps(payload))
PY
}

log_planner_actions() {
    local callback="$1"
    shift || true
    local context_json planner_out summary
    context_json="$(build_planner_context_json "$callback" "$@")"
    planner_out="$(run_planner "$context_json" || true)"
    if [[ -z "$planner_out" ]]; then
        log "engine_callback: planner unavailable callback=$callback"
        return 1
    fi

    summary="$(PLANNER_OUT="$planner_out" python3 - <<'PY' 2>/dev/null || true
import json
import os

raw = os.environ.get("PLANNER_OUT", "").strip()
if not raw:
    print("")
    raise SystemExit(0)

payload = json.loads(raw)
actions = payload.get("actions", [])
parts = []
for action in actions:
    parts.append(f'{action.get("order")}:{action.get("type")}:{action.get("target")}:{action.get("reason")}')
print(",".join(parts))
PY
)"
    if [[ -z "$summary" ]]; then
        local raw_compact
        raw_compact="$(printf "%s" "$planner_out" | tr '\n' ' ' | cut -c1-240)"
        log "engine_callback: planner output parse failed callback=$callback raw=$raw_compact"
        return 1
    fi

    log "engine_callback: planner callback=$callback actions=$summary"
}

execute_promoted_callback_script() {
    local callback="$1"
    shift || true
    "$AEROSPACE_DIR/callbacks/${callback}.sh" "$@"
}

run_shadow_mode() {
    local callback="$1"
    shift || true
    log_planner_actions "$callback" "$@" || true
    return 1
}

run_active_mode() {
    local callback="$1"
    shift || true
    if ! is_promoted_callback "$callback"; then
        log "engine_callback: active callback=$callback not promoted yet"
        return 1
    fi
    log_planner_actions "$callback" "$@" || true
    execute_promoted_callback_script "$callback" "$@"
}

case "$MODE" in
    ts-shadow)
        run_shadow_mode "$CALLBACK" "$@"
        ;;
    ts-active)
        run_active_mode "$CALLBACK" "$@"
        ;;
    *)
        log "engine_callback: unsupported mode '$MODE'"
        exit 1
        ;;
esac
