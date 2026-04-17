# telecodex Agent Rules

## Role

- telecodex is transport and presentation, not orchestration truth.
- Keep Telegram-facing summaries concise, human-readable, and decision-useful.

## Ownership

- Do not make telecodex the source of truth for project routing, execution state, or next-step decisions.
- Reflect ORX state faithfully instead of inventing local workflow.

## Runtime

- Keep local runtime state under `.telecodex/`.
- Never commit env files, runtime databases, logs, or other machine-local telecodex artifacts.

## Verification

- When telecodex behavior changes, prefer verification on the real Telegram or launchd-managed runtime surface when practical.
