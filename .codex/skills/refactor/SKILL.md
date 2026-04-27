---
name: refactor
description: Verification-first refactor workflow for software projects. Use when users ask to refactor, clean up architecture, reduce duplication, extract reusable modules/components, improve maintainability, or safely remove legacy code with zero behavior regression. Especially relevant for codebases using Zustand + Zod, while still adapting to whichever stack the repo already uses.
---

# Refactor

Drive incremental, test-backed refactors that improve maintainability and reuse without changing behavior.

Primary principle:
- Bake reusability into production code (shared modules, utilities, components, adapters, and clear interfaces), not into documentation artifacts.
- Default to code changes only; skip documentation updates unless explicitly requested.

## Quick Start

1. Detect stack/tooling from the target repo before proposing changes.
2. Run baseline verification commands and capture pass/fail signals.
3. If tests are weak, add a minimal regression harness first.
4. Execute refactor slices directly (small, reversible, behavior-safe).
5. Summarize results concisely in chat.

## Output Mode (Default)

1. Be execution-first, not report-first.
2. Do not create formal refactor reports or `refactor.md` files unless the user explicitly asks.
3. Keep summaries short: what changed, why, and what verification passed.
4. Use `references/refactor-template.md` only when a formal report/template is explicitly requested.
5. Do not update README/docs/changelogs as part of refactor work unless explicitly requested.

## Workflow

### 1) Establish Regression Baseline (Required)

1. Identify existing verification suites (typecheck, lint, unit, integration, e2e, build).
2. Run applicable commands from the target repo root.
3. Record exact commands + outcomes.
4. Treat missing tooling as a blocker for structural refactor unless a minimal harness is added.

### 2) Snapshot Real Repo Context

1. Verify actual UI/state/test stack from manifests and imports.
2. Identify hotspots with evidence (large modules, duplication, tangled dependencies, dead code).
3. Avoid assumptions (especially framework/state-library assumptions).

### 3) Define Small, Reversible Refactor Slices

1. Pick 1-3 focus areas only when evidence supports them:
   - UI/UX consistency + reusability
   - state management + correctness
   - deprecation/extraction/cleanup
2. For each change: define problem, code-level reusable abstraction, risk, coverage plan, commands, and affected files.

### 4) Enforce State Integrity Checks

1. Audit mutation/deep-clone risk areas.
2. Encode invariants in tests:
   - previous state is not mutated
   - changed branch has new reference
   - untouched branches retain reference
3. Add edge-case tests for missing IDs, empty collections, partial data, and order-of-ops issues.

### 5) Ship by Milestones

1. M0: baseline tests/gates.
2. M1: new abstractions behind compatibility wrappers.
3. M2: incremental migrations.
4. M3: safe removals after proof + coverage.

End each milestone with a green verification run.

## Zustand + Zod Defaults

If the repo already uses these:

1. Keep canonical state minimal; derive views via selectors.
2. Validate boundaries with Zod (I/O, persistence, network payloads).
3. Prevent state leaks with explicit reset/cleanup transitions.
4. Keep selector outputs referentially stable when inputs are unchanged.

If the repo does not use these, do not force adoption during a refactor-only task unless the user explicitly requests migration.

## Hard Constraints

1. No rewrite without compatibility path.
2. No deletion without proof of non-usage.
3. Any behavior change must be explicit + tested.
4. Any state-update logic change must add at least one mutation-detection regression test.
5. Do not generate a formal refactor report by default.
6. Reusability must be implemented in code changes, not only documented as guidance.
7. Do not perform documentation updates during refactors unless explicitly requested.

## Reference

Use `references/refactor-template.md` as the canonical long-form template/checklist.
