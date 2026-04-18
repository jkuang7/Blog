# Global Agent Standards (`~/Dev`)

Applies to everything under `/Users/jian/Dev` unless a deeper `AGENTS.md` overrides it.

## Keep This File Lean

- Keep only rules that materially change behavior before implementation, verification, or commit.
- Prefer deeper `AGENTS.md` files for repo-specific policy.

## Workspace Ownership

- `workspace/*` is owned by the parent `Dev` repo.
- Do not create nested `.git` repos under `workspace/`.
- Work directly on `main` or the branch already checked out by default; do not require a separate worktree unless the user explicitly asks for one.

## Task Start

- Be context-driven, not optimistic.
- When a user asks to fix something, gather relevant repo/runtime context first before proposing or making changes.
- If a repo defines an LLM or harness contract, follow its load order exactly.
- Reuse an already-running app, browser, or service when it helps verify the real surface.

## Verify, Then Claim

- Treat implementation as provisional until the intended behavior is observed on the right live surface.
- Use a risk-based verification budget instead of defaulting to every available check on every iteration:
  - low risk or exploratory iteration: reproduce the changed surface live and run the smallest focused check that proves the change
  - medium risk: add one targeted harness, test, or narrow automation pass for the risky slice
  - high risk, shared-infra, or commit-ready work: run the broader repo gate that matches the repo contract
- Avoid stacking redundant checks. When one high-signal live verification or one narrow harness already proves the change, do not automatically add the full suite on top during iteration.
- For debugging, do not present a root-cause explanation as likely unless it has been tested against the live surface; explicitly label unverified explanations as hypotheses.
- Before changing code for a bug, separate observed facts, unknowns, and hypotheses; gather at least one discriminating observation that rules a cause in or out.
- If multiple causes are plausible, add the smallest instrumentation or state inspection needed to identify the failing transition before fixing.
- If the bug may involve platform behavior, a third-party app, or an upstream tool limitation, do targeted official/upstream research before broad local fixes or brute-force workarounds.
- For layout, focus, tab, selection, resizing, and navigation bugs, trace the transition path and inspect both logical state and rendered layout state.
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
- Do not add regression coverage by default. Add a targeted regression only when the issue is likely to recur, the contract is stable, and the check will be cheaper than repeated manual repro.
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
  - temporary restore or archive bundles in `/Users/jian/Dev/.restore/`, `/Users/jian/Dev/.restore-backups/`, or `/Users/jian/Dev/.repo-archives/`
- Do not commit generated artifacts, logs, caches, runtime databases, restore bundles, or machine-local snapshots.
- Prune disposable browser/debug output and stale restore or archive material once the recovery window is no longer useful.
- Before deleting restore or archive material, make sure it is not the last practical rollback path for a recent structural change.

## Stitch-First Visual Work

- For UI-affecting work that materially changes layout, hierarchy, spacing, theming, responsiveness, modal/page composition, or reusable visual grammar, start via `/ui` instead of freestyling code first.
- Treat `/ui` as the required trigger for new shared visual patterns, major shared-owner changes, and missing visual states that need a real baseline before implementation.
- Copy-only changes, logic-only UI fixes, and structure-preserving visual repairs with an already-known target may skip `/ui`, but still need live verification when the surface is risky.
- Keep detailed Stitch workflow, prompts, and artifact rules in the skill, not here.
