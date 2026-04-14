---
name: kanban
description: Read and work GitHub Issues from the shared GitHub Project kanban board. Use when Codex needs to pick the next actionable issue, run safe pre-work sync, read the full issue thread, honor decomposed child tickets instead of stale umbrella trackers, synthesize a stronger execution plan, and then start working while leaving concise human ticket updates at important moments. Prefer the current repo when it is clear, but support multi-repo workspaces and treat issue presence in the GitHub Project as sufficient for discovery.
---

# Kanban

Use this skill for the GitHub Project based kanban workflow.

## Defaults

- Owner: `jkuang7`
- Project: `Solo Engineering`
- Project number: `5`
- Status flow: `Inbox` -> `Ready` -> `In Progress` -> `Review` -> `Done`

Override these only when the user explicitly says to.

## Workflow

1. Ground in the current repo:
   - Read the local `AGENTS.md` and the repo remote.
   - Derive the current GitHub repo from `origin`.
   - If the current directory is a workspace root that also contains child repos such as `Repos/*/.git`, treat it as a multi-repo workspace instead of assuming the root repo is the only candidate.
2. Before starting anything new, sync the local repo or workspace when safe:
   - If the current repo or workspace exposes a pre-work sync command such as `pull`, prefer running it before picking new work.
   - Skip automatic sync when the current worktree is dirty, detached, or otherwise unsafe to update in place.
   - Report when sync was skipped for safety instead of pretending the workspace is current.
3. Before starting anything new, check for an active issue:
   - Prefer an item in `In Progress` for the current repo if one already exists.
   - In a multi-repo workspace, if the current repo has no active item, check project items for the local repos under `Repos/` before concluding there is no active work.
   - Also check for open issues in the current repo or local workspace repos whose latest kanban workflow comment is a start/backtrack comment like `Picking this up now.` or `Adjusting course...`, because the project item can be missing or stale while the issue thread is current.
   - If an active issue is found in a child repo, immediately switch execution into that repo, read its nearest `AGENTS.md` and `.memory/lessons.md`, and continue the kanban workflow there.
   - Treat a found active issue as the selected task. Do not stop after reporting the helper output.
   - Do not start a second issue unless the current one is in `Review`, `Done`, or explicitly blocked.
4. If there is no active issue, pick the next task:
   - Use `scripts/github_project_issue_flow.py next --repo <owner/name>`.
   - If you are at a workspace root, also pass `--repos-root <path-to-Repos>` so the helper can fall back to local child repos.
   - Treat issue presence in the GitHub project as enough to surface it. Do not require a matching custom `Project` field or a pre-set `Ready` status.
   - Prefer `Status=Ready`, then `Inbox`, then unset status. Use `Priority` (`P0`, `P1`, `P2`) and issue number as tie-breakers.
   - Selection order is: current repo, then local child repos under `Repos/`, then the next actionable item anywhere on the shared project board.
   - Treat a matching custom `Project` field as a preference only when it is present; never make it a hard requirement for discovery.
   - If the selected issue belongs to a child repo, immediately switch execution into that repo, read its local instructions, and continue working there.
   - Treat a selected issue as the start of execution, not the end of discovery. Do not stop after printing the selected item unless the user asked only for triage.
   - Only report "nothing found" after checking the current repo, the local workspace, and the broader shared project board.
5. Read the full issue thread before acting:
   - Always inspect the issue body and the latest comments before starting work, replying, or moving status.
   - Treat issue comments as the source of truth for the latest direction, clarifications, screenshots, and review feedback.
   - If comments conflict with the original issue body, follow the newest explicit instruction and mention the change in your status comment.
6. Reject stale umbrella or tracker tickets as execution specs:
   - If the selected issue explicitly says it is an umbrella, tracker, deprecated spec, or points to child tickets as the source of truth, do not implement from it directly.
   - Prefer the linked child ticket that actually carries the actionable scope.
   - If the parent tracker is on the board but the child tickets are the source of truth, move back to child selection instead of forcing execution from the parent.
