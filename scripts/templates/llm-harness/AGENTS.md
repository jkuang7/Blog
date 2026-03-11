# Repo Agent Harness

This repository uses the LLM harness contract.

## Required Context Load

At task start, read these files in order:

1. `harness.config.json`
2. `docs/llm/golden-path.md`
3. `.codex/context-pack.md`
4. `.lint-debt.json` (if present)

If generated context pack is stale, run `pnpm run context:pack`.

## Quality Gate Contract

- `pnpm run lint` is ESLint-only.
- Full gate is `pnpm run verify`:
  - `pnpm run lint:harness`
  - `pnpm run lint:structure`
  - `pnpm run tests:changed`
  - `pnpm run context:check`
  - `pnpm run typecheck`
  - `pnpm run test:unit`

## Test Policy

- Unit tests must be co-located with touched source modules.
- Integration and E2E tests belong in dedicated roots from `harness.config.json`.
- Legacy centralized unit tests are allowed only until touched.

## Guardrails

- Architecture and safety rules are non-negotiable; do not add debt for them.
- Maintainability debt is tracked only in `.lint-debt.json` and must never worsen.
