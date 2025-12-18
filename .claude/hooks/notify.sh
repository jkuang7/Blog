#!/bin/bash
set -euo pipefail

# Only read stdin if something is actually piped in
if [ -t 0 ]; then
  JSON_INPUT=""
else
  JSON_INPUT=$(cat)
fi

MESSAGE="Task completed!"

afplay /System/Library/Sounds/Pop.aiff

echo -e "\a"
echo "[$(date)] Task completed"