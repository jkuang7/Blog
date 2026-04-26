# /run — Evidence-Constrained Goal Runner

Resource Hint: sonnet

> **Requires**: GOALS.md with goal prompts (from /goals)
> **Inherits**: `problem-solving.md` (Two-Phase Model, Chess Engine Search), `_task_lifecycle.md`

**Purpose**: Execute goals as an evidence-constrained optimizer. /run can explore and close gaps, but only ships progress backed by hard checks and artifacts.

**Input**: GOALS.md (queue), SPECS.md (intent + rubric), CONTRACTS.md (invariants), STATE.md (latest evidence), .memory/ (project learnings)
**Output**: Working code + STATE.md (evidence ledger, score deltas, stops, judgment calls)

---

## Two-Phase Model (inherited from `_task_lifecycle.md`)

> **Phase 1**: ORIENT → EXPLORE. Read .memory/ + GOALS/STATE, open app, see current state. **No code until you've seen the app.**
> **Phase 2**: IMPLEMENT one goal at a time. OBSERVE → ATOMIC STEP → VERIFY (TRUE/FALSE) → ITERATE. Throwaway branches for experiments.

---

## Runner v2 Protocol (Required)

**Layer 1 — Spec Compiler**

Before touching code, compile PRD artifacts into execution primitives:

1. **Goals**: from GOALS.md (queue + dependencies)
2. **User flows**: critical paths from SPECS.md/CONTRACTS.md
3. **Weighted rubric**: scoring axes from SPECS.md (UX, correctness, resilience, perf)
4. **Invariants**: executable checks from CONTRACTS.md + project scripts

If any of the four are missing, /run must create a best-effort temporary version in STATE.md and mark confidence as `LOW`.

**Layer 2 — Objective Loop**

Each iteration follows:

`observe -> pick gap -> patch -> verify -> score`

No score change is allowed unless verify artifacts exist.

Implementation path is intentionally flexible: /run may choose its own coding strategy, and may split/merge/reorder pending goals when blocked, as long as dependency safety and invariant gates are preserved.

---

## Strict Truth, Flexible Method

`/run` must be strict about:

- invariant pass/fail
- evidence quality
- acceptance criteria
- stop and park rules

`/run` may be flexible about:

- implementation strategy
- local sequencing inside a goal
- restructuring pending goals for better execution

If /run restructures goals, it must log rationale and deltas in STATE.md.

---

## Hard Gates Before Score

Run invariant suite **first** every iteration:

1. build
2. tests
3. lint
4. typecheck
5. contract checks (if present)

If any invariant fails:

- Iteration is `REJECTED`
- Score cannot increase
- Goal stays 🔄 or 🅿️
- STATE.md records failure evidence and route (`/debug` or `/spec`)

---

## Evidence-Only Progress Claims

Progress requires artifacts. Acceptable evidence:

- command output (tests/build/lint/typecheck)
- MCP screenshots/traces
- API diffs and request/response captures
- perf numbers (latency, bundle size, memory, etc.)

Rule: **No artifact = UNKNOWN (never SUCCESS).**

---

## Bounded Infinite (Epochs)

- Run in epochs (`10` iterations per epoch by default)
- Checkpoint + re-plan between epochs
- Stop when no observable state change for `N` epochs (default `2`) or budget is exhausted
- Prefer stopping with a crisp reason over endless drift

---

## Good-Enough Stop Rule (Simple)

Avoid both optimism and endless loops.

When all planned goals are done, run a short hardening sweep:

1. Full invariant suite (build/test/lint/typecheck/contracts)
2. Critical flow smoke replay from SPECS.md (MCP/Playwright)

Stop as `complete` only if both pass for `2` consecutive iterations.
If either fails, open a hardening goal and continue.

This is the default "good enough" bar for the evidence-constrained goal runner.

Keep it lean: do not add deeper checks unless evidence says they're needed.

---

## Lightweight Audit (Useful, Not Heavy)

Runner writes one line per iteration to `.memory/AUDIT.csv`.

Required columns:

- timestamp
- iteration
- invariants pass/fail
- flow smoke pass/fail
- hardening streak
- last decision
- stop reason

Use this file for debugging drift and validating stop decisions.

CSV values should be sanitized (no raw commas/newlines) to avoid audit contamination.

---

## Minimum-Sufficient Coverage Ladder

Keep complexity only where it buys confidence.

- **L1 (always):** invariant suite
- **L2 (always at completion):** critical flow smoke replay
- **L3 (risk-based):** negative-path checks for changed/high-risk flows
- **L4 (rare):** full matrix sweeps only after regressions/incidents

