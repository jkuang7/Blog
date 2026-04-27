# _reference_career.md - Agnostic Narrative + Targeted Prep Spec

Used by `/career`.

Goal: produce reusable narrative artifacts plus company-specific prep packs.

---

## Phase 0 - Session detect

1. Check `workspace/career/session.md`.
2. If present, offer:
   - resume previous run
   - refresh foundation only
   - create new target pack from existing foundation
3. If absent, initialize session file.

Session format:
- timestamp
- source folder
- foundation status
- known targets
- last completed phase

---

## Phase 1 - Source ingest and normalization

Source folder default: `/Users/jian/Dev/Career`

Collect all relevant files:
- resume(s)
- job posting(s)
- notes/interview prep docs
- prior narrative docs

Normalize into evidence blocks:
- role timeline
- projects/initiatives
- explicit metrics
- technologies/tools
- leadership/collaboration moments
- unresolved ambiguities to clarify

If job posting file is empty, mark `posting_status: missing_content` and continue with foundation.

---

## Phase 2 - Build agnostic foundation

Create `workspace/career/foundation/00_narrative_core.md`:
- 60-second intro
- 2-minute arc
- strengths and differentiators
- preferred next-role scope
- risk areas and mitigation statements

Create `workspace/career/foundation/01_story_bank.md`:
- 8-12 story cards with IDs
- for each story:
  - Situation
  - Task
  - Actions (what you did)
  - Results (with evidence)
  - tradeoffs
  - what you would do differently
  - reusable question triggers

Create `workspace/career/foundation/02_behavioral_cheatsheet_template.md`:
- fillable Q/A template
- answer slots for 1-line, 2-minute, deep-dive
- follow-up defense prompts (metrics, ownership, tradeoffs)

Create `workspace/career/foundation/03_question_bank_agnostic.md`:
- ownership/ambiguity
- execution/shipping
- technical leadership
- debugging/incidents
- collaboration/conflict
- learning/adaptability

---

## Phase 3 - Target extraction (company mode)

For `/career target <company>`:

1. Resolve `company_slug`.
2. Parse job posting and extract:
   - mission/culture language
   - role scope and title signals
   - hard skills
   - likely interview dimensions
   - likely red flags they are screening out
3. Infer likely rounds and expected test weight:
   - recruiter
   - hiring manager
   - coding
   - system design
   - behavioral/panel
   - exec/founder (if applicable)

If job posting is absent, generate a lightweight target pack with assumptions and explicit unknowns.

For `/career lean <company>`:

1. Reuse foundation and parsed posting signals.
2. Generate only:
   - `STUDY_PLAN.md`
   - `BEHAVIORAL_PRACTICE.md`
   - `AGNOSTIC_CONTEXT.md`
3. Keep the files concise and interview-practical.
4. In `BEHAVIORAL_PRACTICE.md`, include a clear "what to actually say" script section.
5. In `AGNOSTIC_CONTEXT.md`, keep durable context and interview-safe framing rules.

---

## Phase 4 - Curated target artifacts

Write under `workspace/career/targets/{company_slug}/`:

`00_job_posting_analysis.md`
- concise signal extraction
- what they optimize for
- what to emphasize/de-emphasize

`01_interview_round_map.md`
- likely rounds
- what each round evaluates
- your win conditions per round

`02_behavioral_questions_curated.md`
- tailored questions mapped to story IDs
- follow-up traps and defense points

`03_company_language_signal_bank.md`
- preferred phrases
- phrasing upgrades
- domain vocabulary to know
- smart questions to ask interviewers

`04_study_plan.md`
- day-based execution plan
- weighted by likely test surface
- concrete drills for behavioral/system design/coding/domain

`05_execution_checklist.md`
- final readiness checklist
- must-rehearse stories
- metrics to memorize

Lean mode write path:

- If a company workspace folder exists (for example `ASPIDA/`), prefer writing there.
- Otherwise write under `workspace/career/targets/{company_slug}/lean/`.

---

## Phase 5 - Study plan weighting logic

Use job posting signals to allocate effort:

- if system design heavy -> increase design reps and architecture drills
- if coding heavy -> increase DS&A cadence and timed mocks
- if domain heavy -> add domain crash course and terminology drills
- if vendor/integration heavy -> add integration architecture scenarios

Always produce:
- baseline weekly schedule
- compressed 7-day schedule
- last-48-hours plan

---

## Phase 6 - Quality checks

Before finalizing artifacts, verify:

- every curated behavioral question maps to at least one story ID
- each key story has measurable result + clear ownership
- each key story includes at least one tradeoff and one reflection point
- study plan contains explicit daily actions (not generic advice)
- assumptions are labeled when job posting data is incomplete

---

## Output conventions

- Keep files markdown and editable.
- Prefer concise bullets over long prose.
- Include dates/version metadata at top of each generated file.
- Keep agnostic foundation stable; avoid company-specific language there.

---

## Example run order

- `/career foundation`
- `/career target aspida`
- `/career rounds aspida`
- `/career behavior aspida`
- `/career plan aspida`
