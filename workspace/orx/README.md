# ORX

This checkout is the current local restore snapshot for ORX. Treat the working tree here as the
immediate source of truth until the canonical upstream remote is recovered and reattached.

ORX is the single always-on control plane for the Mac mini.

It owns:
- runner selection and queue semantics
- tmux-backed session continuity
- durable proposal and recovery state
- Telegram-facing loopback API
- SSH/local operator controls

It does not hand planning authority back to `tmux-codex`, `telecodex`, or ad hoc shell flows.

## What ORX Replaces

If `tmux-codex` alone is an infinite runner for one manually chosen task, ORX is the kanban-driven version of that system.

The operating model is:
- Linear is the work ledger and the source of truth for what exists, what is blocked, and what is next.
- Telegram is the normal remote control path.
- `telecodex` is the Telegram adapter, not the planner.
- ORX is the orchestrator that reads durable work state, selects runnable leaf work, owns continuity, and applies control semantics.
- `tmux-codex` is the execution adapter, not the planner.
- tmux is the session substrate that keeps long-lived runs inspectable and recoverable.

Deterministic boundaries:

- each registered bot is tied to one Telegram chat/thread lane
- `/add` is global intake and may decompose into multiple Linear issues across projects
- `/run` is a scheduling request; ORX chooses the runnable project and available bot deterministically
- Codex may advise decomposition/classification, but ORX validates before mutating Linear or assignment state

## Tiered Planning Contract

ORX now owns a deterministic stage/tier contract for Linear-native intake:

- `planning`
  - purpose: interpret raw `/add`, decide routing, decide split vs single ticket, and decide whether HIL is required
  - default: `gpt-5.4` at `xhigh`
  - downgrade: a simple one-ticket intake routed to one project may drop to `high`
- `decomposition`
  - purpose: turn an approved plan into implementation-ready Linear ticket capsules
  - default: `gpt-5.4` at `high`
- `execution`
  - purpose: execute a decision-complete Linear leaf ticket in `tmux-codex`
  - default: `gpt-5.4` at `medium`

The contract is carried in ORX intake plans as `stage_contract`. That keeps the policy in ORX itself instead of leaving it implicit in `telecodex` defaults or chat memory.
ORX also flattens the selected planning, decomposition, and execution tier metadata onto each intake record so downstream API consumers and created Linear tickets do not have to reverse-engineer it from the plan body.

In short:

```text
Telegram
  -> telecodex
    -> ORX
      -> tmux-codex
        -> tmux session
          -> Codex execution
```

The important distinction from plain `tmux-codex` is that the next slice is not chosen ad hoc from a shell prompt.
ORX is supposed to pull from Linear, keep the execution trail durable, and let Telegram and SSH/local control the same underlying runner state.

## Dispatch Model

ORX now has two layers:

- one global dispatch surface that arbitrates the next runnable Linear leaf across managed projects
- one project-scoped runtime per project that owns execution, continuity, leases, and tmux session mapping for that project

The important control rule is:

- any Telegram `/run` calls ORX dispatch
- ORX decides which project owns the selected ticket
- ORX chooses which bot/chat lane may own the project without reusing a busy bot for unrelated active work
- the ingress bot sends only the handoff acknowledgement when another project owns the work
- the owning project bot becomes the long-lived status and conversation surface
- Telegram `/add` is global intake and may route to one or more projects after decomposition
- if `/add` looks wrong-project, cross-project, or oversized, ORX should propose reroute, split, or clarification before mutating Linear

This keeps selection global while keeping execution isolated per project.

## How To Use ORX

Use ORX as the always-on kanban worker for the Mac mini:

1. Keep the Linear API key in repo `.env`.
2. Bootstrap the ORX runtime once.
3. Run the ORX daemon continuously.
4. Let Telegram commands flow through `telecodex` into the local ORX API.
5. Let ORX choose or materialize the next Linear-backed slice and dispatch it into a tmux-backed `tmux-codex` session.
6. Use the ORX operator CLI for inspection, takeover, and recovery when needed.