7. Convert the ticket into an execution plan before starting implementation:
   - After reading the issue and comments, restate the actual problem in concrete terms.
   - Produce an enhanced plan that is more specific than the ticket text: likely root cause or work areas, the intended code slices, validation steps, and any meaningful risk or dependency.
   - Respect dependency notes, wait states, and HIL gates from `/enhance`; do not start work that is intentionally blocked.
   - Keep the plan in working context or user-facing handoff as appropriate; do not dump a long checklist into the issue thread unless the user explicitly wants that.
   - Do not move the ticket to `In Progress` until this plan exists and you are ready to execute it.
8. Execute continuously until the feature is actually complete:
   - `kanban` should keep working after the first successful slice. Do not stop just because one subtask, one page, or one verification step passed.
   - After each slice, compare the current state against the ticket's acceptance criteria, validation steps, and latest issue comments.
   - If meaningful scope from the selected issue still remains and it is unblocked, continue directly into the next slice in the same turn by default.
   - Do not stop to ask the human whether to continue between normal implementation slices. Continuing is the default behavior.
   - Treat partial progress as intermediate state, not as a default stopping point.
   - Only stop execution when one of these is true:
     - the issue is actually complete and ready for `Review` or `Done`
     - there is a real blocker that prevents further safe progress
     - the user explicitly redirects or pauses the work
9. Treat kanban as a loop, not a single-issue pass:
   - After the current issue reaches `Review`, `Done`, or `Blocked`, reassess whether kanban work remains instead of ending the turn automatically.
   - If the current issue is blocked and you created or identified an unblocked follow-up child ticket, continue on that child immediately.
   - If the current issue is blocked by an external dependency and no unblocked child of that issue exists, return to board selection and pick the next actionable issue in the repo/workspace/project.
   - If the current issue is ready for review or done, return to board selection and continue with the next actionable issue unless the user explicitly wanted work on only that one issue.
   - The default kanban session should continue issue-by-issue until the board has no actionable work, a global blocker prevents further progress, or the user explicitly stops the run.
   - Do not treat a single blocked issue or a single completed issue as the end of the kanban session.
10. Fix small gaps immediately; split only when the remaining work deserves its own tracking:
   - If you discover a missing piece, regression, or adjacent gap that is required to satisfy the current ticket and it is still part of the same coherent feature, keep working and fix it.
   - Do not ask the user for permission to continue on obvious required follow-through inside the same ticket.
   - Create a child ticket only when the newly discovered work is meaningfully distinct, cross-repo, phase-separated, risky enough to warrant explicit tracking, or too large to hide inside the current issue.
   - When you create a child ticket, make the parent/child relationship explicit and continue on the highest-priority unblocked child instead of stopping after ticket creation.
   - Use the existing `create-issue` helper or `/enhance` workflow when a new ticket is needed; do not leave discovered required work untracked.
   - If remaining slices or gaps exist, the default action is: either continue implementing them now, or decompose them into enhanced child tickets and immediately continue on the next actionable child.
   - Do not stop at "there is more work" or "this should be another ticket." Ticket creation is a coordination step, not a completion step.
11. Re-plan after every meaningful change:
   - Once a slice lands and is verified, reassess what remains for the selected issue instead of handing off automatically.
   - Keep the next slice narrow, but keep the overall goal fixed on completing the selected feature.
   - If the original issue was too broad, decompose it into actionable child tickets and continue execution from the first real child ticket rather than treating the broad parent as "done enough".
12. Communicate on the ticket like a teammate, not a status bot:
   - Keep comments human, concise, and useful when someone reads the thread later.
   - Do not leave frequent progress comments by default.
   - Default cadence:
     - Start work: one short ownership comment when moving to `In Progress`.
     - Handoff: one completion comment when moving to `Review` or `Done`.
     - Exception-only comments: blocked, major scope change, or correction after review feedback.
   - The comment should focus on what changed, what was verified, and any important risk or follow-up.
13. Use short human-style comments for status changes:
   - Start:
     - `Picking this up now. Plan is <one line>. I will update this thread when it is ready for review or if I hit a blocker.`
   - Review:
     - `Ready for review. I changed <one line>. Verified <one line>. Important note: <risk/follow-up or none>.`
   - Done:
     - `Done. I changed <one line>. Verified <one line>. Important note: <risk/follow-up or none>.`
   - Wrong direction / backtrack:
     - `Adjusting course based on feedback. I am moving this back to In Progress. Next I am going to <one line>.`
   - Blocked:
     - `Blocked on <one line>. I need <one line> before I can continue.`
