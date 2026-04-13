---
name: enhance
description: Turn rough ideas, weak tickets, or oversized mixed-scope requests into execution-ready GitHub issues for the shared kanban workflow. Use when Codex needs to rewrite a vague ticket, split one request into multiple tickets, route work to the right repo, deprecate an old umbrella ticket in favor of child tickets, require explicit human approval, and then create or update the resulting issues in the same GitHub Project used by /kanban.
---

# Enhance

Use this skill to prepare tickets that feed directly into the `/kanban` workflow.

This skill is upstream of `kanban`, not separate from it. Reuse the same owner, GitHub Project, status values, and repo/project-field mapping unless the user explicitly overrides them.

Tool split:

- Use GitHub MCP for issue and thread reads, repo context, and comment-side operations when those are needed.
- Use the shared `kanban` helper plus `gh` for issue creation, issue editing, and GitHub Project field updates, because the GitHub MCP surface in this environment does not expose full issue creation or project mutation directly.

## Defaults

- Owner: `jkuang7`
- Project: `Solo Engineering`
- Project number: `5`
- Default new-ticket status: `Inbox`
- Type values: `Feature`, `Bug`, `Refactor` when the project exposes them
- Project field value: repo name without the owner prefix, for example `create-t3-jian`

## Workflow

1. Ground in the current repo or workspace first:
   - Read the local `AGENTS.md` and derive the current GitHub repo from `origin` when you are inside a repo.
   - If the current directory is a workspace root containing child repos such as `Repos/*/.git`, do not guess the target repo when the request is ambiguous. Ask only for the repo if that cannot be inferred safely.
   - Default the Project field to the repo name, matching `/kanban`, but do not force it when the GitHub Project does not expose a matching option.
2. Decide whether the work should stay as one ticket or split:
   - Keep one ticket only when the scope is cohesive, bounded, and can be executed as one unit.
   - Split when the request mixes repos, phases, dependencies, blocked follow-ups, or work that should not share one execution loop.
   - Group simple changes together instead of fragmenting them.
   - Prefer child tickets over one oversized parent when that makes `/kanban` execution clearer.
   - When a parent ticket in `Dev` mentions unrelated problems for different repos, treat repo routing as part of the split itself, not as a later cleanup step.
3. Convert each resulting work item into an enhanced ticket draft:
   - Produce a concise, specific title.
   - Expand the body into a usable issue with enough detail for future execution, not just a reminder.
   - Include these sections when they add signal:
     - Problem
     - Desired outcome
     - Scope or constraints
     - Acceptance criteria
     - Validation
     - Risks or open questions
   - Keep it practical. Do not write product-manager fluff.
   - Assign repo and project metadata per child ticket based on that child ticket's actual scope, not based on the parent umbrella issue's repo.
4. When splitting an existing umbrella or overloaded ticket:
   - Treat the new child tickets as the future source of truth.
   - Rewrite the original issue into a tracker when you can edit it.
   - Explicitly deprecate the original implementation detail so future agents do not execute from stale mixed-scope text.
   - If body editing is blocked, leave a clear superseding comment that points at the child tickets and says the old description is deprecated.
   - Do not leave both the old umbrella text and the new child tickets as competing specs.
5. Infer the issue classification:
   - Choose `Type` from `Feature`, `Bug`, or `Refactor` when the field exists.
   - Add a matching GitHub label when that label exists or when the repo convention is obvious.
   - Choose a reasonable `Priority` only when the user supplied urgency or the issue clearly implies one; otherwise leave it unset.
   - For complex or externally-scoped requests, gather current context before drafting when that materially improves the decomposition or acceptance criteria.
6. Use HIL before creating anything:
   - Show the user the enriched draft and the exact metadata you plan to apply: repo, status, Project field, Type, Priority, labels.
   - When splitting, show the proposed parent/child structure and which ticket becomes the source of truth.
   - Keep the approval loop lightweight. Ask only the highest-leverage clarification questions.
   - Ask for explicit approval or edits.
   - Do not create the issue, move project state, or post comments until the user approves the enriched draft.
7. After approval, create the issue or issues:
   - Write the approved body to a temporary file.
   - Prefer GitHub MCP for repo or issue context gathering before creation.
   - For a new issue, run:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py create-issue \
  --repo <owner/name> \
  --title "<title>" \
  --body-file <tmpfile> \
  --status "Inbox" \
  --project-field "<repo-name>" \
  [--priority "P1"] \
  [--type "Feature"] \
  [--label "enhancement"]
```

   - Prefer `Inbox` by default. Use `Ready` only when the user explicitly wants the ticket to be immediately actionable.
   - For an existing issue that should become a tracker, use `gh issue edit` or equivalent tooling to replace the stale body after the child tickets exist.
   - If project field mutation fails only because the board has no matching option for that repo, continue and report that the field was intentionally left unset.
8. After creation, hand back the issue URL or URLs, the applied project fields, and any tracker or deprecation update that was made.
9. Do not start implementation automatically. Ticket creation ends here; `/kanban` owns the execution loop.

## Draft Shape

Use this body shape unless the repo or request clearly needs something else:

```md
## Problem

<what is missing or broken>

## Desired Outcome

<what should be true when done>

## Scope

- <in-scope item>
- <constraint or dependency>

## Acceptance Criteria

- [ ] <observable completion condition>
- [ ] <observable completion condition>

## Validation

- <how to verify the work>

## Risks / Open Questions

- <only if useful>
```

## Repo Selection Rules

- If the current repo is clear, use it.
- If the user explicitly names a repo or app, use that.
- If you are at `/Users/jian/Dev` and the request could map to multiple repos under `Repos/`, ask which repo the ticket belongs to before creating it.
- If one source ticket mentions multiple repos, choose the repo separately for each child ticket.
- Do not keep all child tickets in the parent repo just because the original umbrella issue lived there.
- Set the GitHub Project field from the child ticket's repo when the board exposes a matching option; otherwise leave that field unset and report it.

## Split Heuristics

- Split when one request would force `/kanban` to make separate start or stop decisions for different phases.
- Split when one part is blocked on a dependency or HIL but another part is independently actionable.
- Split when multiple repos would otherwise be hidden inside one ticket.
- Split when one umbrella ticket in `Dev` is actually describing unrelated repo-specific problems that should land in different repos.
- Do not split a ticket that already has a clear bounded scope and usable acceptance criteria unless the user asks.

## Already Enhanced

- Treat a ticket as already enhanced when it already has clear scope, acceptance criteria, validation, and bounded execution intent.
- Do not rewrite a well-structured ticket just to restate it.
- If the ticket is enhanced but stale, update only the stale parts and say what changed.

## Notes

- Treat screenshots or pasted notes from the user as source material for the draft.
- Optimize for tickets that a future agent can execute without rediscovering the whole problem.
- Keep the approval loop lightweight: one enriched draft, one approval, then create.
- Do not claim GitHub MCP created the ticket when the actual create or project-mutation path used `gh` through the shared helper.
