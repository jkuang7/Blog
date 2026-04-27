#!/bin/zsh

set -euo pipefail

PROFILE_DIR="${HOME}/.codex/playwright-cdp-chrome-profile"
DEBUG_PORT="${PLAYWRIGHT_CHROME_DEBUG_PORT:-9222}"

mkdir -p "$PROFILE_DIR"

open -na "Google Chrome" --args \
  --remote-debugging-port="$DEBUG_PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  about:blank
