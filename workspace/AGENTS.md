# Workspace Standards

Applies to everything under `/Users/jian/Dev/workspace` unless a deeper `AGENTS.md` overrides it.

## Ownership

- `workspace/*` is parent-owned by the `Dev` repo.
- Do not recreate nested `.git` repos under `workspace/`.
- Keep repo-specific behavior in deeper repo-level `AGENTS.md` files.

## ORX-Managed Flow

- Treat `Telegram -> telecodex -> ORX -> Linear -> tmux-codex runner` as the canonical control flow.
- telecodex is transport and presentation.
- ORX is the orchestration and decision layer.
- Linear is the durable task graph and reviewable execution brief.
- tmux-codex executes bounded slices in the runner session selected by ORX.

## Runtime Artifacts

- Treat `workspace/**/.memory/`, `workspace/**/.telecodex/`, `workspace/**/.orx/`, `workspace/**/target/`, logs, caches, and browser/debug output as local runtime state.
- Do not commit local runtime state as project source.
- The only normal exception under `.memory/` is intentional checked-in guidance such as `lessons.md`.
- Prune stale runtime artifacts once they are no longer useful for recovery or audit.

## Verification

- Verify on the highest-signal live surface available, not tests alone, when behavior can be observed directly.
- Keep generated probes and temporary recovery artifacts only as long as they are still useful.
