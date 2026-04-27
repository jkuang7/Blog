# Global Agent Standards (`~/Dev`)

Applies to everything under `/Users/jian/Dev` unless a deeper `AGENTS.md` overrides it.

## Keep This File Lean

- Keep only rules that materially change behavior before implementation, verification, or commit.
- Prefer deeper `AGENTS.md` files for repo-specific policy.

## Workspace Ownership

- `workspace/*` is owned by the parent `Dev` repo.
- Do not create nested `.git` repos under `workspace/`.
- Use the canonical repo checkout by default.
- Work directly on `main` unless the repo is already on an intentional branch for the current task.
- Do not create, require, or plan around separate worktrees unless the user explicitly asks for one.

## Task Start

- Be context-driven, not optimistic.
- When a user asks to fix something, gather relevant repo/runtime context first before proposing or making changes.
- Reuse an already-running app, browser, or service when it helps verify the real surface.
- Plan for proof, not just implementation. Before changing code, think through how the behavior will be verified, how failures will be localized, and what structure will make later changes safer.

## Scientific Method

- Treat planning and implementation as an evidence-driven process, not optimistic coding.
- Define the intended outcome before changing code, then choose the smallest path that can prove or disprove a hypothesis.
- Isolate confounding variables before broad edits. Prefer verifiable seams, deterministic evaluation paths, and explicit observability over intertwined changes that are hard to reason about.
- Design the work so future sessions can debug and extend it safely: make failure boundaries legible, separate experiment from codified behavior, and capture what was learned once it is proven.
- During planning, answer these questions explicitly when the task is non-trivial:
  - How will this be proven?
  - What are the confounding variables?
  - What part can be isolated first?
  - What realistic workflow needs to be replayed?
  - If this breaks later, how will the failure be observed and localized?
  - What can be captured now so future work is easier?
  - Once this is proven, how do we freeze it as a regression guard?

## Live-Test-First For Hard-To-Predict Work

- When behavior is difficult to predict from static reasoning alone, prefer controlled live-testing over speculative argument.
- Start with a control or baseline, then change one variable at a time so the effect can be interpreted cleanly.
- Keep these layers separate:
  - observation: what the live test actually did
  - interpretation: what we think the result means
  - conclusion: what rule, if any, should change
- Use reasoning to interpret observed results, not to replace them.
- Prefer shadow experiments before live policy changes for noisy, path-dependent, or high-variance systems.
- Use representative baskets or cohorts instead of deriving broad rules from one-off examples.
- Keep raw evidence separate from interpreted policy so later sessions can revisit the conclusion without losing the underlying signal.
- Promote a rule only after the live or shadow test improves a representative set, not just a single case.

## Verify, Then Claim

Shared harness policy lives in `/Users/jian/.codex/docs/agent-harness.md` and the tracked Dev copy at `/Users/jian/Dev/.codex/docs/agent-harness.md`. Use it for proof classes, anti-flake rules, and durable guard decisions.

- Treat implementation as provisional until the intended behavior is observed on the right live surface.
- Plans should include how behavior will be proven, not just what code will be written. Verification assets are part of the deliverable for risky work, not cleanup after implementation.
- Use a risk-based verification budget instead of defaulting to every available check on every iteration:
  - low risk or exploratory iteration: reproduce the changed surface live and run the smallest focused check that proves the change
  - medium risk: add one targeted harness, test, or narrow automation pass for the risky slice
  - high risk, shared-infra, or commit-ready work: run the broader repo gate that matches the repo contract
