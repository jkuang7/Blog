#!/bin/bash
set -euo pipefail

source "/Users/jian/Dev/configs/aerospace/lib.sh"

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

echo "PASS: unit normalization logic"
