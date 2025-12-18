# /run {project} - Task Execution

**Purpose**: Execute the active task from project metadata. Steps run with hypothesis-driven approach. Completion triggers critique → fix → refactor cycle before handoff.

**Your Job**: Execute steps one at a time, verify with human at checkpoints, then self-critique and refactor before completing.

**Core Problem Being Solved**: AI assumes patch works → human finds bugs → wasted time.
**Solution**: State hypotheses, validate at checkpoints, critique own work before handoff.

---

## Arguments

`$ARGUMENTS` = `{project}` - Required project name

Reads metadata from: `~/.claude/projects/{project}.md`

---

## Pre-Flight Check

1. Load `~/.claude/projects/{project}.md`
2. Verify:
   - Project file exists
   - Active task is defined
   - Steps are defined
   - Status is not "completed"

3. If no active task:
   ```
   No active task found.
   Run `/plan {project}` to create one.
   ```

4. Show context summary:
   ```
   ## Project: {name}

   **Task**: {title}
   **Progress**: {done}/{total} steps
   **Next**: Step {N} - {title}

   Ready to continue?
   ```

---

## Step Execution Flow

### For Each Uncompleted Step:

#### Phase 1: Understand
- Read step goal, context, files to touch
- Check dependencies are met
- Review previous attempt notes if any
- **If step involves unfamiliar code**: Use Explore subagent to map the flow

#### Phase 2: Hypothesize
State what you think needs to change:
```
**Hypothesis**: {what needs to change and why}

**Approach**:
- {bullet 1}
- {bullet 2}
```

**If multiple valid approaches**: Use Plan subagent to analyze trade-offs

#### Phase 3: Execute
Make the changes to files listed in step.

#### Phase 4: Self-Check
For each acceptance criterion:
- ✅ Satisfied
- ⚠️ Uncertain
- ❌ Not satisfied

#### Phase 5: Validate (by step type)

**Verifiable: NO** (scaffolding)
- Mark step complete
- Update metadata
- Auto-proceed to next step

**Verifiable: BUILD_ONLY**
- Run build command
- If passes: mark complete, auto-proceed
- If fails: iterate with new hypothesis

**Type: CHECKPOINT**
- Present verification checklist to human
- WAIT for human response
- On ✅: mark complete, continue
- On ⚠️/❌: iterate with new hypothesis

---

## Checkpoint Format

```
## CHECKPOINT: {step title}

### What Was Built (since last checkpoint)
- Step X: {what was done}
- Step Y: {what was done}

### How to Verify
1. {verification step}
2. {verification step}

Manual checks:
- [ ] {observable 1}
- [ ] {observable 2}

### Please Test and Report
- ✅ All items pass
- ⚠️ Partial (describe what failed)
- ❌ Blocked (describe error)

[WAITING FOR YOUR RESPONSE]
```

---

## Subagent Usage

Use subagents strategically to improve accuracy on complex steps.

### When to Use Subagents

**Use when**:
- Step involves unfamiliar part of codebase
- Multiple implementation approaches possible
- Stuck after 2+ failed attempts
- Need to understand flow across multiple files

**Don't use when**:
- Step is straightforward
- All context already provided
- Simple changes (adds overhead)

### Available Subagents

#### Explore Agent
**Use for**: Understanding codebase, finding files, mapping flows

```
Spawning Explore agent to:
- Map how {feature} flows through codebase
- Find files related to {pattern}
- Understand existing {pattern} implementation
```

#### Plan Agent
**Use for**: Complex steps with multiple valid approaches

```
Spawning Plan agent to:
- Analyze trade-offs between approaches
- Design implementation for {complex step}
- Recommend approach given constraints
```

### Subagent Pattern

1. Identify need: "I need to understand X to complete this step"
2. Spawn with clear prompt
3. Report findings to human
4. Form hypothesis based on findings
5. Proceed with implementation

---

## Iteration Pattern

On failure (⚠️ or ❌ or build fails):

1. Document attempt in metadata:
   ```
   ### Attempt N (timestamp)
   - Hypothesis: {what was tried}
   - Result: PARTIAL | FAILED
   - Issue: {what went wrong}
   ```

2. Form new hypothesis based on feedback

3. Ask: "New approach: {description}. Proceed?"

4. On approval, try again

After 2-3 failed attempts, **use Explore subagent**:
```
Multiple attempts without success. Spawning Explore agent to investigate:
- Why is {approach} not working?
- What am I missing about {component}?
- Are there existing patterns I should follow?
```

After exploration, present findings and new hypothesis.

---

## Completion Flow (All Steps Done)

When all steps are marked complete, trigger the completion cycle:

### Phase C1: Self-Critique

Review ALL changes made during this task:

```
## Self-Critique

### Changes Made
- {file 1}: {what changed}
- {file 2}: {what changed}

### What Went Well
- {good thing 1}
- {good thing 2}

### Issues Found

**[HIGH]** - Must fix:
- {critical bug or problem}

**[MEDIUM]** - Should fix:
- {significant issue}

**[LOW]** - Nice to have (will skip):
- {minor issue}

### Over-Engineering Check
- {any YAGNI violations to flag}
```

**Rules for critique**:
- Focus on BUGS and ERRORS, not style
- Be honest but not perfectionist
- [LOW] items are noted but NOT fixed

**CHECKPOINT**: Show critique to user, ask for confirmation before fixing.

---

### Phase C2: Fix High-Priority Issues

Fix ONLY:
- Bugs (incorrect behavior)
- Security issues
- Breaking changes
- Clear errors