Default to L1+L2. Escalate to L3/L4 only with evidence.

---

## Anti-Hallucination and Contamination Controls

- Score only from observable artifacts produced in current iteration window
- If evidence is stale/ambiguous/missing, mark `UNKNOWN`
- Never claim behavior that was not executed or observed
- Separate facts from assumptions in STATE.md notes
- Reused evidence is allowed only for unchanged flows and must be labeled `reused`

If contamination risk is detected, reject the iteration and rerun checks.

---

## Shadow Invariant Miner

Assume PRDs are incomplete. When a bug slips through:

1. Propose a candidate invariant in STATE.md (`candidate_invariant:`)
2. Record source in `.memory/lessons.md` (regression/prod bug/review)
3. After one human approval, promote into CONTRACTS.md + checks
4. Future runs enforce it automatically

---

## Two Modes

| Mode | When | Ceremony | Human |
|------|------|----------|-------|
| **Interactive** (default) | Human is available | Lighter | Frequent check-ins |
| **Autonomous** (`--autonomous`) | Overnight, unattended | Full | None until blocked |

```bash
/run {project}              # Interactive mode (default)
/run {project} --autonomous # Full ceremony, overnight work
```

---

## Preflight Gate (Before Autonomous Loop)

Before `--autonomous` execution, /run validates:

1. `SPECS.md` has flows + weighted rubric + invariant suite
2. `GOALS.md` has runnable goals with acceptance criteria
3. `CONTRACTS.md` defines non-negotiable behaviors/invariants
4. `STATE.md` is writable for evidence + score checkpoints

If preflight fails, /run should:

- refuse autonomous execution
- write failure details in STATE.md
- route back to `/spec` or `/goals`

---

## Human-AI Collaboration Rhythm

Use this default rhythm for robust handoff:

- `/spec`: human decides hard tradeoffs, AI records boundaries
- `/goals`: human risk-orders and sizes, AI drafts executable prompts
- `/run` interactive: AI executes canary goal, human validates real UX
- `cl runner`: autonomous epochs after canary passes

This keeps intent-setting human-led and execution machine-fast.

---

## The Decision Framework

```
Uncertain about something?
    ↓
Check goal's PRD ref in SPECS.md
    ↓
Still unclear?
    ↓
Check Judgment Boundaries:
    ├── Listed in "MAY decide" → Make the call, document it
    ├── Listed in "MUST ask" → Park goal, wait for human
    └── Not listed → Default to park
```

---

## MANDATORY: Phase 1 First (Explore Before Implementing)

**Before ANY code:**

1. Read GOALS.md, STATE.md, .memory/
2. Start dev server (if not running)
3. Open app with MCP (headless)
4. Navigate to the area you're changing
5. Take snapshot - see current state
6. Understand what exists before changing it

**No optimistic coding.** See the app first. Let observations guide what you do.

---

## Interactive Mode (Default)

**Lighter ceremony. Human is watching.**

### Step 0: ORIENT

Read files, output brief summary:

```markdown
## ORIENT: {project}

**Milestone**: {M1 — name}
**Progress**: {n}/{total} goals | ⬜ {n} | 🔄 {n} | ✅ {n} | 🅿️ {n}
**Next goal**: G{n} — {name}
**Dependencies**: {met | blocked by G{x}}
```

### Step 1: Pick Goal

Find next ⬜ goal with all dependencies ✅.

```
Goals:
✅ G1 — Set up Redis cache
✅ G2 — Parse Yahoo response
⬜ G3 — Wire StockCard to fetch ← NEXT (deps met)
⬜ G4 — Handle rate limiting (blocked by G3)
🅿️ G5 — Error fallback (needs decision)
```

### Step 2: Read Goal Prompt

The goal prompt in GOALS.md has everything needed:
- Task (what "done" means)
- File scope (where to work)
- Constraints (what NOT to do)
- Verify (how to test)
- Acceptance criteria (pass/fail checklist)
- Risk level (how deep hardening should go)

```markdown
## Executing: G3 — Wire StockCard to fetch

**Task**: Connect StockCard component to /api/stocks endpoint
**File scope**: src/components/StockCard.tsx
**Constraints**:
- Use existing fetch wrapper
- Do NOT add new dependencies
**Verify**: StockCard shows live data after refresh click
**Verify cmd**: `curl -s localhost:3000/api/stocks | jq .` OR open app and click refresh
```

> **Verify cmd** = a concrete shell command, curl, OR MCP browser action (playwright snapshot, chrome-devtools check). /run MUST execute it after implementation. If it fails → park or debug. This is the observe→verify philosophy made mechanical.

