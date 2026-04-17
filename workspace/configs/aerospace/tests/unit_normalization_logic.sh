#!/bin/bash
set -euo pipefail

source "/Users/jian/Dev/workspace/configs/aerospace/lib.sh"

fail() {
    echo "FAIL: $1"
    exit 1
}

assert_eq() {
    local actual="$1"
    local expected="$2"
    local msg="$3"
    if [[ "$actual" != "$expected" ]]; then
        echo "Expected: [$expected]"
        echo "Actual:   [$actual]"
        fail "$msg"
    fi
}

assert_true() {
    local msg="$1"
    shift
    if ! "$@"; then
        fail "$msg"
    fi
}

assert_false() {
    local msg="$1"
    shift
    if "$@"; then
        fail "$msg"
    fi
}

# --- popup detection ---
assert_true "sign-in title should be popup" is_popup_title "Sign in required"
assert_true "oauth title should be popup" is_popup_title "OAuth permission"
assert_false "normal browsing title should not be popup" is_popup_title "Start Page"

# --- contender/main-intent rebuild decision ---
if ! should_allow_browser_snapshot_rebuild "com.apple.Safari" "app.zen-browser.zen" "true" "com.microsoft.VSCode" "false" "false"; then
    fail "contender browser with non-popup window must trigger rebuild"
fi

if should_allow_browser_snapshot_rebuild "com.apple.Safari" "app.zen-browser.zen" "false" "com.microsoft.VSCode" "false" "false"; then
    fail "contender browser without non-popup evidence should not force rebuild"
fi

if ! should_allow_browser_snapshot_rebuild "com.apple.Safari" "com.apple.Safari" "false" "com.apple.Safari" "false" "false"; then
    fail "active browser main-window intent should allow rebuild"
fi

if should_allow_browser_snapshot_rebuild "com.apple.Safari" "com.apple.Safari" "false" "com.apple.Safari" "true" "true"; then
    fail "popup-only focused/browser intent should not rebuild"
fi

# --- overlay normalization ---
WINDOW_LINES=$'1|com.apple.Safari|floating\n2|app.zen-browser.zen|floating\n3|com.openai.codex|floating\n4|com.microsoft.VSCode|h_tiles\n5|com.google.Chrome|floating\n'

OUT_SAFARI="$(printf '%s' "$WINDOW_LINES" | filter_overlay_candidates_from_lines "com.apple.Safari" | sort)"
EXPECTED_SAFARI=$'1|com.apple.Safari\n3|com.openai.codex'
assert_eq "$OUT_SAFARI" "$EXPECTED_SAFARI" "overlay normalization for active Safari"

OUT_ZEN="$(printf '%s' "$WINDOW_LINES" | filter_overlay_candidates_from_lines "app.zen-browser.zen" | sort)"
EXPECTED_ZEN=$'2|app.zen-browser.zen\n3|com.openai.codex'
assert_eq "$OUT_ZEN" "$EXPECTED_ZEN" "overlay normalization for active Zen"

OUT_EMPTY="$(printf '%s' "$WINDOW_LINES" | filter_overlay_candidates_from_lines "" | sort)"
EXPECTED_EMPTY=$'3|com.openai.codex'
assert_eq "$OUT_EMPTY" "$EXPECTED_EMPTY" "overlay normalization with no active browser"

# Core windows that are part of normalized core order must be excluded from
# floating overlay restore candidates.
OUT_EXCLUDED="$(printf '%s' "$WINDOW_LINES" | filter_overlay_candidates_from_lines "com.apple.Safari" "3" | sort)"
EXPECTED_EXCLUDED=$'1|com.apple.Safari'
assert_eq "$OUT_EXCLUDED" "$EXPECTED_EXCLUDED" "overlay normalization excludes core-id windows"

# Second-pass subtraction should remove any core-tiled ids that slipped into
# overlay candidates and also remove competing browser overlays.
OVERLAY_LINES=$'1|com.apple.Safari\n2|app.zen-browser.zen\n3|com.openai.codex\n4|com.microsoft.VSCode\n'
WS_SNAPSHOT=$'1|com.apple.Safari|floating\n2|app.zen-browser.zen|floating\n3|com.openai.codex|h_tiles\n4|com.microsoft.VSCode|floating\n'
SUB_OUT="$(subtract_core_tiles_from_overlay_lines "$OVERLAY_LINES" "3,999" "$WS_SNAPSHOT" "com.apple.Safari" | sort)"
SUB_EXPECTED=$'1|com.apple.Safari\n4|com.microsoft.VSCode'
assert_eq "$SUB_OUT" "$SUB_EXPECTED" "second-pass subtraction removes core tiles and competing browser overlays"