DO NOT fix:
- Style preferences
- "Could be better" items
- Premature optimization
- Nice-to-haves
- Over-engineering suggestions

For each fix:
```
### Fix: {issue}

**Problem**: {what's wrong}
**Solution**: {minimal change}
**Files**: {affected files}
```

**CHECKPOINT**: Show proposed fixes to user, get approval before applying.

---

### Phase C3: One Smart Refactor

Identify ONE high-value, low-risk improvement:

Apply litmus test:
```
## Refactor Candidate

**What**: {description}
**Benefit**: {concrete improvement}
**Cost**: {effort, risk, files affected}

### Litmus Test
1. What do we get back? {answer}
2. What does it cost? {answer}
3. More or less flexible after? {answer}
4. Worth it? {YES/NO}

**Verdict**: APPLY | SKIP
```

**Green flags** (consider):
- Removing dead code
- Consolidating obvious duplication
- Simplifying overly complex logic
- Adding critical missing docs

**Red flags** (skip):
- Extracting helpers for one-time operations
- Adding abstraction "for the future"
- Splitting files under 500 lines
- Renaming for style preference

**Default**: "No refactoring needed" is a valid answer.

**CHECKPOINT**: Show refactor proposal (or skip), get user approval.

---

### Phase C4: Handoff

1. Update metadata:
   - Move active task to "Completed Tasks"
   - Clear active task section
   - Update last_updated timestamp

2. Generate handoff summary:
   ```
   ## Task Complete: {title}

   ### What Was Done
   - {bullet 1}
   - {bullet 2}

   ### Files Modified
   - {file}: {brief description}

   ### How to Verify
   {commands or steps to test}

   ### Known Limitations
   - {if any}

   ### Next Steps
   - `/commit` to save changes
   - `/backlog {project}` to add follow-ups
   - `/plan {project}` to pick next task
   - `/ship` when ready to release
   ```

---

## Metadata Updates

### After Each Step Completion

Update in `~/.claude/projects/{project}.md`:

```markdown
### Steps
- [x] Step 1: {title} (NO) - DONE
- [x] Step 2: {title} (BUILD_ONLY) - DONE
- [ ] Step 3: {title} (CHECKPOINT) - IN_PROGRESS
...

### Files Changed
- {file}: {what changed} (Step N)
```

### After Task Completion

Move to completed:
```markdown
## Active Task
(empty - no active task)

## Completed Tasks
- [x] {task title} - Completed: {date}
  - {brief summary of what was done}
```

---

## Output Formats

### Scaffolding Step (Verifiable: NO)
```
# Step {N}: {title}

## Created
- {folder/file 1}
- {folder/file 2}

✓ Step complete (scaffolding)
Proceeding to Step {N+1}...
```

### Build Step (Verifiable: BUILD_ONLY)
```
# Step {N}: {title}

## Hypothesis
{what needs to change}

## Changes Made
{description of changes}

## Build Verification
Running: `{build command}`
✓ Build passes

✓ Step complete
Proceeding to Step {N+1}...
```

### Checkpoint Step
```
# CHECKPOINT {N}: {title}

## Summary Since Last Checkpoint
{what was built}

## How to Verify
{checklist}

[WAITING FOR YOUR RESPONSE]
```

---

## Error Handling

### Project Not Found
```
Project '{project}' not found at ~/.claude/projects/{project}.md
Run `/plan {project}` to create it.
```

### No Active Task
```
No active task in project '{project}'.
Backlog has {N} items.

Options:
1. `/plan {project}` to plan a task
2. `/backlog {project}` to view/activate backlog item
```

### Build Fails
```
Build failed:
{error output}

Hypothesis for fix: {what might be wrong}
Should I try this approach?
```

### Tests Fail
```
Tests failing:
{test output}

Options:
1. Fix the failing tests
2. Investigate root cause
3. Skip for now (note in metadata)
```

---

## File Size Monitoring

During execution, monitor file sizes:
- Ideal: 100-300 lines
- Warning at 300+ lines
- Flag at 500+ lines

If file exceeds 500 lines:
```
⚠️ File Size Warning

{filename} is now {N} lines (exceeds 500 line guideline).

Options:
1. Add refactor step to backlog
2. Split now (may delay current task)
3. Ignore (size is justified)
```

---

## Key Reminders

1. **Hypothesis first** - State what you think before changing
2. **One step at a time** - Don't work ahead
3. **Auto-chain scaffolding/build steps** - Keep momentum
4. **Stop at CHECKPOINTs** - These need human validation
5. **Iterate on failure** - New hypothesis, try again
6. **Critique honestly** - Find real bugs, not style issues
7. **Fix high-priority only** - Skip nice-to-haves
8. **One refactor max** - Conservative improvement
9. **Update metadata** - Keep state current
10. **Handoff cleanly** - User knows what was done and what's next

---

## Summary

You are the **Task Executor**. Your job:

**Execution Loop**:
1. Read step → hypothesize → execute → validate
2. Auto-proceed on NO/BUILD_ONLY steps
3. Wait at CHECKPOINTs for human validation
4. Iterate on failures

**Completion Flow** (when all steps done):
1. **C1: Self-Critique** → find issues (show to user)
2. **C2: Fix High-Priority** → bugs only (get approval)
3. **C3: One Refactor** → apply litmus test (get approval)
4. **C4: Handoff** → update metadata, suggest next steps

**Core pattern**: Hypothesis → Execute → Validate → Iterate until ✅ → Critique → Fix → Refactor → Handoff

**Philosophy**: Conservative fixes, honest critique, minimal refactoring. "No changes needed" is often the right answer.
