# Agent Harness Doctrine

Use this as the shared testing and verification policy for Codex work. Commands should point here instead of carrying their own competing test/debug workflows.

## Core Rule

Inspect existing context first, prove behavior on the closest reliable surface, then preserve only the verification that is worth keeping.

- Before building, search for shared utilities, local patterns, prior implementations, and reusable harnesses that can be reused or shaped for the task.
- Prefer adapting existing code paths over creating parallel helpers. New abstractions need an explicit reason.
- Prefer live app, browser, CLI, API, logs, traces, or controller state over static inspection when the behavior can be exercised.
- Use tests as durable memory, not as ritual. Add tests when the behavior is stable, likely to regress, and cheaper to check automatically than to rediscover manually.
- A flaky proof is a harness defect, not acceptance evidence.
- Keep raw observations separate from interpretation so later sessions can re-evaluate the evidence.

## Context and Reuse Card

For non-trivial plans, implementations, and reviews, record:

- `run-flow`: existing command, UI, API, CLI, controller, or workflow inspected before changes.
- `reuse scan`: shared utilities, components, scripts, schemas, fixtures, and similar code checked.
- `reuse decision`: reused, adapted, or intentionally new, with the reason.
- `proof plan`: proof class, baseline signal, confounding variables, and expected acceptance signal.
- `guard decision`: whether to add a durable test or harness, and why it is worth keeping.
- `cleanup`: generated artifacts, restore bundles, logs, screenshots, and local state kept or removed.

## Proof Classes

Record one of these proof classes in tickets, phase comments, and review summaries:

- `live`: exercised the real UI, app, CLI, API, or controller path.
- `harness`: replayed a deterministic repo-owned scenario, fixture, or workflow script.
- `unit`: covered stable pure logic or a narrow contract.
- `static`: inspected code/config only; acceptable only when a stronger proof is unavailable or unnecessary.
- `blocked`: proof cannot run because of credentials, unsafe state, missing dependencies, or external instability.

## Anti-Flake Policy

Avoid adding or accepting checks that depend on:

- arbitrary sleeps instead of event/state waits
- broad screenshot diffs for volatile full pages
- live third-party services in default gates
- unseeded time, randomness, or generated identifiers
- selectors tied to incidental DOM structure or styling
- generated resources without cleanup
- broad E2E in the normal `verify` gate before it has proven stable

Prefer semantic selectors, fixture-backed contract routes, DOM geometry for visual contracts, deterministic local services, and explicit cleanup.

## Verification Budget

- Low risk: one targeted live check or narrow deterministic command.
- Medium risk: add one focused harness, contract check, or regression guard for the risky slice.
- High risk or commit-ready shared infrastructure: run the repo's full deterministic gate plus the highest-signal live proof.

Do not stack redundant checks when one high-signal proof already covers the changed contract.

## Durable Guard Decision

After proving behavior, keep a regression guard only when all are true:

1. The behavior has a stable contract.
2. The failure is likely to recur or expensive to debug manually.
3. The check is deterministic enough to trust.
4. The check is narrower than the manual workflow it replaces.

Otherwise record the evidence in the ticket or handoff and avoid adding low-signal tests.

## Cleanup Policy

- Treat repeated generated files, duplicate helpers, stale plans, old workflow commands, and broad brittle tests as harness debt.
- Delete or archive obsolete workflow surfaces once a cleaner source of truth exists.
- Keep durable scenario definitions, fixtures, and replay scripts only when they encode proven behavior.
- Keep disposable logs, screenshots, browser profiles, mirrors, restore bundles, and debug captures local-only and prune them after the recovery window.