This means:
- do not use `tmux-codex` as a separate planner
- do not treat Telegram as a second source of task truth
- do not bypass ORX when you want queue, continuity, validation, or takeover semantics

## Quick Start

First bootstrap and verify the local host:

```bash
orx --json init
orx --json doctor
```

Expected result:
- runtime paths exist
- sqlite is bootstrapped
- tmux is available
- the Linear key is detected from repo `.env`

Then start the always-on surfaces:

Terminal 1:

```bash
orx --json api serve
```

Terminal 2:

```bash
orx --json daemon run
```

That is the ORX equivalent of an infinite runner loop:
- `api serve` is the local ingress for `telecodex`
- `daemon run` is the always-on orchestration loop that continues active project work and starts the next runnable project ticket when a project runtime becomes idle

In normal operation:
- Telegram talks to `telecodex`
- `telecodex` calls the local ORX API
- ORX updates queue/continuity/proposal state
- `/add` intake is project-default-first with HIL approval before ticket creation
- executors report structured slice results back into ORX through `/slice-results`
- a `success` + `verified=true` + `next_slice=null` result finalizes the active issue and lets the daemon drain into the next runnable ticket for that project
- ORX dispatches work into tmux-backed executor sessions
- `tmux-codex` keeps the actual Codex run resident in tmux

## Daily Operator Flow

For a normal day-to-day loop, use this sequence:

1. Check host readiness.

```bash
orx --json doctor
```

2. Start or confirm the API and daemon are up.

```bash
orx --json api serve
orx --json daemon run
```

3. Inspect runners and daemon state.

```bash
orx --json operator runners
orx --json operator daemon
```

4. Inspect the active queue or a specific issue/runner pair.

```bash
orx --json operator queue --runner-id runner-a
orx --json operator status --issue-key PRO-29 --runner-id runner-a
```

5. If needed, inspect the tmux session ORX is controlling.

```bash
orx --json operator attach-target --runner-id runner-a
orx --json operator pane --runner-id runner-a --lines 80
```

6. Only if you need to intervene locally, take over explicitly before mutating control state.

```bash
orx --json operator takeover --issue-key PRO-29 --runner-id runner-a --operator-id jian --reason "Investigate locally"
orx --json operator control --kind steer --issue-key PRO-29 --runner-id runner-a --operator-id jian --payload-json '{"instruction":"switch direction"}'
orx --json operator return-control --issue-key PRO-29 --runner-id runner-a --operator-id jian --note "Done"
```

## Mental Model

Think of the layers this way:

- Linear answers: what work exists and what is the next runnable leaf
- Telegram answers: what remote control command did the human send
- ORX answers: what should happen to queue, continuity, proposals, validation, and recovery state
- `tmux-codex` answers: how do we run Codex in a persistent tmux-backed executor session
- tmux answers: where does the long-lived session live and how do we inspect or reattach

If a human can bypass ORX and still do the operation, that is a recovery path, not the normal path.
The normal path is always Telegram or SSH/local control into ORX, with ORX owning the durable state transitions.

## Crash Continuity

ORX should assume Codex process memory is disposable.

If Codex crashes, restarts, or loses prior thread memory, the system should still be able to continue work from durable state instead of relying on chat history or model recall.

That means recovery must come from three durable layers:
- Linear stores the canonical work intent: project assignment, issue description, acceptance shape, dependencies, and decomposition trail.
- ORX stores the execution continuity: active slice, next slice, validation plan, last verified delta, failure signatures, artifacts, and resume context.
- The project runtime stores the project identity: `project_key`, `repo_root`, owning bot, tmux session namespace, and Linear team/project mapping.

The important rule is:
- Linear should hold the durable task truth.
- ORX should hold the durable execution truth.
- Neither should assume the model remembers anything that is not persisted.

