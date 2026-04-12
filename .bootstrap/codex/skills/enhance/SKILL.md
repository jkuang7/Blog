---
name: enhance
description: Turn a rough idea into a richer GitHub issue for the shared kanban workflow. Use when the user wants to draft or create a new ticket from a short prompt, rough notes, or a half-formed request. This skill should expand the ticket, identify the right repo and project field, require explicit human approval of the enriched draft, and then create the issue in the same GitHub Project used by /kanban, using GitHub MCP where it fits and the shared gh-based kanban helper where MCP coverage is missing.
---

# Enhance

Use this skill to create new tickets that feed directly into the `/kanban` workflow.

This skill is upstream of `kanban`, not separate from it. Reuse the same owner, GitHub Project, status values, and repo/project-field mapping unless the user explicitly overrides them.

Tool split:

- Use GitHub MCP for issue/thread reads, repo context, and comment-side operations when those are needed.
- Use the shared `kanban` helper plus `gh` for issue creation and GitHub Project field updates, because the GitHub MCP surface in this environment does not expose issue creation or project item field mutation directly.

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
   - If the current directory is a workspace root containing child repos such as `Repos/*/.git`, do not guess the target repo when the user’s request is ambiguous. Ask only for the repo if that cannot be inferred safely.
   - Default the Project field to the repo name, matching `/kanban`.
2. Convert the rough idea into an enhanced ticket draft:
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
3. Infer the issue classification:
   - Choose `Type` from `Feature`, `Bug`, or `Refactor` when the field exists.
   - Add a matching GitHub label when that label exists or when the repo convention is obvious.
   - Choose a reasonable `Priority` only when the user supplied urgency or the issue clearly implies one; otherwise leave it unset.
4. Use HIL before creating anything:
   - Show the user the enriched draft and the exact metadata you plan to apply: repo, status, Project field, Type, Priority, labels.
   - Ask for explicit approval or edits.
   - Do not create the issue, move project state, or post comments until the user approves the enriched draft.
5. After approval, create the issue through the shared kanban helper:
   - Write the approved body to a temporary file.
   - Prefer GitHub MCP for any repo or issue context gathering before creation.
   - Run:

```bash
python3 ~/.codex/skills/kanban/scripts/github_project_issue_flow.py create-issue \
  --repo <owner/name> \
  --title "<title>" \
  --body-file <tmpfile> \
  --status "Inbox" \
  --project-field "<repo-name>" \
  [--priority "P1"] \
  [--type "Feature"] \
  [--label "feature"]
```

   - Prefer `Inbox` by default. Use `Ready` only when the user explicitly wants the ticket to be immediately actionable.
6. After creation, hand back the issue URL and the applied project fields.
7. Do not start implementation automatically. Ticket creation ends here; `/kanban` owns the execution loop.

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

## Notes

- Treat screenshots or pasted notes from the user as source material for the draft.
- Optimize for tickets that a future agent can execute without rediscovering the whole problem.
- Keep the approval loop lightweight: one enriched draft, one approval, then create.
- Do not claim GitHub MCP created the ticket when the actual create/project-mutation path used `gh` through the shared helper.
