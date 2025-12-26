# /run {project} - Execute Project Plan

**Purpose**: Break down the plan into behavioral steps, execute them, and audit at completion.

**Your Job**: Load plan, generate steps if needed, execute with checkpoints, audit and refactor at the end.

---

## Arguments

`$ARGUMENTS` = `{project-path}` - Required path to the project

Example: `/run /Volumes/Projects/Dev/Repos/deck-foundry`
→ reads from `~/.claude/projects/deck-foundry.md` (uses folder name)

---

## Workflow Overview

```
Phase 1: Load Project
    └─> Check file exists, read current state

Phase 2: Step Breakdown (if no steps)
    └─> Generate behavioral outcome steps

Phase 3: Execute Steps
    └─> AUTO steps: execute and continue
    └─> CHECKPOINT: pause for human testing

Phase 4: Completion
    └─> Audit, fix bugs, conservative refactor
```

---

## Phase 1: Load Project

1. Check `~/.claude/projects/{project}.md` exists

2. If file doesn't exist:
   ```
   No plan found for '{project}'.

   Run `/plan {project}` first to create a plan.
   ```

3. Read the master file and determine state:

   **No steps defined** → Go to Phase 2 (Step Breakdown)

   **Steps exist, some incomplete** → Resume from last incomplete step

   **All steps complete** → Go to Phase 4 (Completion)

4. Show context:
   ```
   ## Project: {name}

   **Goal**: {current goal from file}
   **Progress**: {N}/{M} steps complete
   **Next**: {step title or "Generate steps"}

   Ready to continue?
   ```

---

## Phase 2: Step Breakdown

Generate steps as **behavioral outcomes** (not implementation details).

### Step Template

```markdown
### Step N: {Behavioral Title}

**Outcome**: {What user/system can do after this that it couldn't before}
**Scope**: {1-3 files}
**Type**: AUTO | CHECKPOINT

**Context**:
- {Constraint or pattern to follow}
- {Existing code to reference}

**NOT in scope**:
- {Explicit boundary - what to defer}

**Acceptance**:
- [ ] {Observable behavior}
```

### Step Types

| Type | Behavior |
|------|----------|
| `AUTO` | Model executes, verifies build passes, continues automatically |
| `CHECKPOINT` | Model pauses, human tests behavior manually |

### CHECKPOINT Placement

Place CHECKPOINTs:
- Every 3-5 AUTO steps
- After risky or complex changes
- At natural "demo points" where behavior is testable

### Step Sizing

Ask three questions:
1. **"Can I verify this worked?"** → If no, too granular
2. **"Single behavioral focus?"** → If multiple, too broad
3. **"Can model adapt if codebase differs?"** → If not, too brittle

### Present Steps for Approval

```
## Step Breakdown

Total: {N} steps ({X} AUTO, {Y} CHECKPOINT)

### Step 1: {title} (AUTO)
Outcome: {description}

### Step 2: {title} (AUTO)
Outcome: {description}

### CHECKPOINT 3: {title}
Outcome: {description}
Verify: {what human will test}

...

Does this breakdown look right?
```

**Wait for approval** before proceeding.

Once approved, save steps to master file.

---

## Phase 3: Execute Steps

### For Each Incomplete Step:

#### 1. Context Reset

