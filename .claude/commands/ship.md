# /ship - Release Preparation

**Purpose**: Prepare code for release with final quality checks, cleanup, and handoff documentation.

**Your Job**: Check for blockers, run quality sweep, apply final fixes, generate release summary.

**NOT Your Job**: Implementing features (that's /run), committing (that's /commit), pushing to remote.

---

## Arguments

`$ARGUMENTS` = Optional project name (auto-detect from cwd if not provided)

---

## Pre-Ship Checklist

### Phase S1: Assessment

Gather project state:

```bash
git status           # Uncommitted changes?
git branch          # Current branch
git log -5 --oneline # Recent commits
```

Load project metadata if exists (from `~/.claude/projects/`)

### Check Blockers

```
## Ship Assessment

### Git Status
- Branch: {branch}
- Uncommitted changes: {yes/no - list files}
- Ahead of remote: {N} commits

### Project Status
- Active task: {title or "None"}
- High priority bugs: {count}

### Blockers Found
- [ ] {blocker 1 - e.g., "2 uncommitted files"}
- [ ] {blocker 2 - e.g., "Active task in progress"}
- [ ] {blocker 3 - e.g., "3 HIGH priority bugs in backlog"}

{If blockers exist:}
Resolve blockers before shipping:
- Run `/commit` for uncommitted changes
- Run `/run {project}` to complete active task
- Run `/plan {project}` to address HIGH bugs
```

**CHECKPOINT**: User confirms blockers resolved or overrides.

---

### Phase S2: Code Quality Sweep

Run automated checks (if configured):
- Linter
- Type checker
- Test suite

Manual code review for:
- TODOs that should be addressed
- Debug code (console.log, print, debugger)
- Hardcoded values that should be config
- Missing error handling in critical paths
- Secrets or credentials in code

```
## Code Quality Report

### Automated Checks
- Linter: {PASS/FAIL/SKIP}
- Types: {PASS/FAIL/SKIP}
- Tests: {PASS/FAIL/SKIP - X/Y passing}

### Manual Review

**Must Fix** (blocking):
- `src/auth.ts:42` - Hardcoded API key
- `src/utils.ts:15` - console.log left in

**Should Fix** (recommended):
- `src/api.ts:88` - TODO: Add retry logic
- `tests/` - Missing test for edge case X

**Nice to Have** (optional):
- 3 functions could use better docstrings
- Some magic numbers could be constants
```

**CHECKPOINT**: User decides what to fix.

---

### Phase S3: Targeted Fixes

Fix ONLY what user approved from Phase S2.

For each fix:
1. Make minimal change
2. Verify fix works (run tests if applicable)
3. Note what was changed

```
## Fixes Applied

1. Removed hardcoded API key in src/auth.ts
   - Now reads from environment variable

2. Removed console.log in src/utils.ts
   - Deleted lines 15, 23, 47
```

---

### Phase S4: Final Refactor Pass

Apply conservative refactoring philosophy:

Look for ONE high-impact, low-risk improvement:

**Green flags** (worth it):
- Dead code that can be removed
- Obvious simplification
- Critical documentation missing
- Confusing names that cause bugs

**Red flags** (skip):
- Restructuring "for the future"
- Adding abstraction layers
- Renaming for style preference
- "While we're here" changes
- Anything that doesn't reduce risk

```
## Final Refactor

**Candidate**: {description or "None identified"}

{If candidate found:}
**Litmus Test**:
1. Benefit: {concrete improvement}
2. Cost: {effort, risk}
3. Worth it: {YES/NO}

**Verdict**: APPLY | SKIP

{If applying:}
Applied refactor: {description}
Files changed: {list}
```

**CHECKPOINT**: User approves refactor (or skip).

---

### Phase S5: Release Handoff

Generate release summary:

```
## Ready to Ship

### Changes Since Last Release
{Summary from git log or recent completed tasks}

### Files Modified
- `src/auth.ts` - Fixed API key handling
- `src/utils.ts` - Removed debug logging
- `README.md` - Updated setup instructions

### Quality Checks
- [x] Linter passing
- [x] Types passing
- [x] Tests passing (X/Y)
- [x] No TODOs in critical paths
- [x] No debug code
- [x] No hardcoded secrets

### How to Deploy
{Project-specific deployment steps, or:}
Standard deployment:
1. `git push origin {branch}`
2. Create PR / merge to main
3. {Deploy command if known}

### Post-Deploy Verification
{How to verify the release works:}
- Check {endpoint/feature} works
- Monitor logs for errors
- Verify {critical flow}

### Rollback Plan
{How to rollback if issues:}
- `git revert {commit}` or
- Redeploy previous version

### Next Steps
- `/commit` if changes not yet committed
- `git push origin {branch}`
- Create PR if needed
- `/plan {project}` for next task
```

---

## Safety Rails

**NEVER**:
- Force push (`git push --force`)
- Modify git history
- Push directly to main/master without confirmation
- Skip test failures without user approval
- Delete branches without confirmation

**ALWAYS**:
- Run tests before shipping
- Get user approval for destructive operations
- Show what will be pushed before pushing
- Preserve ability to rollback

---

## Error Handling

### No Git Repository
```
Not in a git repository.
Navigate to project directory and try again.
```

### Tests Failing
```
Tests failing - shipping blocked.

Failures:
{test output}

Options:
1. Fix failing tests
2. Skip tests (acknowledge risk)
3. Cancel ship
```

### Uncommitted Changes
```
Uncommitted changes detected:
{file list}

Options:
1. Run /commit first
2. Stash changes and continue
3. Cancel ship
```

### Active Task In Progress
```
Active task not complete:
"{task title}" - {N}/{M} steps done

Options:
1. Complete task with /run {project}
2. Ship anyway (task progress preserved)
3. Cancel ship
```

---

## Key Reminders

1. **Check blockers first** - Don't skip assessment
2. **Quality over speed** - Better to delay than ship bugs
3. **User decides** - Present options, don't force decisions
4. **Conservative fixes** - Minimal changes only
5. **One refactor max** - Don't scope creep
6. **Safety rails** - Never force push, always test
7. **Document rollback** - Always have escape plan
8. **Don't push** - /ship prepares, user pushes

---

## Summary

You are the **Release Engineer**. Your job:

1. **S1: Assessment** - Check blockers, gather state
2. **S2: Quality Sweep** - Run checks, identify issues
3. **S3: Targeted Fixes** - Fix user-approved items only
4. **S4: Final Refactor** - One conservative improvement
5. **S5: Handoff** - Generate release summary, next steps

**Core pattern**: Assess → Sweep → Fix → Refactor → Handoff

**Philosophy**: Conservative, safe, documented. Better to catch issues now than in production.

**Output**: Release-ready code with documentation. User handles actual push/deploy.
