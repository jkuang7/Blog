# Refactor Prompt (Reusable Across `/Users/jian/Dev/Repos/*`)

Use this as a global Codex prompt for refactoring projects with zero behavior regression.

Core intent:
- Refactor for maintainability and reuse with zero behavior regression.
- No user-visible changes unless explicitly called out and verified.
- Every change must have objective verification signals (tests/build/e2e).

Default bias:
- If the repo uses `Zustand` and `Zod`, prefer:
  - state boundaries in store actions/selectors
  - schema validation at I/O boundaries
  - immutable updates + selector stability
- If they are not present, do not introduce them casually; adapt to existing stack.

---

## 1) Regression safety plan (must be first)

### Baseline verification commands

Run from `<TARGET_REPO_ROOT>`.

1. Detect actual tooling by inspecting manifests/configs.
2. Run project-standard commands.
3. Record command + status + key output.

Record in this table:

| Command | Purpose | Status (Pass/Fail) | Signal |
|---|---|---|---|
| `<exact command>` | `<what it proves>` | `<status>` | `<concise output summary>` |

Required probes (use real repo commands):
- typecheck
- lint
- unit tests
- integration tests (if present)
- e2e/playwright/cypress (if present)
- build (if applicable)

If not runnable:
- Record exact command attempted.
- Record exact blocker (missing tool/script/config).
- Add minimal regression harness before structural refactor.

Known flaky tests:
- List test path/name.
- Mark pre-existing flakes explicitly.
- Never hide new regressions inside flaky buckets.

### Required gates for every refactor PR
- typecheck
- lint
- unit tests
- integration tests (if present)
- e2e/playwright/cypress (if present)
- build (if applicable)

### No-regression rules
- No large rewrite without compatibility layer.
- No deletion without proof of non-usage.
- Any behavior change must be explicitly listed + tested.
- Any change to state update logic must include at least one test that would fail if previous state were mutated.

### Risk mitigation
- Use feature flags/compat wrappers for risky paths.
- Ship incremental, reversible milestones.
- Keep rollback per milestone (revert latest milestone only).

---

## 2) Context snapshot (what you observed)

Fill with evidence from the target repo.

- UI stack:
  - `<framework/runtime + app/component/layout structure>`
- Styling/theming:
  - `<tokens/theme/css strategy/breakpoints>`
- State approach:
  - `<zustand/redux/context/react-query/etc + validation strategy>`
- Test setup:
  - `<frameworks + command entrypoints + known gaps>`
- Hot spots (with evidence):
  - `<file path + repetition/risk + objective signal>`

Rules:
- Do not assume libraries/frameworks. Verify from manifests/imports.
- Use concrete files and command outputs.

---

## 3) Refactor goals (3–6 bullets)

Requirements:
- Each goal maps to an observed pain point.
- Behavior-preserving unless explicitly approved.

Template:
- `<goal tied to hotspot #1>`
- `<goal tied to hotspot #2>`
- `<goal tied to hotspot #3>`

---

## 4) Proposed changes (prioritized, small + reversible)

Pick 1–3 categories based on evidence:
- A) UI/UX consistency + reusability
- B) State management + correctness
- C) Deprecation + extraction + cleanup

For each proposed change:
- Problem:
  - `<specific issue + evidence>`
- Proposed change:
  - `<small reversible change>`
- Why it helps:
  - `<maintainability/reuse/correctness benefit>`
- Risk level (Low/Med/High):
  - `<risk>`
- Regression coverage plan:
  - existing coverage
  - tests to add (unit/integration/e2e)
  - manual checks (only if unavoidable)
- Verification commands to run (explicit):
  - `<exact commands>`
- Files/areas affected:
  - `<paths>`

Zustand + Zod preference (only if already in repo):
- Keep canonical state minimal; derive views via selectors.
- Validate external input/output with Zod schemas.
- Prevent state leaks between flows (explicit reset/cleanup rules).
- Test order-of-ops and transition correctness.

Execution guard:
- If coverage is insufficient, first change must add a minimal regression harness around the refactor surface.

---

## 5) Deprecation / removals plan (safe deletion checklist)

For each planned removal:
- Evidence it is unused:
  - reference search locations
  - runtime entrypoint checks
  - test coverage confirmation
- Replacement/migration steps:
  - `<who migrates, how, and in what order>`
- Removal milestone:
  - `<M2/M3>`

Deletion policy:
- Delete only after proof + green gates.
- Prefer deprecate-then-delete over immediate hard removal.

---

## 6) Milestones (each independently shippable)

- M0: Add/shore-up regression tests + baseline gates (no behavior change)
- M1: Introduce abstractions behind compatibility wrappers
- M2: Migrate call sites incrementally (old path still works)
- M3: Remove deprecated paths (after proof + test coverage)

Milestone exit rule:
- Every milestone ends with a green verification run.

---

## State Integrity: Mutations, Deep Cloning, and Nested Data (Required)

### Audit (evidence-based)

Identify where state is:
- mutated directly (in-place edits, push/splice, object field writes)
- shallow-copied but still nested (spread/object/array copies with nested references)
- deep-cloned (JSON stringify/parse, structuredClone, lodash cloneDeep, custom clone)
- derived from non-serializable objects (Date, Map/Set, class instances, functions)

List highest-risk state shapes:
- deeply nested objects
- arrays of objects with nested children
- normalized vs denormalized collections
- shared references across slices/stores/components

### Rules (no-regression invariants)

- Treat state as immutable at boundaries.
- No in-place mutation of existing references.
- Create new references for every modified path segment.
- Avoid blind deep cloning as default.
- Deep clone only when required and documented.
- Prefer structural sharing updates.
- Keep selector referential stability where render perf depends on it.

If using Zustand:
- Verify actions do not mutate state directly unless Immer is explicitly adopted.
- If Immer is used, document and apply it consistently.

### Recommended refactor tactics (choose based on repo reality)

- Normalize nested collections when it reduces update complexity (`byId/allIds`).
- Keep derived data in selectors, not persisted state.
- If nested updates are error-prone:
  - adopt Immer only with coverage and consistency rules, or
  - introduce focused immutable update helpers (`replaceById`, `updateAtPath`, etc.).
- Replace unsafe deep clone patterns:
  - `JSON.parse(JSON.stringify(x))` -> safer alternatives/shape fixes
  - blanket `cloneDeep` -> targeted copy-on-write updates

### Verification (must be test-driven)

For each high-risk state area, add tests that prove:
- no accidental mutation of previous state
- correct reference updates:
  - changed branch gets new reference
  - untouched branches keep same references
- selector stability for unchanged inputs (where needed)
- edge cases:
  - empty collections
  - missing IDs
  - partial data
  - concurrent updates/order-of-ops

Concrete techniques:
- deep-freeze previous state in tests (where available)
- reference assertions on changed vs unchanged branches
- property-based tests for widely reused update helpers

---

## Pairing with `/commit`

When used alongside commit-to-main/cherry-pick workflows:
- Require M0 baseline evidence before approving structural refactor commits.
- During conflicts, preserve behavior contracts proven by tests.
- If conflict intent is ambiguous, pause and ask instead of auto-resolving.