### Step 3: Implement (Iterative)

**OBSERVE → HYPOTHESIS → ATOMIC STEP → VERIFY → ITERATE**

```markdown
**HYPOTHESIS**: {what I think will work}
**ATOMIC STEP**: {one change, one file}
**VERIFY**: {how to test}
**EXPECTED**: {observable result}
```

Make the change. One file. One behavior.

### Step 4: Verify Against Acceptance Criteria

Check each criterion from the goal prompt:

```markdown
**Acceptance Criteria Check:**
- [x] StockCard fetches on mount
- [x] Refresh button triggers new fetch
- [x] Loading state shows during fetch
- [ ] Error state on failure ← FAIL
```

| Result | Action |
|--------|--------|
| All pass | Mark ✅, update STATE.md, continue |
| Any fail | Debug loop or 🅿️ park |

### Step 5: Update STATE.md + .memory/

After goal completes or parks:

1. Update STATE.md with goal status + evidence + score delta
2. Update .memory/context.md with progress checkpoint (which goal just finished, what's next)
3. If debugging revealed something → update .memory/lessons.md

```markdown
## G3 — Wire StockCard to fetch
**Status**: ✅ DONE
**Completed**: {timestamp}
**Notes**: Used existing fetchWrapper, added loading state
**Evidence**: {artifact ids or paths}
**Score delta**: {+n | 0 | rejected}
```

### Step 6: Check In (Interactive Only)

```
✅ G3 complete
Verified: StockCard shows live data, refresh works
Continue to G4? [y/n]
```

Human can redirect, adjust, or pause.

---

## Autonomous Mode (--autonomous)

**Full ceremony. No human until blocked.**

### Full Checkpoint Template (Required)

**Before EVERY goal:**

```markdown
## CHECKPOINT: G{n}

### Before
- [ ] Goal status set to 🔄 in STATE.md
- [ ] Dependencies verified (all ✅)
- [ ] File scope: `{filepath}`
- [ ] Acceptance criteria understood

### Implementation
GOAL: G{n} — {name}
FILE: {path}
CHANGE: {one sentence}

### After
- [ ] All acceptance criteria pass
- [ ] No regressions in dependent goals
- [ ] STATE.md updated
- [ ] .memory/context.md updated (progress checkpoint)

### Gate
- [ ] **✅ DONE** → update STATE.md + .memory/context.md, next goal
- [ ] **🅿️ PARK** → document reason + routing, update .memory/context.md
```

### Judgment Call Documentation

When making a decision within "MAY decide" boundaries:

```markdown
## JUDGMENT CALL

**Goal**: G{n}
**Situation**: {what came up}
**Intent says**: {relevant part from SPECS.md}
**Decision**: {what I'm doing}
**Rationale**: {why this aligns with intent}
**Flag for review**: {YES if uncertain | NO if confident}
```

Flagged items get human review in `/review`.

### Parking a Goal

When a goal can't be completed:

```markdown
## 🅿️ PARKING LOT

### G{n} — {goal name}
**Blocked because**: {what's preventing completion}
**Tried**: {approaches attempted}
**Needs**: {what would unblock this}
**Route to**: {/goals | /spec | /ui | /debug}
```

Move to next unblocked goal. Don't stop the run.

### Signal File for Runner Loop

Write `$DEV/Repos/{project}/.memory/.runner_stop` when:

| Condition | Signal |
|-----------|--------|
| All goals ✅/🅿️ + hardening sweep passes twice | `complete: {summary}` |
| All remaining blocked | `all_parked: {reasons}` |
| Hit MUST ASK boundary | `must_ask: {question}` |
| No observable state change for N epochs | `plateau: no observable state change for {epochs}` |
| Budget exhausted | `budget_reached: {iterations or time}` |
| Repeated invariant failures | `invariant_blocked: {failing checks}` |
| Hardening never stabilizes in budget | `hardening_blocked: {failing flow/check}` |

Runner fallback: if `.runner_stop` is missing but GOALS has no ⬜/🔄 and STATE reports fresh stable hardening for the current iteration (`INVARIANTS_PASS=true`, `FLOW_SMOKE_PASS=true`, `HARDENING_STREAK>=2`), tmux runner may auto-stop as `complete`.

For GOALS parsing, only explicit goal heading states count (e.g., `### G7 ⬜`), not legend/examples.

Fallback must also see `LAST_DECISION=accept` and empty `STOP_REASON`.

If `/run` exits non-zero, runner should stop with `run_failed: ...`.

---

## When to Park (Not Debug Forever)

**Park if:**
- 3+ failed approaches to same goal
- Needs decision outside judgment boundaries
- Depends on unresolved [OPEN] in SPECS.md
- Constraint conflict between goals
- External blocker (API down, missing credentials)
- Claimed progress has no verifiable artifact

**Debug if:**
- Clear bug with identifiable cause
- Known fix in .memory/
- Simple implementation error

---

## The Debug Loop

```
Implement → Verify → FAIL
    ↓
Check .memory/
    ├── Known fix → apply
    └── Unknown → investigate (2-3 attempts)
    ↓
Still failing? → 🅿️ Park with learnings
```

**Mutation discovered = investigate.**
Don't try to fix without understanding.

---

## STATE.md Template

```markdown
# STATE: {project}

## Overview
**Milestone**: {M1 — name}
**Updated**: {timestamp}
**Goals**: {n} total | ⬜ {n} | 🔄 {n} | ✅ {n} | 🅿️ {n}

## Runner Metrics (machine-readable)
RUNNER_MODE: evidence_optimizer_v2
EPOCH: {n}
ITERATION: {n}
INVARIANTS_PASS: {true|false}
HARDENING_STREAK: {0|1|2}
FLOW_SMOKE_PASS: {true|false}
EVIDENCE_COUNT: {n}
EVIDENCE_MODE: {fresh|mixed|reused}
LAST_DECISION: {accept|reject|park}
STOP_REASON: {blank|reason}

---

## Goal Progress

### G1 ✅ — {name}
**Completed**: {timestamp}
**Notes**: {brief summary}
**Evidence**: {artifacts}
**Score delta**: {+n}

### G2 ✅ — {name}
**Completed**: {timestamp}

### G3 🔄 — {name}
**Started**: {timestamp}
**Current**: {what's being worked on}

### G4 ⬜ — {name}
**Blocked by**: G3

### G5 🅿️ — {name}
**Parked**: {timestamp}
**Reason**: {why}
**Route to**: /spec

---

## 🅿️ PARKING LOT

### G5 — {name}
**Blocked because**: {detailed reason}
**Tried**: {approaches attempted}
**Needs**: {what would unblock}
**Route to**: {/goals | /spec | /ui}

---

## JUDGMENT CALLS

| Goal | Decision | Rationale | Flagged? |
|------|----------|-----------|----------|
| G2 | Used 30s timeout | Matches Yahoo p99 | YES |
| G3 | Added retry logic | Intent says "resilient" | NO |

---

## For Next Session
**Next goal**: G{n} — {name}
**Context**: {anything needed to resume}
**Don't repeat**: {approaches that failed}
```

---

## Invariants (Non-Negotiable)

- **ORIENT first** - Read .memory/ + GOALS/STATE before coding
- **Explore app before coding** - See it, then change it
- **One goal at a time** - Complete or park before moving on
- **Invariant gate first** - Build/tests/lint/typecheck/contracts before scoring
- **Evidence or unknown** - No artifact means no success claim
- **Never skip verification** - Acceptance criteria must pass
- **Respect judgment boundaries** - MAY decide vs MUST ask
- **Update STATE.md** - After every goal completion or park
- **Park don't spin** - 3 failures = park, not infinite debug
- **Clean repo on exit** - No experiment branches left

---

## Usage Summary

```bash
# Interactive (human available)
/run {project}

# Autonomous (overnight)
/run {project} --autonomous

# Specific goal
/run {project} G5
```

Recommended launch order:

1. `/run {project}` (interactive canary)
2. `cl runner {project}` (autonomous epochs)

**Interactive:** Lighter ceremony, frequent check-ins, human redirects.
**Autonomous:** Full checkpoints, judgment documentation, signal file.

---

## Key Points

1. **Compile spec first** - goals + flows + rubric + invariants before coding
2. **Invariant gate first** - failed checks reject iteration regardless of rubric gain
3. **Evidence-only progress** - no artifact means UNKNOWN
4. **Objective loop** - observe → gap → patch → verify → score
5. **Bounded epochs** - checkpoint every 10 iterations, stop on stall/budget
6. **Goal states** - ⬜ → 🔄 → ✅ or 🅿️
7. **Park don't spin** - 3 failures = park with learnings
8. **STATE.md is ledger** - status + evidence + score deltas + stop reasons
9. **Judgment calls documented** - flagged for human review
10. **Good-enough completion** - 2-pass hardening sweep before `complete`

> **Detailed examples**: See `_reference_run.md`
