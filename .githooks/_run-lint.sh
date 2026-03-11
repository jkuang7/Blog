#!/usr/bin/env bash

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

if [ ! -f package.json ]; then
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "pre-hook lint: node is required but not found." >&2
  exit 1
fi

if ! node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync('package.json','utf8'));process.exit(p?.scripts?.lint?0:1)" >/dev/null 2>&1; then
  exit 0
fi

echo "pre-hook lint: running lint in $repo_root"

if [ -f pnpm-lock.yaml ]; then
  if ! command -v pnpm >/dev/null 2>&1; then
    echo "pre-hook lint: pnpm-lock.yaml found but pnpm is not installed." >&2
    exit 1
  fi
  pnpm run lint
  exit 0
fi

if [ -f yarn.lock ]; then
  if ! command -v yarn >/dev/null 2>&1; then
    echo "pre-hook lint: yarn.lock found but yarn is not installed." >&2
    exit 1
  fi
  yarn lint
  exit 0
fi

if [ -f bun.lock ] || [ -f bun.lockb ]; then
  if ! command -v bun >/dev/null 2>&1; then
    echo "pre-hook lint: bun lockfile found but bun is not installed." >&2
    exit 1
  fi
  bun run lint
  exit 0
fi

npm run lint --if-present
