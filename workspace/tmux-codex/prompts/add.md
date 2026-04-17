# /add - Queue local follow-up work for a runner-managed Linear issue

Use this command to queue extra runner work quickly for the current conversation scope.

This is not the canonical issue-creation path anymore.
Canonical `/add` intake is Telegram -> telecodex -> ORX -> Linear.
Use this local prompt only to queue follow-up runner work against the current ORX/Linear-backed worktree when you are already inside an active runner-managed repo.

Intent:
- fast backlog intake while a runner is already active or while you are working in a runner-managed repo
- minimal friction: infer target project/root when possible
- do not interrupt the current active cycle unless the human explicitly asks to preempt

## Fast Exit

If the user is not asking to add runner backlog, stop and say this prompt only queues work for a runner-managed project.

## Task Text

- Resolve the raw task request from the slash-command argument text after `/add`.
- If the request is empty, ask the human for the task in one sentence.
- Keep the original intent compact; do not inflate it into a long plan.
- If the request is short or underspecified, combine it with the active conversation context before queueing it.

## Scope Resolution (Do This First)

Infer the target runner root in this order:

- If the human gives a repo path ending in `.memory`, normalize it back to the repo root before resolving runner scope.
- Example path normalization: `/Users/jian/Dev/Repos/time-track/.memory` should resolve to `/Users/jian/Dev/Repos/time-track`.

1. If the current working directory or any ancestor contains `.memory/runner/runtime/RUNNER_STATE.json`, use that ancestor as `<target_root>`.
2. Otherwise, if the current working directory is inside a git repo, use the repo root basename as `<project>` and resolve it via:
   - `python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --task queue --project <project> --runner-id main`
   - if that works, use the resolved project context for `<project>`
3. Otherwise, if exactly one runner under `$DEV/Repos/*/.memory/runner/runtime/RUNNER_STATE.json` is currently enabled or has a live heartbeat, use that project root.
4. If more than one plausible runner root exists, ask the human which project to queue against.
5. If no runner state exists for the inferred project, tell the human to run `/run_setup` first for that project and stop.

Rules:
- Prefer the current conversation/worktree root over canonical repo fallback.
- Do not create or refresh runner setup from `/add`.
- Do not switch projects silently if scope is ambiguous.
- Treat the current conversation as the default repo signal when scope is otherwise ambiguous.

## Existing Task Triage

- Read `.memory/PRD.md` if it exists for the older objective wording.
- Read `.memory/lessons.md` if it exists for durable project-specific constraints.
- Use `RUNNER_TASK_INTAKE.json` only as intake/audit context; do not treat it as the source of truth over `TASKS.json`.

## Read Minimal Context

After resolving `<target_root>`:

1. Read `<target_root>/.memory/runner/runtime/RUNNER_STATE.json`
2. Read `<target_root>/.memory/runner/TASKS.json`
3. Optionally read `<target_root>/.memory/runner/RUNNER_EXEC_CONTEXT.json` if you need the current phase or next-task wording.

Use that context only to improve queueing:
- identify the current active/next task
- detect explicit task-id references in the user request
- infer obvious dependency wording like “after TT-003” or “after the current task”

## Queueing Rules

Map the request to:

- `--title "<task title>"` (required)
- optional `--priority p0|p1|p2|p3`
- optional `--depends-on TT-...`
- optional `--acceptance "..."`
- optional `--validation "..."`
- optional `--allow-preempt` only if the human explicitly asks to interrupt or do it next

Defaults:
- use `p1` unless the user clearly signals urgency/blocker/regression (`p0`) or low-priority follow-up (`p2`/`p3`)
- if the request references a known task id, use it in `--depends-on`
- otherwise rely on `runctl --task add` default anchoring so the new task does not preempt the in-flight cycle
- only synthesize acceptance/validation lines when they are obvious from the request; otherwise leave them out rather than guessing

## Command

Queue the task with:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --task add --project-root <target_root> --runner-id main --title "<title>" ...
```

If the request is really asking to tighten or correct an existing queued task instead of adding a new one, update it with:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --task set --project-root <target_root> --runner-id main --task-id TT-... ...
```

Then show the queued intake with:

```bash
python3 /Users/jian/Dev/workspace/tmux-codex/bin/runctl --task queue --project-root <target_root> --runner-id main
```

## Output

Keep the response short:
- resolved project/root
- queued task id
- whether it will wait behind the current cycle or preempt
- any explicit dependency inferred

If scope was ambiguous and you had to ask, ask only one concise question.
