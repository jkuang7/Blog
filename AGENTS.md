# Global Agent Standards (`~/Dev`)

Applies to everything under `/Users/jian/Dev` unless a deeper `AGENTS.md` overrides it.

## Keep This File Lean

- Keep only guidance that materially changes behavior before lint, verify, or review.
- Do not restate rules already enforced by repo tooling.

## Default Posture

- Be context-driven, not optimistic.
- Treat implementation as provisional until the intended behavior is observed on the right live surface.
- Before implementing, gather context on the actual situation:
  - load the repo's required agent or harness context in the required order
  - inspect the relevant code, ownership boundaries, and current constraints
  - reuse a running app or browser when useful
  - reproduce the bug or inspect the live surface when behavior or layout is in question
- Do not jump into code just because a likely fix seems obvious. Understand what owns the behavior first.
- Prefer implementations that are easy to test, easy to observe, and easy to maintain.
- Once the situation is clear, execute end-to-end without pausing for obvious next steps.

## Task Start

- If the repo defines an LLM or harness contract, follow its loading order exactly.
- Start new feature or problem work from a worktree by default; `main` is the integration target, not the default execution surface.
- Reuse an already-running app, browser, or local service when suitable.

## Response Style

- Prefer a conversational engineer-to-engineer tone.
- Use structure only when it improves comprehension.
- Put the short summary at the bottom for substantive responses, including plans.
- Keep the bottom summary brief and high signal:
  - what changed, decided, or was found
  - what matters next
  - real blockers only
- For reviews, keep findings first and end with a short bottom-line summary.

## Commit Requests

- When the user says `commit`, promote only the conversation-relevant changes onto `main`.
- Prefer cherry-picking or replaying the intended changes; do not blindly merge unrelated branch state.
- Resolve merge or cherry-pick conflicts using conversation intent and current `main` behavior.
- After promotion, restore a clean ownership state: canonical checkout on `main`, feature worktree on its branch.
- Do not disturb unrelated branches, stashes, worktrees, or local resource directories unless explicitly asked.

## Stitch-First Visual Work

- For UI-affecting work, use the `stitch-first-ui` skill instead of freehanding visual changes in code first.
- Treat this section as policy only; keep the detailed Stitch workflow, artifacts, prompting, and budget rules in the skill.
- Required policy:
  - gather context and inspect the current UI first
  - use Stitch before production visual code changes
  - get human approval in Codex before extracting `STYLE.md` or implementing
  - use approved `STYLE.md` to drive `PLAN.md` and implementation
  - archive generated design images into the run folder and clean up stray design-loop image files after verification
- Never paste generated Stitch HTML directly into production.
- This does not enforce project-level design tokens here; `create-t3-jian` remains the scaffold-level opinion source.

## ORX / Linear / Runner

- Treat `Telegram -> telecodex -> ORX -> Linear -> tmux-codex runner` as the canonical control flow.
- telecodex is transport, not the source of truth.
- ORX owns orchestration:
  - intake, decomposition, routing, dependency checks, queueing, recovery, and execution-tier choice
- Linear is the durable task graph and reviewable execution brief.
- tmux-codex `runner-<project>` sessions are the canonical execution sessions for ORX-managed work.
- Do not reintroduce raw `orx-*` executor sessions or local task-file selection into the runtime path.

## Linear Ticket Contract

- Runnable leaf tickets should be stateless enough for medium-tier Codex execution.
- Prefer ticket bodies that stand alone:
  - objective, why, goal, scope, constraints, ordered steps, verification, stopping conditions, escalation guidance
- Keep stable execution context in the ticket when it is reviewable:
  - project key, repo root, worktree or packet context, branch intent, dependencies, risks
- Keep one mutable `Latest Handoff` section with:
  - current status
  - what changed
  - blockers, risks, lessons
  - next direction
  - selected execution tier and why
- Do not assume prior chat memory when refining runnable tickets.

## Execution Ownership

- ORX decides what runs next.
- Linear records what the work is.
- tmux-codex runner executes it.
- telecodex reports it.
- Executor slices should report facts:
  - what changed
  - what was verified
  - blockers, risks, lessons
- ORX interprets those facts, updates the active Linear ticket, and decides continue, block, split, reroute, or follow-up.
- Prefer one runner session per project.
- Prefer one shared packet context when tightly related tickets should stay together.
- Keep final integration explicit under HIL: merge or cherry-pick to `main`; do not silently auto-merge multi-ticket packets.

## Tier Routing

- Default to `medium` for runnable leaves.
- Escalate to `high` when execution uncovers owner mismatch, concrete blockers, verification failure, or multiple live risks.
- Escalate to `xhigh` when execution uncovers scope mismatch, resequencing need, or ambiguity large enough that ORX must replan before redispatch.
- Do not put model-tier authority back into local runner prompts or local runner state.

## Verify, Then Claim

- The source of truth is verifiable behavior on the relevant live surface, not that the code change looks correct.
- Use the loop: analyze context -> form a hypothesis -> make the smallest correct change -> run a live smoke test or equivalent observable verification -> inspect the result -> repeat until resolved.
- Do not stop at implementation if verification has not happened yet, or if verification exposes gaps. Continue until the behavior works or a real blocker is identified.
- Choose the highest-signal verification surface available:
  - app or runtime behavior
  - browser automation for UI flows
  - CLI or API invocation for non-UI behavior
  - logs, traces, or metrics when the effect is indirect but observable
  - repo-specific harnesses, tests, or other automated checks
- Passing tests or static checks alone is not sufficient when a live surface can be exercised.
- If the behavior is hard to observe, first add or use the smallest practical observability seam so correctness can be proven.
- For high-risk or uncertain work, prefer a throwaway probe, script, fixture, or runner to validate a small slice before reintegrating the final change. Remove temporary verification code when it is no longer needed unless the user wants it kept.
- After a non-trivial fix, add a targeted regression when the behavior is worth protecting.
- Delete dummy resources created during testing once no longer needed, unless the user wants them kept.

## `.memory/lessons.md`

- Use it only for non-testable knowledge:
  - constraints
  - failure signatures
  - rationale and tradeoffs
  - safe-change playbooks
  - tooling quirks
- It is not a changelog, bug diary, or test index.
- Keep it DRY and current.
- Prefer project-local lessons under `/Users/jian/Dev/Repos/<project>*/.memory/lessons.md`.
- Exception: `/Users/jian/Dev/workspace/tmux-codex/.memory/lessons.md` may hold runner/control-plane guidance that is specific to tmux-codex itself.
- If something becomes testable, move it to tests.

## Runner Memory

- Treat `.memory/runner` files as cache or recovery breadcrumbs, not truth, for ORX-managed work.
- `.memory/**` is local runtime state and should not be committed as project source, except for intentional checked-in guidance files such as `lessons.md`.
- Allowed local runner memory:
  - active issue or worktree snapshot
  - runner status
  - append-only ledger
  - cached last ORX execution packet
- If ORX selects a new issue or packet, stale local runner files must not override that selection.
- The durable execution brief belongs in Linear; hot orchestration state belongs in ORX.

## Refactors and UI Code

- Refactors should not change behavior unless requested.
- Use git history when needed to avoid known failure modes.
- Prefer reusable, maintainable, scalable React structures.
- Prefer small cohesive feature components over god components.
- Separate pure render pieces from orchestration when it improves clarity.
- Preserve stable naming, file ordering, and grep-friendly exports or test IDs.
- Before UI or UX changes, trace the owner chain far enough to understand props, composition, and shared styles.
- For CSS, layout, or visual system changes, fix the owning layer instead of patching leaves blindly.
