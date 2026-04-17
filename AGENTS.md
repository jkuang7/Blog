# Global Agent Standards (`~/Dev`)

Applies to everything under `/Users/jian/Dev` unless overridden by a deeper `AGENTS.md`.

## Keep This File Token-Efficient

- Keep only guidance that must be satisfied before a full lint/verify pass.
- Do not restate rules already enforced by repo lint/build hooks.

## Commit Requests

- Use worktrees by default for new feature/problem work; `main` is the integration target, not the default execution surface.
- When the user says `commit`, promote the conversation-relevant changes onto `main`.
- Prefer cherry-picking or otherwise replaying only relevant changes onto `main`; do not blindly merge unrelated branch state.
- Resolve merge/cherry-pick conflicts using conversation intent and current `main` behavior.
- Keep scope selective: include only changes needed for the user request.
- After promotion, restore a clean ownership state: canonical checkout on `main`, feature worktree on its branch.
- Do not disturb unrelated branches, stashes, worktrees, or local resource directories unless explicitly asked.

## Task Start

- If the repo defines an LLM or harness contract, follow its loading order exactly.
- Reuse an already-running app or browser when suitable.
- Start new feature/problem work from a worktree by default, not the main checkout.

## ORX / Linear Flow

- Treat `Telegram -> telecodex -> ORX -> Linear -> tmux-codex runner` as the canonical control flow.
- telecodex is transport and presentation, not the source of truth for project selection or execution state.
- ORX owns deterministic orchestration:
  - intake
  - decomposition
  - project routing
  - bot assignment
  - dependency checks
  - queueing
  - recovery
- Linear is the durable task graph and the reviewable execution brief.
- tmux-codex `runner-<project>` sessions are the only canonical execution sessions for ORX-managed work.
- Do not reintroduce raw `orx-*` executor sessions or local task-file selection into the active runtime path.

## Linear Ticket Contract

- Runnable Linear leaf tickets should be stateless enough for medium-tier Codex execution.
- Prefer ticket bodies that stand on their own:
  - problem
  - goal
  - scope
  - requirements
  - acceptance criteria
  - execution context
  - verification expectations
- Keep execution context in the ticket itself when it is stable and reviewable:
  - project key
  - repo root
  - worktree or packet context
  - branch intent
  - relevant dependencies and risks
- Do not assume prior Codex chat memory when drafting or refining runnable tickets.

## Execution Ownership

- Keep the control plane low-coupling and high-cohesion:
  - ORX decides what should run
  - Linear records what the work is
  - tmux-codex runner executes it
  - telecodex reports it
- Prefer one runner session per project.
- When work spans multiple tightly related tickets, prefer one shared packet execution context until the packet is complete.
- Keep final integration explicit under HIL:
  - merge to `main`
  - or cherry-pick from the packet branch to `main`
- Avoid silent auto-merge behavior for multi-ticket packets.

## Early Non-Lint Contracts

- Delete dummy resources created during testing once no longer needed, unless the user wants them kept.

## Bug Fix Loop

Loop: analyze context -> hypothesis -> smallest fix -> reproduce and verify with the appropriate execution surface -> repeat until fixed

Verification must include post-fix execution evidence from the relevant surface, such as:

- app/runtime behavior
- browser automation (for example Playwright or browser MCP flows)
- repo-specific harnesses/tools
- tests/logs

After the fix is confirmed, add targeted regressions when the behavior is worth protecting.

## `.memory/lessons.md`

Use only for non-testable knowledge:

- constraints
- failure signatures
- rationale and tradeoffs
- safe-change playbooks
- tooling quirks

Rules:

- Not a changelog, bug diary, or test index.
- Keep it DRY and current.
- Do not create new `.memory/lessons.md` files outside `/Users/jian/Dev/Repos/<project>*`.
- If something becomes testable, move it to tests.

## Refactors

- No behavior change unless requested.
- Use git history when needed to avoid known failure modes.

## React Design

- Prefer: Reusability, Maintainable, Scalable
- Your changes should not conflict with the styling of the system or the design structure
- Prefer small, cohesive feature components over god components.
- Separate pure render pieces from stateful orchestration when it improves clarity.
- Preserve stable naming, file ordering, and grep-friendly exports/test IDs.
- Before UI/UX changes, trace the owner chain far enough to understand props, composition, and shared styles.
- For CSS/layout/visual system changes, assess cascade and reuse impact before editing so fixes happen at the right layer.
