# /commit - Git Commit

Resource Hint: haiku

**Purpose**: Create git commits with intelligent message generation.

**Your Job**: Analyze changes, generate commit message, stage and commit.

---

## Workflow

### Step 0: Branch Check

**Protect dev root**: `$DEV` must always be on `main`. Auto-switch if not.

```bash
git branch --show-current
```

If not on `main`:
```
⚠️ {repo} is on '{branch}', switching to main...
git checkout main
```

Project repos (`$DEV/Repos/*`): any branch is fine - workflow commands handle branching.

---

### Step 1: Analyze Changes (Multi-Repo)

**Check the Dev root repo**. `workspace/` is part of `$DEV`, not a separate git repo.

```bash
# Check the Dev root repo
cd $DEV && git status --short
```

**Scope rules:**
- `$DEV/workspace/*` - Always check, commit conversation changes
- root control files in `$DEV/*` - Always check, commit conversation changes
- `$DEV/Repos/*` - IGNORE unless files modified in current conversation

**Output format:**
```
## Repos with changes

Dev:
  M .gitignore
  M workspace/example/src/menu.py
```

If nothing to commit:
```
No changes to commit.
Dev repo clean.
```

---

### Step 2: Smart Staging

**Only stage files you modified in this conversation.** Ignore unrelated changes.

#### Auto-Stage Config Files

When committing **any repo**, the following paths are **dev config files** and should be auto-staged without asking if they have changes — they're always intentional:

**In `$DEV` repo under `workspace/`:**
- `commands/*.md` (slash command files)
- `AGENTS.md`
- `settings.local.json`
- `hooks/**`
- `scripts/**`
- `rules/**`
- `templates/**`
- `config/**`
- `repos.txt`
- `.gitignore`

**In project repos (`$DEV/Repos/*`):**
- `AGENTS.md` (project root)

**In `$DEV` repo:**
- `AGENTS.md`
- `.gitignore`

**Exclude from auto-staging** (require explicit user request):
- `plans/` (ephemeral plan output)
- `logs/` (runtime logs)
- `app-backups/` (sync artifacts)

When auto-staging config files, mention them:
```
## Auto-staged (workspace config)

- commands/commit.md
- AGENTS.md
- settings.local.json

(These are dev config files — always safe to commit)
```

#### General Staging Rules

If there are both conversation changes and unrelated changes:

```
## Changes

This conversation:
- config/aerospace/lib.sh (12 lines)

Unrelated (not staging):
- scripts/menu.py (150 lines)

Staging conversation changes only.
```

If ALL unstaged changes are from this conversation, stage them without asking.

If NONE of the changes are from this conversation:
```
No changes from this conversation to commit.

Outstanding changes from previous sessions:
  M AGENTS.md (42 lines)
  D old-files/*.md (15 files)
  ...

[commit all] [leave as-is]
```

**If user says "commit all"** → stage everything, generate appropriate commit message.

### `--all` Flag

`/commit --all` or `/commit all` → commit ALL outstanding changes, not just this conversation.

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

### Step 4: Execute Commit (Multi-Repo)

On approve, commit to each repo that has conversation changes:

```bash
# Dev repo
cd $DEV && git add {files} && git commit -m "{message}"
```

**Do NOT include**:
- Co-Authored-By lines
- Generated with Codex lines

Show result:
```
## Committed

Dev: {hash} - {N} files

{type}({scope}): {summary}
```

---

### Step 5: Show Result & Offer Push

After commit:
```
## Committed

Dev: {hash} ({N} files) or "no changes"

Push? [y/n]
```

If user says yes → push the Dev repo:
```bash
cd $DEV && git push  # if had changes
```

**Scope**: `$DEV` only. For all repos including `Repos/*`, use `/commitp`.

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

---

## Safety Rails

**NEVER**:
- Commit secrets or credentials
- Use `--force` or `--no-verify`
- Amend commits not authored by you
- Commit to main/master without confirmation

**ALWAYS**:
- Show diff summary before committing
- Warn on large commits (>500 lines changed)
- Warn on credential-like files
- Confirm branch before committing

---

## Key Reminders

1. **Conversation scope** - Only stage files you modified in this conversation
2. **Ignore unrelated** - Don't touch changes from other sessions
3. **Never commit secrets** - Always check for credentials
4. **Conventional commits** - Use type(scope): summary format
5. **Imperative mood** - "Add" not "Added"
6. **Explain why** - Body explains reasoning, not just what
7. **Small commits** - Warn on large changesets
8. **Safety first** - Never force, never skip hooks

---

## Summary

You are the **Commit Helper**. Your job:

1. **Analyze** - Understand what changed
2. **Stage** - Smart staging with safety checks
3. **Message** - Generate conventional commit message
4. **Commit** - Execute with confirmation
5. **Push?** - Offer to push after commit

**Core pattern**: Analyze → Stage conversation changes only → Commit → Push?

**Philosophy**: One conversation = one commit. Offer to commit outstanding changes if none from this conversation.

**Flags**:
- `/commit` - Conversation changes only (default)
- `/commit --all` - All outstanding changes in $DEV (excludes Repos/*)
- `/commit {repo}` - Specific repo only

**Scope**:
- `$DEV` only
- Exclude `$DEV/Repos/*` unless current conversation modified them
- Use `/commitp` for ALL repos including Repos/*
