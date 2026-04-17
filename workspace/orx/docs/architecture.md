# Architecture

## Goal

Build a deterministic multi-runner orchestration system that:

- reads work from Linear issues and Linear project/team state
- coordinates multiple concurrent runners safely
- uses tmux + Codex TUI as a bounded executor
- accepts commands from Telegram, local shell, local Codex, and ShellFish
- persists follow-up work instead of losing it in chat text

## Core decisions

- `runner_id` is the primary runner identity.
- Telegram bot tokens are unique runner attachments, not the primary identity.
- One shared coordination store owns leases, runs, issue mirrors, journals, and queues.
- Selectors operate only on a canonical normalized issue model.
- AI produces typed proposals only; deterministic validators decide whether to apply, reject, or require HIL.
- Every executor slice is journaled for replay-safe recovery.

## Stage and tier contract

ORX owns the stage model for `/add` and execution handoff:

- `planning`
  - decides routing, split vs single-ticket shape, and whether HIL is required
  - defaults to `xhigh`
  - may downgrade to `high` only for a simple single-ticket intake routed to one project
- `decomposition`
  - turns an approved plan into implementation-ready Linear ticket capsules
  - defaults to `high`
- `execution`
  - runs a decision-complete leaf ticket inside `tmux-codex`
  - defaults to `medium`

That contract is deterministic ORX policy. `telecodex` may transport it and Codex may execute within it, but neither owns the policy.

## Top-level components

### Control plane

Owns:

- run state machine
- global lease acquisition and expiry
- deterministic issue selection
- queue draining
- pause, stop, and steer behavior
- recovery actions

### Linear sync

Owns:

- mirroring Linear issue and project state
- canonical issue normalization
- deterministic ranking inputs
- Linear mutation application

### Executor

Owns:

- tmux session lifecycle
- workspace acquisition
- Codex TUI execution of a bounded slice
- heartbeat emission
- slice result emission

The executor never owns global orchestration transitions.

### Transports

Own:

- parsing external commands
- associating commands with a runner transport session
- rendering status and approvals

Transports do not own issue selection or lifecycle logic.

## Shared coordination store

Phase 1 uses a shared SQLite database behind a storage API.

The storage API is required so the coordination backend can later move to Postgres if multi-host scaling is needed.

The shared store owns:

- runner registry
- canonical issue mirror
- global leases
- runs
- slice journal
- mutation journal
- command queue
- approvals
- artifacts
- health heartbeats
- recovery actions

## Deterministic control flow

### `/run`

1. Reconcile runner-local health and shared control state.
2. Resume an owned recoverable run if one exists.
3. Otherwise rank globally executable issues.
4. Attempt transactional lease acquire.
5. Dispatch a bounded executor slice only after lease success.
6. Ingest slice result.
7. Apply deterministic transition rules.

### `/add`

1. Normalize request.
2. Dedupe against canonical issues, active runs, queued commands, and follow-up proposals.
3. Classify request intent.
4. If interpretation is needed, request a typed AI proposal.
5. Validate and either apply, reject, or require HIL.

### `/stop`

- Interrupt current executor.
- Pause the run.
- Hold or release the lease according to pause TTL policy.

### `/steer`

- The only interrupting operator command.
- Flush queued steer commands in order into the active run.

## Deterministic follow-up policy

When a slice discovers more work:

- required and in current scope -> continue current issue
- required but out of scope in same feature line -> create child issue
- required across repo or system boundary -> create dependency issue
- adjacent improvement only -> create sibling follow-up
- risky or ambiguous -> require HIL

## Replay-safe recovery

Every slice must have:

- start record
- completion record
- mutation records for external side effects

Recovery must never blindly replay an unknown slice.
