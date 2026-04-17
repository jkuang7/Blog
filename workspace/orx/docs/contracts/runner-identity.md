# Runner Identity

## Primary identity

`runner_id` is the primary identity for a runner.

Bot tokens are attached credentials, not the runner identity.

## Runner fields

- `runner_id`
- `profile_name`
- `telegram_bot_token`, optional
- `tmux_namespace`
- `runtime_dir`
- `log_dir`
- `workspace_policy`
- `scope_policy`
- `enabled`

## Uniqueness rules

- one runner may have at most one Telegram bot token
- one Telegram bot token may be attached to at most one runner
- token rotation must not change `runner_id`
- a runner may exist without Telegram

## Operational rules

- startup fails if duplicate token assignment exists
- startup fails if another live process owns the same runner
- startup fails if a runner profile is incomplete

## Pause and lease policy

Paused runs must not monopolize work indefinitely.

Phase 1 policy:

- pausing may keep the lease only for a fixed pause TTL
- after TTL expiry, the lease becomes stealable
- resume after expiry must reacquire the lease

This prevents starvation across concurrent runners.
