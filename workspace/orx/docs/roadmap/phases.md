# Phases

## Phase 1: Core coordination layer

Build:

- repo scaffold
- shared storage API
- shared SQLite backend
- runner registry
- global leases
- command queue
- run state machine
- slice journal

Test:

- transactional lease acquire/release
- duplicate runner-token rejection
- concurrent selection race tests
- restart persistence

## Phase 2: Canonical issue mirror

Build:

- Linear mirror
- canonical issue normalization
- machine metadata block support
- executability gate
- total ranking order

Test:

- normalization tests
- parent/child precedence tests
- dependency gating tests
- ranking tie-break tests

## Phase 3: Executor substrate

Build:

- tmux lifecycle manager
- workspace resolver
- bounded slice runner
- slice result contract
- mutation journal hooks

Test:

- interrupt/resume tests
- stale session recovery tests
- replay-safe recovery tests

## Phase 4: Telegram runner transport

Build:

- profile-bound Telegram adapter
- queue-by-default behavior
- `/steer`
- `/stop`
- queued edit support
- token ownership checks
- cutover and rollback scripts

Test:

- queue FIFO tests
- edit-before-consume tests
- token conflict tests
- direct takeover smoke tests

## Phase 5: Proposal and mutation pipeline

Build:

- typed AI proposal contracts
- stage/tier contract for planning, decomposition, and execution
- deterministic validators
- HIL gates
- Linear mutation applier
- deterministic follow-up routing

Test:

- split/refine validator tests
- mutation idempotency tests
- HIL policy tests
- follow-up routing tests

## Phase 6: Watchdogs and recovery

Build:

- process watchdog
- run watchdog
- transport watchdog
- recovery API
- retry and backoff policy
- retention classes

Test:

- failure-injection tests
- stuck-loop escalation tests
- stale lease expiration tests
- partial mutation recovery tests

## Phase 7: Migration and deprecation

Build:

- controlled cutover path
- compatibility wrappers where useful
- migration notes
- deprecation path for old systems

Test:

- local shell E2E
- Telegram E2E
- ShellFish E2E
- concurrent multi-runner E2E
