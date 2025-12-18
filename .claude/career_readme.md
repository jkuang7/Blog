# `/career` Command - Quick Reference

**Purpose**: Interview Coach for Tech Startups - helps engineers prepare for senior roles.

**Output**: `~/.claude/career/narrative.md`

---

## How to Use

### First Time Setup
1. Put your career documents in a folder (resume PDF, performance reviews, notes)
2. Run `/career` or `/career /path/to/docs`
3. Follow the phases - the coach will guide you through each step
4. Answer questions when prompted (gates marked with ✓)
5. Final output lands in `~/.claude/career/narrative.md`

### Typical Workflow
```
Day 1: /career              # Full run through Phase 4 (Stories probed)
Day 2: /career 5            # Continue with Resume Improvement
Day 3: /career 6-7          # STAR generation + Practice
Before interview: /career 7  # Quick practice session
```

### Tips
- **Be honest at gates** - Corrections early save time later
- **TOP 6 stories matter most** - Focus your energy there
- **Startup Translation** - Think: "What would I do with 3 people instead of 30?"
- **Practice mode** - Use `/career practice` or `/career 7` for quick drills

---

## Arguments

```
/career           → Full workflow (Phase 0 → 7)
/career 3         → Run Phase 3 only
/career 4-6       → Run Phases 4, 5, 6
/career practice  → Jump to Phase 7
/career resume    → Continue from last checkpoint
```

---

## Phase Overview

| Phase | Name | Purpose | Gate |
|-------|------|---------|------|
| 0 | Session Detection | Resume/fresh/jump options | - |
| 1 | Document Ingest | Read career docs, extract data | - |
| 2 | Summary Generation | Generate career timeline | ✓ Human confirms |
| 2.5 | Story Inventory | Extract 8-12 stories, assign IDs | ✓ Human confirms |
| 3 | Clarification Loop | Refine stories based on feedback | Loop until satisfied |
| 4 | Probe Stories | Deep-dive TOP 6 stories | ✓ Story Audit |
| 5 | Resume Improvement | Refine TOP 10 bullets | Loop per bullet |
| 6 | STAR Generation | Generate STAR format stories | ✓ Human confirms |
| 7 | Question Practice | Practice behavioral questions | ✓ Final output |

---

## Phase Details

### Phase 0: Session Detection
- Check if `narrative.md` exists
- Options: Resume / Start Fresh / Jump to Phase / Practice Only

### Phase 1: Document Ingest
- List files in input folder (PDF, .md, .txt)
- Read small files directly
- Chunk large files (>20K tokens) via subagent
- Extract: dates, projects, metrics, impact statements

### Phase 2: Summary Generation
- Generate career timeline with companies, roles, projects
- **GATE**: Human confirms accuracy

### Phase 2.5: Story Inventory
- Extract 8-12 canonical stories from timeline
- Assign Story IDs (STORY-01, STORY-02, ...)
- Tag with: Type, Company, Competencies, Stats
- **GATE**: Human confirms story list

**Story Types:**
- Core: Incident, Migration, Conflict, Delivery, Learning, Leadership, Debugging, Shipping
- Senior Signal: Optimization, Mentorship, Build-vs-Buy, Pushback, Customer Empathy

### Phase 3: Clarification Loop
- Apply corrections from human feedback
- Show before/after diffs
- Loop until human satisfied

### Phase 4: Probe Stories
- Score stories (Recency × Impact × Ownership × Complexity × Versatility)
- Select TOP 6 to harden
- Choose probing mode: Thorough (one-by-one) or Batch (all at once)
- For each story:
  - Walk through what happened
  - Alternatives considered
  - Tradeoffs made
  - How measured
- Add Startup Translation per story
- **GATE**: Story Audit with Weak Story Checklist
- **Save Progress** to narrative.md

**Weak Story Checklist:**
- [ ] Clear tradeoff articulated
- [ ] Specific metric
- [ ] YOUR action vs team's
- [ ] Alternatives considered
- [ ] WHY you chose this approach
- [ ] What you'd do differently

### Phase 5: Resume Improvement
- Select TOP 10 bullets to harden
- For each bullet:
  - Show current wording
  - Link to Story ID
  - Suggest revision
  - Options: Accept / Revise / APPROVED AS-IS / REMOVE
- Write finalized bullets to file

### Phase 6: STAR Generation
- Generate STAR for each hardened story
- Include Startup Translation
- Cross-link to supported bullets
- **GATE**: Human confirms

**STAR Format:**
- Situation (20-30 sec)
- Task (20-30 sec)
- Action (60-90 sec)
- Result (30 sec)

### Phase 7: Question Practice
- Load question bank (6 categories)
- Choose mode: Full Run / Focus / Random / Mock Interview
- For each question:
  - Ask question with suggested story
  - Score response (1-5 on 5 dimensions)
  - Provide feedback
  - Generate follow-up bank
  - Create answer versions (1-line / 2-min / deep-dive)
- Output: Quick Reference Card + Drill Schedule

**Categories:**
1. Ownership & Ambiguity
2. Shipping & Execution
3. Technical Leadership
4. Debugging & Incident Response
5. Collaboration & Conflict
6. Learning & Adaptability

---

## Cross-Linking System

```
Resume Bullet: "...50M+ requests" (Story: STORY-01)
Story: "Supports Bullets: B-01, B-03, B-07"
Question Answer: "Story: STORY-01"
Quick Reference: | Ownership | STORY-01 | 50M+ | B-01, B-03 |
```

---

## Output Structure

```
~/.claude/career/narrative.md
├── Session State (phase, timestamp)
├── Career Timeline
├── Story Inventory (STORY-01, STORY-02, ...)
├── Resume Bullets (B-01, B-02, ...)
├── STAR Stories
├── Behavioral Question Answers
├── Quick Reference Card
└── Drill Schedule
```