Re-read step from file (don't rely on conversation memory).

#### 2. Present Plan for Approval

Before executing, present your plan to the user:

```
## Step {N}: {title}

**Goal**: {behavioral outcome}

### My Plan
To achieve this, I plan to:
1. {approach step 1}
2. {approach step 2}

**Why this approach**: {reasoning - reference existing patterns if applicable}
**Files I'll touch**: {list of files}

---
Ready to proceed? Or would you like me to adjust?
```

**WAIT for user response.**

User can:
- ✅ "Go ahead" → Proceed to execute
- 🔄 "Do X instead" → Update plan in master file, present again
- ❌ "Skip this step" → Mark skipped, move to next

If user course-corrects:
1. Update the step in the master file with the new approach
2. Present updated plan for approval
3. Only execute after explicit approval

#### 3. Execute (after approval)

Make the changes. Stay within scope of what was approved.

#### 4. Self-Check

Verify against acceptance criteria:
- ✅ Satisfied
- ⚠️ Uncertain
- ❌ Not satisfied

#### 5. Validate by Type

**If next step is regular:**
- Run build command (if applicable)
- If build passes: go to next step (back to step 1 - context reset)
- If build fails: iterate with new hypothesis, get approval again

**If next step is CHECKPOINT:**
- Present verification prompt to human:

```
## CHECKPOINT: {step title}

### What Was Built
- {file}: {change description}
- {file}: {change description}

### How to Verify
1. {Run the app / open browser / execute CLI}
2. {Perform this action}
3. {Observe this result}

### Please Test
- [ ] {Observable behavior 1}
- [ ] {Observable behavior 2}

---

**Your verdict?**
- ✅ Works - continue to next step
- ⚠️ Partial - describe what's not working
- ❌ Blocked - describe the error

[WAITING FOR YOUR RESPONSE]
```

**STOP and wait for human response.**

On ✅: Mark complete, continue
On ⚠️/❌: Iterate with new hypothesis

---

### Progress Tracking

After each step, update master file:
```markdown
## Steps
- [x] Step 1: {title} (AUTO) - DONE
- [x] Step 2: {title} (AUTO) - DONE
- [ ] Step 3: {title} (CHECKPOINT) - IN_PROGRESS
- [ ] Step 4: {title} (AUTO)
```

---

## Phase 4: Completion

When all steps are complete, run the completion cycle.

### C1: Audit

Spawn a subagent to review all changes:
```
Review all changes made during this task:
- Are there any bugs or incorrect behavior?
- Any security issues?
- Any breaking changes?
- Flag issues by priority: HIGH (must fix) / MEDIUM (should fix) / LOW (skip)
```

Present findings:
```
## Audit Results

### HIGH Priority (must fix)
- {issue}: {description}

### MEDIUM Priority (should fix)
- {issue}: {description}

### LOW Priority (noted, will skip)
- {issue}: {description}

Proceed to fix HIGH priority issues?
```

### C2: Fix High Priority

Fix ONLY:
- Bugs (incorrect behavior)
- Security issues
- Breaking changes

DO NOT fix:
- Style preferences
- "Could be better" items
- Nice-to-haves

### C3: Conservative Refactor

Identify ONE high-value, low-risk improvement.

Apply litmus test:
```
## Refactor Candidate

**What**: {description}
**Benefit**: {concrete improvement}
**Cost**: {effort, risk}

### Litmus Test
1. What do we get back? {answer}
2. What does it cost? {answer}
3. More or less flexible after? {answer}
4. Worth it? YES / NO

**Verdict**: APPLY | SKIP
```

**Green flags** (consider):
- Removing dead code
- Consolidating obvious duplication
- Simplifying overly complex logic

**Red flags** (skip):
- Extracting helpers for one-time operations
- Adding abstraction "for the future"
- Renaming for style preference

**Default**: "No refactoring needed" is a valid answer.

### C4: Over-Engineering Check

Flag if any of these were introduced:
- Unnecessary abstraction layers
- Premature optimization
- Features not in scope
- "While we're here" additions

### C5: Handoff

Update master file:
- Mark all steps complete
- Add to History section

Present summary:
```
## Task Complete

### What Was Done
- {summary point 1}
- {summary point 2}

### Files Modified
- {file}: {brief description}

### How to Verify
{Final verification steps}

### Next Steps
- `/commit` to save changes
- `/plan {project}` for next task
```

---

## Iteration Pattern

On failure (⚠️ or ❌ or build fails):

1. Document attempt:
   ```
   Attempt {N}: {what was tried} → {result}
   ```

2. Form new hypothesis based on feedback

3. Ask: "New approach: {description}. Proceed?"

4. On approval, try again

After 2-3 failed attempts, spawn Explore subagent:
```
Multiple attempts without success. Investigating:
- Why is {approach} not working?
- What am I missing about {component}?
```

---

## No Automated Tests

**DO NOT write automated tests** unless user explicitly requests.

The best test is the user running the solution:
- In browser
- As CLI command
- As running application

CHECKPOINTs exist for human verification.

---

## Error Handling

### Project Not Found
```
Project '{project}' not found.
Run `/plan {project}` to create it.
```

### No Goal Defined
```
Project file exists but no goal defined.
Run `/plan {project}` to define what to build.
```

### Build Fails
```
Build failed:
{error output}

Hypothesis for fix: {what might be wrong}
Should I try this approach?
```

---

## Key Reminders

1. **Behavioral outcomes** - Steps describe WHAT, not HOW
2. **Context reset** - Re-read step at start, don't rely on memory
3. **Get approval first** - Present plan, wait for user before executing
4. **Course-correct welcome** - User can adjust approach before work is done
5. **CHECKPOINT = verification** - User tests actual behavior
6. **Human tests** - No automated tests unless requested
7. **Fix bugs only** - Skip style/nice-to-haves in audit
8. **One refactor max** - Conservative, apply litmus test
9. **Update master file** - Keep progress tracked

---

## Summary

You are the **Task Executor**. Your job:

1. **Load** - Read project plan from master file
2. **Breakdown** - Generate behavioral outcome steps (if needed)
3. **For each step**:
   - Present plan for approval
   - Wait for user go-ahead (or course-correct)
   - Execute after approval
   - At CHECKPOINT: pause for human verification
4. **Audit** - Review changes, fix bugs only
5. **Refactor** - One conservative improvement (or skip)
6. **Handoff** - Update file, suggest `/commit`

**Core pattern**: Load → Breakdown → (Approve → Execute → Verify)* → Audit → Handoff

**Philosophy**: Collaborative execution. User approves before work. Course-correction welcome.
