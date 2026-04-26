---
model: opus
---

# /add - Telecodex Linear Intake

Use this local prompt only as a compatibility wrapper for Telecodex-managed Linear planning.

Canonical `/add` intake is:

```text
Telegram -> Telecodex -> Linear
```

`/add` means the user wants a plan materialized as Linear ticket(s). It does not queue local tmux-codex work, launch a background loop, or mutate tmux sessions.

## Behavior

1. Read the request and current conversation context.
2. Infer the target repo or project from the request and workspace state.
3. If the request spans multiple independent projects or repos, create separate feature tickets plus a coordination ticket when useful.
4. If the request is one coherent project, create one self-contained feature ticket with ordered phase comments.
5. Use `telecodex:phase` comments so `/run` and `/review` can resume from Linear without chat memory.
6. Include actual Linear URLs for every created or updated issue, formatted as Markdown links such as `[PRO-270](https://linear.app/jkprojects/issue/PRO-270)`.
7. Stop after planning/materializing Linear work. Do not implement code from this command.

If the target project cannot be inferred safely, ask one concise question instead of creating an ambiguous ticket.

## Ticket Requirements

Each feature ticket should include:

- original request and relevant context
- target repo name and absolute path
- goal, non-goals, constraints, risks, and dependencies
- definition of done
- scientific proof plan
- ordered execution plan
- recovery notes for a future no-memory `/run`

Each phase comment must begin with the standard marker:

```md
<!-- telecodex:phase id="phase-01" status="ready" depends="" branch="feature/PRO-264-short-slug" worker="" lease_expires_at="" proof="" commit="" -->
```

Keep phases small enough for one future `/run` to execute one coherent slice.
