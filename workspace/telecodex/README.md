# Telecodex for Local Codex on Mac mini

This repo is a local adaptation of [`Headcrab/telecodex`](https://github.com/Headcrab/telecodex) for one job: use Telegram as the low-noise remote control surface for the local ORX + Linear + tmux-codex control plane running on macOS.

The intended flow is:

- Telegram chat command
- `telecodex` transport / formatting
- ORX deterministic orchestration
- Linear issue graph + issue metadata
- tmux-codex live session + worktree execution

Current contract:

- each bot is tied to one Telegram chat
- `/add` is global intake from any allowed Telegram chat and may decompose into multiple Linear issues across projects
- `/run` asks ORX to pick runnable work and an available bot lane
- `telecodex` is transport/presentation only; it does not choose work locally

## What changed locally

- persisted per-session queue in SQLite so inbound Telegram turns survive process restarts
- explicit session runtime states: `idle`, `planning`, `coding`, `running`, `waiting_approval`, `blocked`, `interrupted`, `completed`, `failed`
- persisted status-message tracking so each Telegram session can reuse one editable status card
- launchd-first local ops with repo-local scripts and logs

## Local layout

- `.env`
- `.telecodex/data/telecodex.sqlite3`
- `.telecodex/runtime/telecodex.toml`
- `.telecodex/logs/telecodex.out.log`
- `.telecodex/logs/telecodex.err.log`
- `bin/start`
- `bin/stop`
- `bin/restart`
- `bin/status`
- `bin/logs`
- `bin/healthcheck`

## First-time setup

1. Copy `.env.example` to `.env`.
2. Set `TELEGRAM_BOT_TOKEN`.
3. Set `TELEGRAM_ALLOWED_USER_ID` to your Telegram numeric user id.
4. Adjust `DEFAULT_CWD` if you want a different starting workspace.
5. `CODEX_BINARY=codex` is resolved to an absolute path when the runtime config is rendered, so launchd does not depend on ambient `PATH`.
6. Run `bin/healthcheck`.
7. Run `bin/start`.
8. Send `/login` to the bot in Telegram and complete Codex device auth.

## Multiple bots

One checkout can now host multiple Telecodex bots side by side.

- Default profile uses the existing unprefixed env vars and LaunchAgent label `dev.jian.telecodex`.
- Named profiles use `<PROFILE>_...` env vars from `.env`, plus a separate LaunchAgent label, config, SQLite DB, and logs.
- Commands accept `--profile <name>` for service operations.

Example:

```bash
./bin/start --profile runner
./bin/status --profile runner
./bin/logs --profile runner -f

./bin/start --profile misc
```

Profile isolation:

- `runner` => `dev.jian.telecodex-runner`
- runtime dir => `.telecodex/profiles/runner/`
- config => `.telecodex/profiles/runner/runtime/telecodex.toml`
- db => `.telecodex/profiles/runner/data/telecodex.sqlite3`

## Telegram flow

- Send any normal message to continue the current session in that chat.
- Use `/new` to force a fresh Codex thread for the same Telegram chat.
- Use `/status` to inspect the mapped session, queue depth, and runtime state.
- If the session `cwd` points at a `tmux-codex` workspace, `/status` also shows the local runner and active Linear issue summary so BentoBox can check progress without opening the shell.
- `/add` sends intake to ORX and may create one or more Linear issues after approval.
- `/run` asks ORX to choose the next runnable project/issue and the bot lane that should own it.
- Use `/sessions` and `/use <thread-prefix|latest>` to resume a previous Codex thread.
- Use `/stop` to interrupt the active turn.
- When Codex asks for approval, Telegram shows an approval card and the session enters `waiting_approval`.
- New Telegram sessions default to `gpt-5.4-mini` with `medium` reasoning for cheap control-plane chat.
- Planning and execution-style turns such as `/plan`, `/run`, `/spec`, `/integrate`, `/refactor`, `/enhance`, `/commit-main`, and `/review` auto-promote to the execution profile (`gpt-5.4` with `high` reasoning). `/kanban` remains as a legacy alias for `/run`.
- Explicit `/model` and `/think` session overrides still win over the automatic execution profile.

## Service flow

- `bin/start` builds if needed, renders config, installs the LaunchAgent, and starts it.
- `bin/stop` unloads the LaunchAgent and watchdog, and clears any stale per-profile poller lock.
- `bin/restart` reloads it.
- `bin/status` prints launchd state, local paths, lock state, and the resolved Codex binary path.
- `bin/logs -f` tails the service logs.
- All service commands also accept `--profile <name>`.

LaunchAgent label:

- `dev.jian.telecodex`

Plist path:

- `~/Library/LaunchAgents/dev.jian.telecodex.plist`

## Security defaults

- no public webhook
- local long-polling only
- fail closed if required secrets are missing
- access restricted to the seeded Telegram user id unless you explicitly allow more users later

## Verification

Local verification completed in this repo:

- `cargo test --no-run`
- targeted store/session persistence tests
- script syntax checks and config rendering checks

Full Telegram end-to-end verification still requires a real bot token and first message from the allowed Telegram account.