- Avoid stacking redundant checks. When one high-signal live verification or one narrow harness already proves the change, do not automatically add the full suite on top during iteration.
- Do not treat shallow mocks, stubs, or narrow unit tests as primary proof for complex, stateful, or environment-sensitive workflows. They can support the work, but realistic proof must exercise the real chain or a high-fidelity slice of it.
- For debugging, do not present a root-cause explanation as likely unless it has been tested against the live surface; explicitly label unverified explanations as hypotheses.
- Before changing code for a bug, separate observed facts, unknowns, and hypotheses; gather at least one discriminating observation that rules a cause in or out.
- If multiple causes are plausible, add the smallest instrumentation or state inspection needed to identify the failing transition before fixing.
- If the bug may involve platform behavior, a third-party app, or an upstream tool limitation, do targeted official/upstream research before broad local fixes or brute-force workarounds.
- For layout, focus, tab, selection, resizing, and navigation bugs, trace the transition path and inspect both logical state and rendered layout state.
- For fragile or high-risk workflows that are hard to prove in isolation, default to a replayable live verification asset: a live script, scenario runner, workflow harness, frozen snapshot, or equivalent mechanism that walks the real flow and can be rerun later.
- Once a fragile flow is proven, preserve that proof as a reusable regression guard. Prefer a durable, high-signal workflow check over rediscovering the behavior from scratch in a later session.
- Prefer the highest-signal verification surface available:
  - app or runtime behavior
  - browser automation for UI flows
  - CLI or API invocation for non-UI behavior
  - logs, traces, or metrics when the effect is indirect but observable
  - repo-specific harnesses or tests
- Reserve full `verify`, `precommit`, or broad unit/E2E/typecheck runs for:
  - commit-ready work
  - shared contracts, generators, harnesses, or dependency changes
  - cases where targeted verification is unavailable or insufficient
- Passing tests alone is not enough when a live surface can be exercised.
- For uncertain fixes, validate the risky slice first, then reintegrate the final change.
- Do not claim a fix unless the failing repro was observed, the cause was evidenced by a targeted check, and the repro passes after the change; otherwise describe it as a hypothesis, mitigation, or likely fix.
- Do not add low-signal regression coverage by default. Add a targeted regression when the issue is likely to recur, the contract is stable, and the check will be cheaper than repeated manual repro. For fragile flows, prefer a reusable live or high-fidelity regression guard once the workflow is proven.
- Treat flaky checks as harness defects or follow-up work, not acceptance evidence.
- Delete dummy resources created during testing once no longer needed unless the user wants them kept.

## Commit Requests

- When the user says `commit`, promote only the conversation-relevant changes onto `main`.
- Prefer cherry-picking or replaying the intended changes; do not blindly merge unrelated branch state.
- Resolve conflicts using conversation intent and current `main` behavior.
- After promotion, leave the repo in a clean ownership state on `main` or the branch already in use for the task.
- Do not disturb unrelated branches, stashes, worktrees, or local resource directories unless explicitly asked.

## Artifact Hygiene

- Keep generated artifacts in local-only homes:
  - browser and Playwright debug output in `/Users/jian/Dev/.playwright-mcp/`
  - Codex machine-local state in `/Users/jian/Dev/.codex/`
- Tracked verification assets are allowed when they intentionally encode proven behavior for future replay. Keep the durable harness or scenario definition; keep disposable logs, raw captures, and ad hoc debug output local-only.
- Do not commit generated artifacts, logs, caches, runtime databases, restore bundles, or machine-local snapshots.
- Prune disposable browser/debug output and stale restore or archive material once the recovery window is no longer useful.
- Before deleting restore or archive material, make sure it is not the last practical rollback path for a recent structural change.

## Stitch-First Visual Work

- For UI-affecting work that materially changes layout, hierarchy, spacing, theming, responsiveness, modal/page composition, or reusable visual grammar, start via `/ui` instead of freestyling code first.
- Treat `/ui` as the required trigger for new shared visual patterns, major shared-owner changes, and missing visual states that need a real baseline before implementation.
- Copy-only changes, logic-only UI fixes, and structure-preserving visual repairs with an already-known target may skip `/ui`, but still need live verification when the surface is risky.
- Keep detailed Stitch workflow, prompts, and artifact rules in the skill, not here.