For this reason, any restart-safe execution path should be reconstructible from:
- the Linear ticket
- the ORX continuity record
- the project registry/runtime mapping
- the current repo state on disk

Not from prior chat memory alone.

The practical recovery surface for that is the restart context pack:

```bash
orx --json dispatch context --project-key <project-key>
orx --json dispatch drift --project-key <project-key>
```

That bundle should be enough for a fresh Codex process to recover:
- which project runtime it belongs to
- which Linear issue is active
- what the last verified delta was
- what the next slice should be
- what tmux session/pane owns the current run
- whether ORX thinks the correct recovery action is resume, continue, verify, refine, or HIL

Before trusting that bundle, ORX should also report whether the project topology has drifted:
- missing repo root on disk
- runtime-home mismatch
- missing owner chat/thread bindings
- mirrored issue project mismatch
- continuity or active slice request project mismatch
- tmux session namespace mismatch

Recovery should degrade explicitly when those contracts drift instead of silently resuming.

## Verification Philosophy

ORX should not treat work as truly correct just because an internal step completed or a test passed.

The correctness order is:
- use the cheapest real-surface debug loop that can actually exercise the behavior
- Playwright is one example for browser- or UI-reachable flows, not the only valid mechanism
- use that loop to confirm the observed behavior is actually correct
- add or update tests only when they provide non-redundant future confidence; do not do duplicate validation work just to satisfy process

If the real-surface debug loop cannot run, ORX should not silently mark the work correct. It should record the missing validation path as a blocker, caveat, or degraded-confidence result in Linear.

## Control Surface

- Telegram/telecodex: use the local ORX API for health, status, proposals, commands, and rough-idea intake.
- SSH/local shell: use the ORX CLI for inspection, recovery, and explicit takeover-aware local control.
- tmux: use tmux as the session residency and inspection substrate, not as an independent planner.
- Linear: use Linear as the durable work ledger and execution trail.

## Runtime Commands

The shell helpers live in `/Users/jian/Dev/.custom`:
- `orx`
- `ox`

Core commands:

```bash
orx --json init
orx --json doctor
orx --json daemon run --once
orx --json api serve
orx --json operator runners
orx --json operator validations --issue-key PRO-29 --runner-id runner-a
orx --json operator record-validation --issue-key PRO-29 --runner-id runner-a --surface cli --tool operator --result passed --confidence confirmed --summary "manual verification"
orx --json operator queue --runner-id runner-a
orx --json operator status --issue-key PRO-29 --runner-id runner-a
orx --json operator attach-target --runner-id runner-a
orx --json operator takeovers
orx --json operator takeover --issue-key PRO-29 --runner-id runner-a --operator-id jian --reason "Investigate locally"
orx --json operator control --kind steer --issue-key PRO-29 --runner-id runner-a --operator-id jian --payload-json '{"instruction":"switch direction"}'
orx --json operator return-control --issue-key PRO-29 --runner-id runner-a --operator-id jian --note "Done"
```

Telegram-side API routes:
- `GET /health`
- `GET /status?issue_key=...&runner_id=...`
- `GET /proposals?issue_key=...`
- `GET /validation?issue_key=...&runner_id=...`
- `GET /control/context?project_key=...`
- `GET /control/drift?project_key=...`
- `POST /commands`
- `POST /telegram/commands`
- `POST /telegram/ideas`
- `POST /slice-results`
- `POST /validation`

## Operator Model

1. Remote control normally enters through Telegram into the ORX API.
2. ORX normalizes commands into one queue and one continuity model.
3. Executors run in tmux-backed sessions and report structured slice results back into ORX.
   If a result carries a follow-up `next_slice`, ORX continues the same issue on the next daemon tick.
   If a result is `success`, `verified=true`, and `next_slice=null`, ORX finalizes that issue and the daemon can start the next runnable ticket for that project.
