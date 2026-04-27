# Workspace Standards

Applies to everything under `/Users/jian/Dev/workspace` unless a deeper `AGENTS.md` overrides it.

## Ownership

- `workspace/*` is parent-owned by the `Dev` repo.
- Do not recreate nested `.git` repos under `workspace/`.
- Keep repo-specific behavior in deeper repo-level `AGENTS.md` files.

## Telecodex-Managed Flow

- Treat `Telegram -> telecodex -> Codex -> Linear` as the canonical remote-work flow.
- `Repos/telecodex` owns the Telegram transport, session controller, and presentation layer.
- Linear is the durable task graph and reviewable execution brief.
- `Repos/tmux-codex` owns the local tmux viewer/launcher. Its runner view reflects busy Telecodex sessions; it must not start background runner loops.

## Runtime Artifacts

- Treat `workspace/**/.memory/`, `workspace/**/.telecodex/`, `workspace/**/target/`, logs, caches, and browser/debug output as local runtime state.
- Do not commit local runtime state as project source.
- The only normal exception under `.memory/` is intentional checked-in guidance such as `lessons.md`.
- Prune stale runtime artifacts once they are no longer useful for recovery or audit.

## Verification

- Use `/Users/jian/.codex/docs/agent-harness.md` for proof classes, anti-flake rules, and durable guard decisions.
- Verify on the highest-signal live surface available, not tests alone, when behavior can be observed directly.
- Plan with proof in mind: for non-trivial work, think through how behavior will be proven, how confounding variables will be isolated, and how later failures will be localized before broad implementation.
- For fragile, stateful, or environment-sensitive workflows, prefer a replayable live verification asset over shallow proof. Use a script, scenario runner, workflow harness, frozen snapshot, or equivalent mechanism that exercises the real flow and can be rerun after future changes.
- Once a sensitive workflow is proven, preserve that known-good path as a reusable regression guard so later sessions can verify it without rediscovering the behavior from scratch.
- Treat flaky checks as harness defects or follow-up work, not acceptance evidence.
- Keep generated probes and temporary recovery artifacts only as long as they are still useful.
