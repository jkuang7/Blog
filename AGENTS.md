# Global Agent Standards (`~/Dev`)

Applies to everything under `/Users/jian/Dev` unless a deeper `AGENTS.md` overrides it.

## Priority

1. Reproduce from real behavior first.
2. Prefer the smallest valid change.
3. Add regression protection before trusting notes or memory.
4. Keep only non-testable residual knowledge in `.memory/lessons.md`.

## Keep This File Token-Efficient

- Do not restate rules already enforced by repo lint/build hooks.
- Assume repos using `eslint-config-jian` already enforce maintainability limits, promise safety, cycle checks, feature/UI boundaries, and no focused tests.
- Only keep guidance here for contracts agents should satisfy before burning a full lint/verify pass.

## Task Start

- Read the nearest project `AGENTS.md` first if one exists.
- Load `<project-root>/.memory/lessons.md` if present.
- Only create `.memory/lessons.md` for projects rooted under `/Users/jian/Dev/Repos/<project>*`.
- If the repo defines an LLM or harness contract, follow that loading order exactly.

## Default Definition Of Done

- **Signal:** concrete repro evidence, or explicit “not reproducible” with reason.
- **Fix:** smallest valid change.
- **Regression:** automated fail-before/pass-after, or explicit why not.
- **Verify:** exact commands run and pass evidence.
- **Gaps:** remaining risks called out.

## Required Handoff

- **Signal:** …
- **Hypothesis:** …
- **Change:** …
- **Verification:** …
- **Regression:** …

## Non-Lint Contracts To Respect Early

- Co-locate unit tests with touched source modules when the repo uses colocated unit tests.
- Keep integration and e2e tests in the repo’s dedicated test roots.
- When a repo has generated context, harness manifests, or structure checks, update them as required by that repo instead of leaving it for the end.
- Preserve observable behavior during refactors unless the user explicitly asks for behavior change.

## Verify Before Claiming Fixed

Loop: reproduce -> hypothesis -> smallest fix -> rerun same repro -> run targeted regressions.

Never mark fixed without post-fix execution evidence.

## `.memory/lessons.md`

Use only for non-testable knowledge:

- constraints
- failure signatures
- rationale and tradeoffs
- safe-change playbooks
- tooling quirks

Rules:

- Not a changelog, bug diary, or test index.
- Keep it DRY and current.
- Do not create new `.memory/lessons.md` files outside `/Users/jian/Dev/Repos/<project>*`.
- If something becomes testable, move it to tests.

## Refactors

- No behavior change unless requested.
- Use git history when needed to avoid known failure modes.
- Slice work and verify each slice.

## React Design

- Prefer small, cohesive feature components over god components.
- Keep pure render pieces separate from stateful orchestration when that split improves clarity.
- Preserve stable naming, file ordering, and grep-friendly exports/test IDs.