4. Local operators inspect runner state through the ORX CLI.
5. Local pause/resume/stop/steer mutations require an explicit ORX takeover.
6. Recovery and proposal state stay durable in sqlite so restart and handoff do not depend on chat history.
7. Real-surface validation evidence can be recorded durably in ORX so confidence and blockers do not live only in chat or ticket comments.

## Verification

Primary verification commands:

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
tmpdir=$(mktemp -d)
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" init
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" doctor
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" daemon run --once
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" api serve --max-requests 0
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" operator runners
python3 /Users/jian/Dev/workspace/orx/bin/orx --json --home "$tmpdir" operator takeovers
```

Live-host cutover/readiness flow:

```bash
orx --json doctor
export ORX_LINEAR_API_KEY=...
orx --json daemon run --once
orx --json operator daemon
```

Expected cutover gating:
- `doctor.ok` should be `true` before claiming the host is ready for live daemon-driven Linear materialization.
- If `doctor` reports the Linear key as missing, treat `PRO-37` as blocked instead of expecting the daemon to create Linear tickets yet.

## Cutover Checklist

1. Preflight the host.

```bash
orx --json doctor
```

Required outcome:
- runtime is bootstrapped
- tmux is available
- the Linear materialization key is configured

Stop here if `doctor.ok` is `false`.
Record the blocker instead of pretending cutover is complete.

Example blocked-state evidence:

```bash
orx --json operator record-validation \
  --issue-key PRO-37 \
  --runner-id runner-a \
  --surface host \
  --tool doctor \
  --result blocked \
  --confidence degraded \
  --summary "cutover blocked at preflight" \
  --details-json '{"step":"doctor"}' \
  --blocker "ORX_LINEAR_API_KEY is not configured"
```

2. Run a live daemon tick once the host is actually ready.

```bash
orx --json daemon run --once
orx --json operator daemon
```

Required outcome:
- the daemon tick reflects the live state you expect
- daemon visibility is persisted after the command exits

3. Verify the real surface that changed.

Use the cheapest real debug loop that can actually exercise the behavior:
- localhost API request
- operator CLI inspection
- tmux/operator interaction
- MCP browser validation when the surface is browser-reachable

4. Record the observed result in ORX.

Example success evidence:

```bash
orx --json operator record-validation \
  --issue-key PRO-37 \
  --runner-id runner-a \
  --surface host \
  --tool operator \
  --result passed \
  --confidence confirmed \
  --summary "live daemon materialization verified" \
  --details-json '{"step":"post-daemon verification"}'
```

Inspect the durable record:

```bash
orx --json operator validations --issue-key PRO-37 --runner-id runner-a
```

5. Only after the real-surface check, update any remaining non-redundant regression coverage or docs.

Readiness is not cutover.
`doctor` proving the host is ready does not mean the live daemon-driven Linear flow has already been verified.

End-to-end regression lives in `tests/test_e2e.py` and covers:
- run dispatch and structured result handling
- steer, pause, and resume queueing
- durable proposal intake
- recovery candidate listing
- restart continuity reload
- explicit takeover and return-control

Manual temp-runtime proof also now covers:
- dispatching a project issue over the HTTP API
- reporting a structured slice result over `/slice-results`
- daemon continuation of the same issue when `next_slice` is present
- daemon transition to the next runnable project issue after finalization

## Cutover Risks

- The live telecodex profiles on this Mac mini still need explicit `ORX_*` profile bindings and a real Telegram chat/thread proof before Telegram -> telecodex -> ORX can be claimed as production-verified.
- tmux pane capture and attach behavior are covered by service tests using the tmux transport abstraction; production behavior still depends on tmux being installed and reachable on the host.
- The local API currently has no authentication layer and is intended only for localhost use.
- ORX can now finalize completed issues into the local mirror when a structured slice result is submitted, but a live Telegram/bot proof of that path is still pending.
