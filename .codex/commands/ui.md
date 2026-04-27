---
model: opus
---

Resource Hint: sonnet

# /ui - Stitch-First Visual Work

> **Entry point for visual UI work.** Freeze a reviewable visual baseline, prove it on a lightweight contract surface when needed, then promote it into production UI code.
> **Uses**: `~/.codex/docs/agent-harness.md` for proof classes, verification budget, and anti-flake policy.

## Purpose

Use `/ui` when the requested work materially changes layout, styling, spacing, hierarchy, theme, responsiveness, modal/page composition, or reusable visual grammar.

`/ui` owns:

- current-surface inspection
- Stitch prompt and design artifact creation when the visual target is not already fixed
- contract-candidate implementation for shared visual patterns
- production UI implementation after the target is approved or already known
- focused visual verification

Non-visual UI logic or copy-only changes can skip Stitch, but still need the closest reliable verification surface.

## Current Workflow

1. Read the repo, current UI, local design system, and relevant constraints before editing.
2. Capture the current state with screenshots or a focused browser check when the surface exists.
3. If the visual target is ambiguous, use Stitch and store the run under `/Users/jian/Dev/.codex/stitch-runs/<repo>/`.
4. If the change introduces or alters a shared visual pattern, first prove the candidate on the repo's lightweight contract/demo surface when one exists.
5. Stop for human approval only when the user needs to choose between concrete visual options.
6. Promote the accepted target into production code using the repo's existing architecture.
7. Verify the risky slice with the smallest high-signal check: screenshot, DOM geometry, browser flow, or repo-owned harness.
8. If follow-up implementation work should be queued, create or update Linear tickets through `/add`; do not route to the retired local planning commands.

## Budget Rules

- Default Stitch budget: one generation plus up to two edits, or one two-variant comparison when comparison is needed.
- Prefer editing an existing screen over generating from scratch.
- Avoid broad screenshot diffs for volatile full pages.
- Prefer deterministic Playwright/browser scripts, DOM geometry checks, and fixture-backed contract captures for replayable proof.
- Escalate to broad lint/typecheck/unit/E2E only when shared owners changed materially or targeted checks cannot prove the risk.

## Outputs

| Artifact | Where | When |
|----------|-------|------|
| `request.md` | `.codex/stitch-runs/<repo>/<timestamp>-<slug>/` | Before Stitch calls |
| `before/` | Same run folder | Before changing visuals |
| `reference/` | Same run folder | Frozen Stitch-approved reference |
| `after/` | Same run folder | Candidate or rebuilt result |
| `status/*.md` / `status/*.json` | Same run folder | As the visual loop progresses |
| Production UI code | Target repo | After approval or when target is already known |

Generated Stitch HTML is reference material only. Do not paste it directly into production.

## Fallback

If Stitch is unavailable, rate-limited, or unauthenticated, still capture the current surface, write the intended visual target, build the smallest representative candidate locally, and verify it against the user-approved target before production rollout.
