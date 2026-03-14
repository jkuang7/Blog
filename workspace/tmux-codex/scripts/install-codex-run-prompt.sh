#!/usr/bin/env bash
set -euo pipefail

REPO_HOME="${TMUX_CLI_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_DIR="$REPO_HOME/prompts"
DEST_DIR="$HOME/.codex/prompts"
mkdir -p "$DEST_DIR"

for legacy_prompt in run run_clear runner-cycle runner-discover runner-implement runner-verify runner-closeout; do
  rm -f "$DEST_DIR/$legacy_prompt.md"
done

for prompt_name in run_setup run_execute run_update add; do
  SRC="$SOURCE_DIR/$prompt_name.md"
  DEST="$DEST_DIR/$prompt_name.md"

  if [[ -e "$DEST" || -L "$DEST" ]]; then
    rm -f "$DEST"
  fi

  install -m 0644 "$SRC" "$DEST"
  echo "Installed prompt: $DEST <= $SRC"
done