14. Move status only after the matching comment is posted:
   - Start work: comment, then move to `In Progress`.
   - Handoff: comment, then move to `Review`.
   - Done: comment, then move to `Done`.
   - Wrong direction: comment, then move from `Review` back to `In Progress`.
15. Treat screenshots and attached images in the issue body or comments as task context.
16. Keep comments short and structured. Do not narrate every command or paste a changelog into the thread.
17. When design or frontend review needs a live local preview:
   - Prefer reusing an already-running local app or preview server before starting a new one.
   - If you need to serve a standalone artifact such as `index.html`, start a simple local server bound to `0.0.0.0`, not `127.0.0.1`, so other devices can reach it.
   - If Tailscale is available, prefer the machine's Tailscale DNS name over the raw `100.x.x.x` IP when handing the review link to the user.
   - Keep the preview URL stable across iterations when possible and tell the user they can refresh the same URL after changes land.
18. For frontend or design tasks, default to a phone-reviewable handoff:
   - When the feature is ready for user review, make a reasonable effort to expose a live preview URL the user can open from their phone.
   - Prefer a Tailscale URL when available, because it avoids localhost-only dead ends and is easier to share than a raw IP.
   - Include the live preview URL in the user handoff, alongside any normal review status update, unless there is no safe or practical way to run the feature locally.
   - If the preview is a temporary standalone artifact, say that clearly so the user understands it is not yet integrated into the app.

## Execution Rule

- `kanban` is an execution skill, not a reporting skill. After it finds an active or next issue, it must continue through issue read, planning, implementation, verification, and handoff unless blocked or the user explicitly asked only to inspect the board.
- Helper commands such as `active`, `next`, and `issue-item` are discovery steps. Their output should be used to drive execution, not returned as the final result by default.
- `kanban` should assume the user wants end-to-end completion of the selected feature, not just the first code slice. Keep going until the issue is actually done, blocked, or deliberately re-scoped.
- If the selected issue turns out to be too broad for honest completion in one uninterrupted pass, create or update the needed child tickets, move execution onto the first actionable child, and continue working instead of stopping at the decomposition step.
- `kanban` must not wait for repeated human "continue" messages during normal progress. Human confirmation is only needed for true blockers, risky scope changes, or explicit product decisions that cannot be inferred safely.
- If additional slices or hidden gaps are discovered mid-flight, `kanban` must absorb them into the current execution flow: finish them directly when feasible, or create enhanced follow-up tickets and continue working the next unblocked one without waiting for the human to restart the process.
- `kanban` must also continue across issues. Once one issue is handed off, blocked, or completed, the default behavior is to re-enter selection and keep pulling the next actionable work item until no actionable item remains.
- A blocked issue should narrow the stop condition to that issue, not to the whole kanban session, unless the blocker is global enough to prevent any other safe work from proceeding.

## Tools

- Use `scripts/github_project_issue_flow.py` for project item lookup and status changes.
- Use the GitHub MCP tools or `gh issue comment` for issue reads and comments.
- Use `gh issue view --comments` or the GitHub MCP tools to inspect the issue body, comments, and attached images.
- Use local preview servers plus Tailscale DNS when the user wants to review local work from another device.

## Common Calls

Run pre-work sync when the repo supports it:

```bash
npm run pull
```

Find the next task for the current repo:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py next --repo jkuang7/Banksy
```

Find the next task from a workspace root with child repos:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py next --repo jkuang7/Blog --repos-root /Users/jian/Dev/Repos
```

Read the full issue thread before acting:

```bash
gh issue view 123 --repo jkuang7/Banksy --comments
```

Check for an already-started issue before selecting something new:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py active --repo jkuang7/Banksy
```

Find the project item for a specific issue:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py issue-item --issue-url https://github.com/jkuang7/Banksy/issues/123
```

Move an issue after commenting:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py set-status --issue-url https://github.com/jkuang7/Banksy/issues/123 --status "In Progress"
```

List active work:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py list --status "In Progress"
```
