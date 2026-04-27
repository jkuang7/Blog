---
name: prune
description: Prune extra git worktrees while keeping one checkout, usually the canonical repository checkout on main. Use when the user wants to clean up old worktrees, remove stale linked checkouts, keep only one repo copy, or verify whether a worktree is safe to delete before pruning it.
---

# Prune

Use this skill to keep one checkout and remove the rest of the linked git worktrees.

After this workspace cleanup, linked worktrees should be treated as exceptional. The normal state is one canonical checkout per repo, typically on `main`.

## Workflow

1. Inspect all worktrees before deleting anything.
2. Choose the single checkout to keep.
3. Prefer the canonical repo checkout and switch it to `main` when that is safe.
4. Remove the other worktrees.
5. Prune git metadata, clean up empty leftover folders, and verify the final state.

## Inspect First

Collect:

```bash
git worktree list --porcelain
git branch -vv
git status --short --branch
```

For each worktree, inspect:

```bash
git -C <worktree-path> status --short --branch
git -C <worktree-path> rev-list --left-right --count main...HEAD
git -C <worktree-path> log -1 --format='%H%n%ci%n%s'
```

Use that to determine:

- which checkout should remain
- whether any worktree is dirty
- whether any worktree differs from `main`

Do not assume a detached worktree is disposable until the status and commit comparison confirm it.

## Choose The Checkout To Keep

Default to the canonical repo checkout, typically the non-linked repo path such as `/Users/.../Repos/<repo>`.

If multiple clean checkouts point at the same commit as `main`, keep the canonical repo checkout and remove the linked worktrees.

If the checkout you are keeping is detached but clean and matches `main`, switch it to `main` before removing the others:

```bash
git -C <keep-path> checkout main
```

If the kept checkout has local changes, do not switch branches until you understand whether that would disturb active work.

## Removal Rules

Use normal removal for clean worktrees:

```bash
git -C <keep-path> worktree remove <worktree-path>
```

Use forced removal only when the user explicitly wants to discard dirty or stale worktrees:

```bash
git -C <keep-path> worktree remove --force <worktree-path>
```

After removals:

```bash
git -C <keep-path> worktree prune
git -C <keep-path> worktree list --porcelain
```

Then inspect the removed worktree paths on disk. If git deregisters a worktree but leaves empty folders behind, remove the empty worktree directory and its now-empty wrapper folder. If leftovers contain files, inspect them before deleting anything and remove only obvious cache files or directories that are fully empty.

## Helper Script

Use the helper script for dry-run planning or repeatable execution:

```bash
python3 scripts/prune_worktrees.py --repo <any-checkout-path>
python3 scripts/prune_worktrees.py --repo <any-checkout-path> --apply
python3 scripts/prune_worktrees.py --repo <any-checkout-path> --apply --force-dirty
```

The script:

- detects the checkout to keep automatically unless `--keep` is provided
- prefers the canonical repo checkout
- refuses to remove dirty worktrees unless `--force-dirty` is set
- switches the kept checkout to `main` when safe
- removes the other worktrees, prunes git metadata, and cleans up empty leftover folders

Read [cleanup-notes.md](./references/cleanup-notes.md) only when you need the edge-case checklist.

## Verification

Always end with:

```bash
git -C <keep-path> worktree list --porcelain
git -C <keep-path> status --short --branch
```

Report:

- which checkout was kept
- which worktrees were removed
- which empty leftover folders were cleaned up, if any
- whether anything was skipped because it was dirty or diverged from `main`
