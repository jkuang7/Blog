---
name: commit-main
description: Use when the user asks to commit to main, merge to main, or cherry-pick to main in a git worktree, including resolving merge or cherry-pick conflicts by analyzing main behavior vs incoming architecture intent and integrating safely.
metadata:
  short-description: Commit/cherry-pick to main safely
---

# Commit Main

When the user asks to "commit to main", "merge to main", or "cherry-pick to main", follow this workflow.

## 1) Preflight

1. Create a temporary `codex/...` branch in the current worktree.
2. Build the intended change set before staging:
   - Run `git status --short --branch`.
   - Treat tracked modifications, staged changes, and relevant untracked files as candidates.
   - Exclude generated artifacts or unrelated files from the commit; remove or leave them untracked as appropriate.
3. Stage the full intended conversation change set explicitly:
   - Use `git add <paths>` for the intended files.
   - Verify with `git diff --cached --name-only`.
   - Verify no intended files are left behind with `git diff --name-only` and `git ls-files --others --exclude-standard`.
   - Rule: do not rely on partial staging by accident. If the user asked to commit the work from this conversation, make sure all intended conversation changes are staged before committing.
4. Commit only relevant changes in clean, atomic commits.
5. Do not bypass commit or verification hooks with `--no-verify` or similar shortcuts.
6. If a commit hook or verification step fails, resolve the blocking violations before completing the commit-to-main workflow, even when the failures originate outside the immediate feature change.
7. Free the `main` branch before starting transfer:
   - Run `git worktree list`.
   - If any other worktree is currently on `main`, record that path as the `main-holder`.
   - Detach the `main-holder` first with:
     - `git -C <PATH> checkout --detach`
   - Then check out `main` in the current worktree before cherry-pick/merge work begins.
   - Rule: only one worktree may own `main` at a time. Any later handoff must detach the current holder before checking out `main` elsewhere.
8. Identify transfer mode to `main`:
   - `commit to main` or `cherry-pick to main`: cherry-pick the new commit(s) onto `main`.
   - `merge to main`: merge the requested source branch into `main`.
9. Start the transfer on `main` and continue with normal flow if there are no conflicts.

## 2) Conflict Detection and Analysis

If the merge or cherry-pick reports conflicts:

1. Enumerate conflicts with:
   - `git status --short`
   - `git diff --name-only --diff-filter=U`
2. Build a conflict intent card for each conflicted file:
   - `main behavior contract`: which observable behavior/guardrails must be preserved.
   - `incoming architecture intent`: what structural pattern the incoming change introduces.
   - `behavior delta intent`: whether behavior change is intentional or accidental.
3. Use available evidence before resolving:
   - commit messages and diffs (`git show`)
   - nearby history (`git log`, `git blame`)
   - tests that define expected behavior

## 3) Resolution Decision Tree (Per File)

Apply this priority order:

1. Integration-first (default):
   - Preserve `main` behavior contracts.
   - Implement that behavior inside the incoming architecture where feasible.
2. Replace with incoming:
   - Use when incoming intentionally supersedes old design and behavior is equivalent or intentionally changed.
3. Keep `main`:
   - Use when incoming change is incomplete/risky and behavior cannot be safely preserved.

Low-confidence gate:

- If intent cannot be established from diff/context/tests/history, stop auto-resolution and ask the user with a concise options summary.

Safety rules:

- Do not blindly resolve all files with `--ours` or `--theirs`.
- Resolve file-by-file with explicit rationale.
- Prefer the smallest coherent edit that satisfies behavior + architecture intent.

## 4) Integration Method (Default Path)

When choosing integration-first:

1. Start from incoming structure where possible.
2. Port required `main` behavior into new extension points/adapters/hooks.
3. Keep externally observable behavior stable unless commit intent clearly changes it.

## 5) Verification Before Continue

Before `--continue` or merge completion:

1. Run targeted tests for changed areas (or closest available checks).
2. Confirm there are no unmerged paths:
   - `git diff --name-only --diff-filter=U` returns nothing.
   - `git status --short` shows no `U` entries.
3. Continue operation:
   - `git cherry-pick --continue` for cherry-pick flow.
   - complete merge flow for merge operations.

## 6) Completion Rules

1. Determine which worktree should own `main` at the end.
   - Follow any higher-priority caller instruction first.
   - Common outcomes:
     - current Codex worktree should end on `main`
     - original `main-holder` should be restored to `main`
2. If the current Codex worktree should end on `main`:
   - Run `git checkout main`.
   - If checkout fails because another worktree already owns `main`, detach that holder first:
     - `git -C <PATH> checkout --detach`
     - `git checkout main`
3. If the original `main-holder` should be restored instead:
   - Detach the current Codex worktree first:
     - `git checkout --detach`
   - Then restore `main` in the original holder:
     - `git -C <PATH> checkout main`
4. Never try to check out `main` in a second worktree before detaching the current holder. That ordering is invalid and will fail with `fatal: 'main' is already used by worktree at '<PATH>'`.
5. Delete the temporary `codex/...` branch.
6. Do not run `git worktree remove`.
7. Verify final state according to the selected owner:
   - the intended worktree is on branch `main`
   - the non-owning worktree is detached if required
   - the relevant worktree status is clean
8. Before declaring success, confirm the worktree does not still contain intended conversation changes:
   - Re-run `git status --short --branch`.
   - If tracked or untracked intended work remains, either commit/cherry-pick it too or tell the user exactly what was intentionally left out and why.
   - When the request was "commit all uncommitted changes in this convo onto main", the default expectation is that no intended conversation changes remain uncommitted afterward.

## 7) Scenario Expectations

1. Incoming full architecture replacement with equivalent behavior: replacement is acceptable.
2. Incoming new architecture missing `main` guardrails: integrate `main` behavior into incoming pattern.
3. Ambiguous merge conflict intent: pause and ask user.
4. Conflict-free merge/cherry-pick: proceed with normal flow.
5. Multi-file conflict with mixed intent: apply per-file decisions with rationale.
