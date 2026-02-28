# Global Agent Standards (`~/Dev`)

These rules apply to all projects under `/Users/jian/Dev` unless a deeper `AGENTS.md` overrides them.

## Priority Order (Always Apply)

1. Reproduce and debug via visible behavior/output first (prefer MCP Playwright headless when applicable).
2. Ship regression protection (automated tests) before relying on docs or notes.
3. Record only non-testable residual knowledge in project memory.

## 1) Project Memory (`.memory/lessons.md`) — Non-testable Knowledge Only

Purpose:

- `lessons.md` stores ONLY knowledge regression tests cannot encode:
  constraints, failure signatures, rationale/tradeoffs, safe-change playbooks, platform/tooling quirks.
- If something can be captured by a regression test, write/update the test instead of expanding `lessons.md`.
- It is NOT a changelog and NOT a test index.
- Keep it compact and DRY: merge repeated guidance into thematic sections instead of incident-by-incident notes.

Task start:

- Load `<project-root>/.memory/lessons.md` at the beginning of the task (if present).
- If new validated evidence conflicts with older lessons, treat new evidence as source of truth and update the relevant theme.

Create if missing (use exactly this template):

```md
# Lessons (Non-testable Field Guide)

Rules:
- No per-bug diary entries.
- No “Regression test:” sections.
- No long narratives. Tight bullets only.
- Prefer themes over dates.
- Keep sections DRY: update existing themes instead of creating duplicates.
- If a lesson becomes testable later, move protection into regression tests and keep only non-testable context.

## Editor interactions (MDXEditor/Lexical/CodeMirror)
- Scope:
- Constraints:
- Failure signatures:
- Anti-patterns:
- Safe change strategy:
- When to write a new regression test:

## Window sizing + layout contracts (Tauri)
- Scope:
- Constraints:
- Failure signatures:
- Anti-patterns:
- Safe change strategy:
- When to write a new regression test:

## Defaults + migrations + persistence
- Scope:
- Constraints:
- Failure signatures:
- Anti-patterns:
- Safe change strategy:
- When to write a new regression test:
```

## 2) Verify Before Claiming Fixed

Required loop:

1. Reproduce with a concrete signal.
2. Form a specific hypothesis.
3. Apply the smallest valid fix.
4. Re-run the same reproduction path.
5. Run targeted regression checks.
6. Repeat until verified.

Handoff rule:

- Do not mark fixed without post-fix execution evidence.
- State exactly what was verified and how.

## 3) Automated Verification for Behavior + Regressions

For user-visible behavior/output, prefer automated checks first:

- Default to MCP Playwright (headless) for reproduction and verification when behavior is visible in UI/output.
- Capture concrete before/after signals (assertions, snapshots, logs, DOM state, network evidence) in that path.
- If unavailable, use the best alternative (tests, CLI checks, logs) and state the gap.
- Manual-only verification is last resort and must be labeled as such.

For bug fixes:

- Regression tests are the primary artifact; docs alone are insufficient for regression prevention.
- Add or update automated regression tests (failing before, passing after).
- Eliminate, rewrite, or replace redundant/out-of-date tests when current product behavior/context has changed and a better test exists.
- Avoid overlapping assertions that validate the same behavior at the same layer without added signal.
- Keep tests at the closest affected layer (unit/integration/e2e).
- Run the relevant suite before handoff.
- If a test cannot be added, document why and record follow-up in `.memory/lessons.md`.

## 4) Library Adoption Research (Before Install + Use)

Before installing and using a library (especially unfamiliar/esoteric):

- Research official repo + docs first.
- Inspect real usage examples (`examples/`, `src/`, tests, sample apps, README snippets).
- Prefer upstream-proven patterns over ad-hoc guesses.
- If docs are thin, read relevant source to infer intended usage.
- Summarize key usage patterns/constraints in your implementation plan.

## 5) Refactoring with Git History

Before and during refactors:

- Refactor toward reusability: identify repeated patterns, simplify call paths, and extract/abstract functions where it reduces complexity without changing behavior.
- Review relevant git history (`git log`, `git show`, `git blame`) for files/modules being changed to recover proven implementation context.
- Prefer extending previously successful patterns over reinventing new approaches that increase regression risk.
- If prior commits/reverts show known failure modes, document and avoid repeating them.
- Run relevant tests before and after refactors to prove no behavioral regressions.
