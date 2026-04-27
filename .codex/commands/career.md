# /career - Agnostic Interview Narrative + Targeted Prep Engine

Resource Hint: sonnet

Purpose: Build a reusable interview narrative core, then tailor it to specific companies/job postings and generate a focused prep plan.

This replaces the older startup-only flow.

Detailed execution spec: `_reference_career.md`

---

## What this command now does

- Creates an agnostic narrative foundation you can reuse across interviews.
- Curates and cross-links resume, story bank, job posting, and question prep.
- Generates behavioral artifacts and a realistic interview-round breakdown.
- Produces a practical study plan based on expected test surface (coding, design, domain, etc).

---

## Command modes

`$ARGUMENTS` controls mode.

- `/career`
  - Full workflow: foundation + optional company tailoring.
- `/career foundation`
  - Build or refresh reusable core narrative artifacts only.
- `/career target <company>`
  - Build target pack for one company using job posting + foundation.
- `/career lean <company>`
  - Generate only 3 practical files: study plan, behavioral script, agnostic context.
- `/career plan <company>`
  - Generate only the study plan for that company.
- `/career rounds <company>`
  - Generate only interview round map + expected tests.
- `/career behavior <company>`
  - Generate only behavioral question set + fillable cheat sheet.
- `/career resume`
  - Continue from last checkpoint.
  - If lean files exist for the company, update them in place.
  - Continue behavioral work one question block at a time.

If `<company>` is omitted in target modes, infer it from the job posting title/content.

---

## Inputs

Default source folder:
- `/Users/jian/Dev/Career`

Reusable reference docs (preferred when present):
- `/Users/jian/Dev/workspace/career/narrative.md`
- `/Users/jian/Dev/workspace/career/style-notes.md`
- `/Users/jian/Dev/workspace/career/readme.md`

Expected source files (any subset):
- resume (`.pdf`, `.docx`, `.md`, `.txt`)
- job posting (`.md`, `.txt`, pasted notes)
- prior narrative/story notes
- interview notes

Practical company workspace (lean mode, update-in-place):
- `/Users/jian/Dev/{COMPANY}/STUDY_PLAN.md`
- `/Users/jian/Dev/{COMPANY}/BEHAVIORAL_PRACTICE.md`
- `/Users/jian/Dev/{COMPANY}/AGNOSTIC_CONTEXT.md`

If multiple job postings exist, ask once which one to prioritize.

---

## Outputs

Foundation (reusable across all companies):
- `workspace/career/foundation/00_narrative_core.md`
- `workspace/career/foundation/01_story_bank.md`
- `workspace/career/foundation/02_behavioral_cheatsheet_template.md`
- `workspace/career/foundation/03_question_bank_agnostic.md`

Target pack (company-specific):
- `workspace/career/targets/{company_slug}/00_job_posting_analysis.md`
- `workspace/career/targets/{company_slug}/01_interview_round_map.md`
- `workspace/career/targets/{company_slug}/02_behavioral_questions_curated.md`
- `workspace/career/targets/{company_slug}/03_company_language_signal_bank.md`
- `workspace/career/targets/{company_slug}/04_study_plan.md`
- `workspace/career/targets/{company_slug}/05_execution_checklist.md`

Lean output mode (`/career lean <company>`):
- `{company_folder}/STUDY_PLAN.md`
- `{company_folder}/BEHAVIORAL_PRACTICE.md`
- `{company_folder}/AGNOSTIC_CONTEXT.md`

Session state:
- `workspace/career/session.md`

---

## Core principles

- Foundation first, tailoring second.
- Stories are the primitive; bullets and metrics are evidence.
- Every claim should survive follow-up questions.
- Optimize prep around expected test surface, not generic grind.
- Keep outputs editable and modular for reuse.
- Optimize narrative for hiring-side risk reduction (trust, execution, fit).

## Conversation style (default)

- Run behavioral refinement one block at a time (single Q/A loop).
- Keep scripts concise, human, and in the candidate's own tone.
- Lead with STAR summary first, then add nuance only if asked.
- Prefer practical scripts over polished resume language.
- Be explicit, not implicit: explain why risk/constraints mattered.
- Keep claims easy to say out loud; avoid overlong wording.
- Use numbers only when the candidate wants them in the spoken version.
- Keep delivery candid and professional (no blame framing).
- Capture durable context in `AGNOSTIC_CONTEXT.md`; keep practice scripts in `BEHAVIORAL_PRACTICE.md`.
- For each answer block, make explicit which hiring risk it de-risks.
- Assume 30-45 minute interviews by default; prefer 45-60 second base answers and expand only on follow-up.

---

## Safety rails

Never:
- invent metrics or accomplishments
- overfit all stories to one company
- produce vague behavioral prompts with no story linkage

Always:
- separate agnostic narrative from company-specific tailoring
- map each curated question to at least one story ID
- include tradeoffs and reflection prompts in behavioral artifacts

---

## Quick flow

ingest -> normalize evidence -> build agnostic foundation -> parse job posting -> infer round/tests -> generate curated artifacts -> emit study plan

Resume flow (when artifacts already exist):

load existing files -> detect unfinished blocks -> continue next high-priority block -> refresh reusable context -> persist session
