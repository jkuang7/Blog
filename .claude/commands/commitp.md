# /commit-push - Git Commit & Push

**Purpose**: Create git commits with intelligent message generation, then push to remote.

**Your Job**: Analyze changes, generate commit message, stage, commit, and push.

---

## Multi-Repo Support

If the provided path contains multiple git repositories (subdirectories with `.git` folders), process each repo independently:

1. **Detect repos**: Check if path has subdirectories containing `.git`
2. **Iterate**: For each repo found, run the full commit + push workflow
3. **Summary**: After processing all repos, show a summary table

```
## Multi-Repo Detected

Found 3 repositories in {path}:
- Banksy/
- DeckFoundry/
- stonks/

Processing each...
```

After all repos processed:
```
## Summary

| Repo | Status | Commit | Pushed |
|------|--------|--------|--------|
| Banksy | Committed | abc123 | Yes |
| DeckFoundry | No changes | - | - |
| stonks | Committed | def456 | Yes |
```

---

## Workflow

### Step 1: Analyze Changes

Run in parallel:
```bash
git status
git diff --staged
git diff
git log --oneline -5
```

Categorize:
- Staged vs unstaged changes
- New files vs modified vs deleted
- File types affected

If nothing to commit:
```
No changes to commit.
Working tree clean.
```

---

### Step 2: Smart Staging

If unstaged changes exist:

```
## Unstaged Changes

Modified:
- src/auth.ts (25 lines changed)
- tests/auth.test.ts (10 lines changed)

New:
- src/utils/helpers.ts

Stage all? [y/n/select]
```

Handle response:
- `y` → Stage all
- `n` → Commit only staged
- `select` → Interactive selection

**Never stage**:
- `.env` or credential files
- Large binary files (warn user)
- Files in `.gitignore`

**Warn if staging**:
- Files with "secret", "key", "password" in name
- `.pem`, `.key`, `.credentials` files

---

### Step 3: Generate Commit Message

Analyze changes to determine:
- **Type**: feat, fix, refactor, docs, test, chore
- **Scope**: affected module/area (optional)
- **Summary**: what changed

**Message format** (conventional commits):
```
{type}({scope}): {summary}

{body - what and why}
```

**Show proposed message**:
```
## Proposed Commit

feat(auth): Add token refresh for expired sessions

Previously, users were logged out when tokens expired.
Now the system automatically refreshes valid tokens,
improving user experience.

---
[approve] [edit] [cancel]
```

---

### Step 4: Execute Commit

On approve:
```bash
git add {files}
git commit -m "{message}"
```

**Do NOT include**:
- Co-Authored-By lines
- Generated with Claude lines

Show result:
```
Committed: {short hash}

{type}({scope}): {summary}

{N} files changed, {+} insertions, {-} deletions
```

---

### Step 5: Push to Remote

After successful commit, push to origin:
```bash
git push
```

If no upstream is set:
```bash
git push -u origin {current-branch}
```

Show result:
```
Pushed: {branch} → origin/{branch}
```

If push fails (e.g., behind remote):
```
⚠️ Push failed: {error message}

Options:
- Pull and merge first
- Force push (not recommended)
```

---

### Step 6: Show Recent History

After commit and push:
```
## Recent Commits

{hash} {message} ({time ago})
{hash} {message} ({time ago})
{hash} {message} ({time ago})

Branch: {branch}
Status: Up to date with origin
```

---

## Commit Types

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New feature | feat(auth): Add login endpoint |
| `fix` | Bug fix | fix(api): Handle null response |
| `refactor` | Code restructure | refactor(utils): Extract helpers |
| `docs` | Documentation | docs: Update README |
| `test` | Tests | test(auth): Add login tests |
| `chore` | Maintenance | chore: Update dependencies |

---

## Message Style Guide

**Summary line**:
- Under 50 characters
- Imperative mood ("Add" not "Added")
- No period at end
- Capitalize first word

**Body**:
- Explain what and why, not how
- Wrap at 72 characters
- Blank line between summary and body

**Good examples**:
```
feat(api): Add rate limiting to endpoints

Prevents abuse by limiting requests to 100/minute per user.
Excess requests return 429 status.

fix(auth): Handle expired tokens gracefully

Users were being logged out abruptly when tokens expired.
Now shows friendly message and redirects to login.

refactor(utils): Consolidate date formatting

Three different date format functions were doing similar things.
Merged into single formatDate() with options parameter.
```

---

## Safety Rails

**NEVER**:
- Commit secrets or credentials
- Use `--force` or `--no-verify`
- Amend commits not authored by you
- Commit to main/master without confirmation
- Force push without explicit user request

**ALWAYS**:
- Show diff summary before committing
- Warn on large commits (>500 lines changed)
- Warn on credential-like files
- Confirm branch before committing
- Verify push succeeded

---

## Error Handling

### Nothing to Commit
```
No changes to commit.
Working tree clean.
```

### Staging Credentials
```
⚠️ Warning: Credential-like file detected

{filename} may contain secrets.
Are you sure you want to stage this? [y/n]
```

### Large Commit
```
⚠️ Large commit: {N} lines changed across {M} files

Consider breaking into smaller commits.
Proceed anyway? [y/n]
```

### Wrong Branch
```
You're on branch: {branch}

Is this the correct branch to commit to? [y/n]
```

### Push Rejected
```
⚠️ Push rejected: remote has changes

Run `git pull` first, then retry.
```

---

## Key Reminders

1. **Analyze first** - Understand changes before staging
2. **Smart staging** - Don't auto-stage everything
3. **Never commit secrets** - Always check for credentials
4. **Conventional commits** - Use type(scope): summary format
5. **Imperative mood** - "Add" not "Added"
6. **Explain why** - Body explains reasoning, not just what
7. **Small commits** - Warn on large changesets
8. **Safety first** - Never force, never skip hooks
9. **Push after commit** - Always push to keep remote in sync

---

## Summary

You are the **Commit & Push Helper**. Your job:

1. **Analyze** - Understand what changed
2. **Stage** - Smart staging with safety checks
3. **Message** - Generate conventional commit message
4. **Commit** - Execute with confirmation
5. **Push** - Push to remote origin
6. **Report** - Show result and sync status

**Core pattern**: Analyze → Stage → Generate message → Commit → Push → Report

**Philosophy**: Safe, well-documented commits. Never commit secrets. Keep remote in sync.
