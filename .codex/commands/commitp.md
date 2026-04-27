# /commitp - Git Commit & Push (All Repos)

Resource Hint: haiku

> **Inherits**: `/commit` for staging rules, commit message format, and safety rails

**Purpose**: Commit all managed repos and push to remote.

**Your Job**: Scan all repos, commit changes, push to keep remote in sync.

---

## Multi-Repo Support

**Always** check all managed repos:

```bash
$DEV                   # Root workspace repo
$DEV/Repos/*/  # All project repos
```

1. **Scan all repos**: Check each location for uncommitted changes
2. **Filter**: Only process repos with actual changes
3. **Iterate**: For each dirty repo, run the full commit + push workflow
4. **Summary**: Show final status table

```
## Repos with Changes

Found changes in 2 of 5 repos:
- Dev (2 files modified)
- blog (3 files modified)

Processing each...
```

After all repos processed:
```
## Summary

| Repo | Commit | Changes |
|------|--------|---------|
| Dev | `abc123` | refactor: consolidate workspace config |
| blog | `def456` | fix: typo in header |

✅ All pushed
```

---

## Workflow (Per Repo)

> **Shared steps**: See `/commit` for Branch Check, Smart Staging, Commit Message format, Commit Types, and Safety Rails.

### For Each Repo

1. Branch check (see `/commit` Step 0)
2. Analyze changes (see `/commit` Step 1)
3. Smart staging (see `/commit` Step 2 — including auto-stage for config files across all repos)
4. Generate commit message (see `/commit` Step 3)
5. Execute commit (see `/commit` Step 4)
6. Push to remote (below)

### Push to Remote

```bash
git push
```

If no upstream:
```bash
git push -u origin {current-branch}
```

If push fails:
```
⚠️ Push failed: {error message}

Options:
- Pull and merge first
- Force push (not recommended)
```

---

### Step 6: Show Summary

After all repos processed (truncate Changes to ~40 chars):
```
## Summary

| Repo | Commit | Changes |
|------|--------|---------|
| Dev | `abc123` | refactor: consolidate workspace config |
| blog | - | (has runner) |
| Banksy | - | (no changes) |

✅ All pushed (1 skipped - has runner)
```

---

## Telecodex Runner Detection

Before committing a repo, check whether Telecodex has an active runner that owns the same checkout. Prefer the Telecodex SQLite runner state exposed by `tmux-codex`/`clls`; do not use old tmux runner session names.

If an active Telecodex runner owns the repo, skip that repo and note it as "(active Telecodex runner)" in the summary.

---

## Safety Rails

> See `/commit` Safety Rails for base rules.

**Additional for /commitp:**
- Check for active Telecodex runners before committing
- Skip repos owned by active Telecodex runners
- Verify push succeeded for each repo

---

## Summary

You are the **Commit & Push Helper**. Your job:

1. **Scan** - Find all repos with changes
2. **Iterate** - For each repo: stage, commit, push
3. **Report** - Show final summary table

**Core pattern**: Scan repos → Stage → Commit → Push → Report

**Philosophy**: Keep all repos synced. Safe commits. Never commit secrets.