# Force rebuilds should untile the whole workspace first, not just managed apps.
UNTILE_LINES=$'10|com.microsoft.VSCode\n11|NULL-APP-BUNDLE-ID\n12|com.cmuxterm.app\n10|com.microsoft.VSCode\n'
UNTILE_OUT="$(printf '%s' "$UNTILE_LINES" | workspace_untile_ids_from_lines)"
UNTILE_EXPECTED=$'10\n11\n12'
assert_eq "$UNTILE_OUT" "$UNTILE_EXPECTED" "workspace untile helper includes all workspace windows exactly once"

ORDER_WITH_BROWSER_CAP="$(build_tiled_slot_order_csv "10" "20" "30" "50")"
assert_eq "$ORDER_WITH_BROWSER_CAP" "10,20,50" "browser takes the far-right slot when UpNote and VSCode already occupy the left slots"

ORDER_WITHOUT_BROWSER="$(build_tiled_slot_order_csv "10" "20" "30" "")"
assert_eq "$ORDER_WITHOUT_BROWSER" "10,20,30" "utility uses the right slot when browser is absent"

ORDER_STANDARD_THREE="$(build_tiled_slot_order_csv "" "20" "30" "50")"
assert_eq "$ORDER_STANDARD_THREE" "20,30,50" "standard three-slot order keeps VSCode left, utility middle, browser right"

assert_true "focused non-popup Terminal with a different window id should promote" \
    should_promote_focused_terminal_window "com.cmuxterm.app" "42" "false" "com.cmuxterm.app" "40"
assert_false "focused Terminal should not promote when already the active owner" \
    should_promote_focused_terminal_window "com.cmuxterm.app" "40" "false" "com.cmuxterm.app" "40"
assert_false "popup-like Terminal windows should not promote" \
    should_promote_focused_terminal_window "com.cmuxterm.app" "42" "true" "com.cmuxterm.app" "40"
assert_false "non-Terminal utilities should remain sticky-owner" \
    should_promote_focused_terminal_window "com.openai.codex" "42" "false" "com.cmuxterm.app" "40"

ORIGINAL_WINDOW_IS_ON_SCREEN="$(declare -f window_is_on_screen)"
window_is_on_screen() {
    case "$1" in
        41|42|43) return 0 ;;
        *) return 1 ;;
    esac
}

STATE_ACTIVE_UTILITY_BUNDLE="com.cmuxterm.app"
STATE_ACTIVE_UTILITY_WID="40"
SNAPSHOT=$'20|com.microsoft.VSCode|h_tiles|Code\n40|com.cmuxterm.app|h_tiles|Old Terminal\n42|com.cmuxterm.app|floating|Visible Terminal\n50|app.zen-browser.zen|h_tiles|Zen\n'
RESOLVED_UTILITY="$(resolve_active_utility_window "$SNAPSHOT")"
assert_eq "$RESOLVED_UTILITY" "com.cmuxterm.app|40" "stored tiled utility owner remains authoritative while it still exists"

STATE_ACTIVE_UTILITY_BUNDLE="com.cmuxterm.app"
STATE_ACTIVE_UTILITY_WID="999"
FALLBACK_SNAPSHOT=$'20|com.microsoft.VSCode|h_tiles|Code\n41|com.openai.codex|floating|Codex\n43|com.tdesktop.Telegram|floating|Telegram\n50|app.zen-browser.zen|h_tiles|Zen\n'
FALLBACK_UTILITY="$(resolve_active_utility_window "$FALLBACK_SNAPSHOT")"
assert_eq "$FALLBACK_UTILITY" "com.openai.codex|41" "utility resolution respects utility priority across visible utility apps"

window_is_on_screen() { return 1; }
LATEST_NONVISIBLE_SNAPSHOT=$'20|com.microsoft.VSCode|h_tiles|Code\n41|com.openai.codex|floating|Codex\n50|app.zen-browser.zen|h_tiles|Zen\n'
LATEST_NONVISIBLE_UTILITY="$(resolve_active_utility_window "$LATEST_NONVISIBLE_SNAPSHOT")"
assert_eq "$LATEST_NONVISIBLE_UTILITY" "com.openai.codex|41" "utility resolution falls back to latest non-popup utility when none are on-screen"

eval "$ORIGINAL_WINDOW_IS_ON_SCREEN"

echo "PASS: unit normalization logic"
