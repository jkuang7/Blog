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
- Do not default to `pnpm run verify` during normal iteration.
- Default verification budget:
  - low-risk iteration: targeted lint, one changed-surface live check, or one narrow repo-local command for the touched slice
  - medium-risk or shared-owner changes: add one focused harness, test, or probe for the risky slice
  - commit-ready or high-risk changes: run the full gate that matches the repo contract

## Live Verification Contract

- For commit-ready or high-risk work, `pnpm run verify` is the full gate, but it is not, by itself, proof that the feature works.
- While iterating, prefer the smallest high-signal proof instead of stacking the full gate plus redundant route/unit/E2E checks.
- Before marking implementation complete, exercise the changed behavior on the most relevant live surface available:
  - browser automation for UI flows
  - direct CLI or API invocation for non-UI behavior
  - runtime inspection via logs or other observable signals when effects are indirect
- If live verification is difficult, make the change easier to observe or test first, or validate the risky slice with temporary probe code before integrating the final solution.
- Keep iterating until the behavior is observably correct or a real blocker remains.

## Test Policy

- Unit tests should be co-located with the source modules they protect when they add clear value.
- New source modules do not automatically require tests. Add coverage when the module owns non-trivial logic, a stable contract, or a regression worth keeping cheap.
- Integration and E2E tests belong in dedicated roots from `harness.config.json`.
- Legacy centralized unit tests are allowed only until touched.

## Guardrails

- Architecture and safety rules are non-negotiable; do not add debt for them.
- Maintainability debt is tracked only in `.lint-debt.json` and must never worsen.
