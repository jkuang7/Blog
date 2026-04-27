# Worktree Cleanup Notes

Use this checklist only for edge cases.

## Safe Defaults

- Keep the canonical repo checkout.
- Keep `main` checked out there if it is safe to switch.
- Remove only linked worktrees.
- Do not delete local branches.

## Edge Cases

- Dirty worktree: inspect first; remove with `--force` only if the user explicitly wants to discard it.
- Detached keep checkout: switch it to `main` only when it is clean and at the same commit as `main`.
- Diverged worktree: compare against `main` before removing it.
- Orphaned leftover directory after `git worktree remove`: first remove the empty worktree directory itself, then its empty wrapper folder if one remains. If files are still present, inspect them and delete only obvious cache files or directories that are fully empty.
