#!/usr/bin/env bash
set -euo pipefail

REPO_HOME="${TMUX_CLI_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_DIR="$REPO_HOME/prompts"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
COMMAND_DIR="$CODEX_HOME_DIR/commands"
PROMPT_DIR="$CODEX_HOME_DIR/prompts"
mkdir -p "$COMMAND_DIR" "$PROMPT_DIR"

for legacy_prompt in \
  run \
  run_clear \
  run_update \
  runner-cycle \
  runner-discover \
  runner-implement \
  runner-verify \
  runner-closeout \
  runner_cycle \
  runner_discover \
  runner_implement \
  runner_verify \
  runner_closeout
do
  rm -f "$COMMAND_DIR/$legacy_prompt.md" "$PROMPT_DIR/$legacy_prompt.md"
done

for command_name in run_setup run_execute run_govern add; do
  SRC="$SOURCE_DIR/$command_name.md"
  COMMAND_DEST="$COMMAND_DIR/$command_name.md"
  PROMPT_DEST="$PROMPT_DIR/$command_name.md"

  if [[ -e "$COMMAND_DEST" || -L "$COMMAND_DEST" ]]; then
    rm -f "$COMMAND_DEST"
  fi
  ln -s "$SRC" "$COMMAND_DEST"
  echo "Installed command link: $COMMAND_DEST -> $SRC"

  if [[ -e "$PROMPT_DEST" || -L "$PROMPT_DEST" ]]; then
    rm -f "$PROMPT_DEST"
  fi
  ln -s "$SRC" "$PROMPT_DEST"
  echo "Installed compatibility prompt link: $PROMPT_DEST -> $SRC"
done

for alias_pair in "run-setup:run_setup" "run-execute:run_execute" "run-govern:run_govern" "runner-add:add"; do
  ALIAS_NAME="${alias_pair%%:*}"
  CANONICAL_NAME="${alias_pair##*:}"
  SRC="$SOURCE_DIR/$CANONICAL_NAME.md"
  COMMAND_DEST="$COMMAND_DIR/$ALIAS_NAME.md"
  PROMPT_DEST="$PROMPT_DIR/$ALIAS_NAME.md"

  if [[ -e "$COMMAND_DEST" || -L "$COMMAND_DEST" ]]; then
    rm -f "$COMMAND_DEST"
  fi
  ln -s "$SRC" "$COMMAND_DEST"
  echo "Installed alias command link: $COMMAND_DEST -> $SRC"

  if [[ -e "$PROMPT_DEST" || -L "$PROMPT_DEST" ]]; then
    rm -f "$PROMPT_DEST"
  fi
  ln -s "$SRC" "$PROMPT_DEST"
  echo "Installed alias compatibility prompt link: $PROMPT_DEST -> $SRC"
done
