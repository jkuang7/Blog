# Canonical Issue Model

Selectors and gates operate only on the canonical model, never on raw issue prose.

## Fields

- `issue_key`
- `repo`
- `linear_id`
- `identifier`
- `project_id`
- `project_name`
- `team_id`
- `team_name`
- `title`
- `body_digest`
- `status`
- `priority`
- `type`
- `parent_issue_key`
- `child_issue_keys`
- `depends_on`
- `blocked_by`
- `complexity`
- `routing`
- `acceptance_criteria`
- `validation_plan`
- `scope_class`
- `hil_required`
- `dedupe_keys`
- `last_thread_digest`
- `last_mutation_hash`
- `updated_at`

## Source precedence

Canonical data is derived in this order:

1. structured machine metadata block
2. supported Linear issue/project fields
3. deterministic parser output from issue content
4. local defaults

## Write path

Relationship metadata must have a single durable machine write path.

Phase 1 default:

- store machine metadata in a dedicated structured block in the issue body
- use comments for human discussion and audit notes, not as the authoritative relationship store

This prevents parent/child and dependency truth from drifting across prose comments.

## Executability gate

Executable issues must satisfy all of:

- not an umbrella, tracker, or coordination issue
- complexity not gated by policy
- dependencies resolved
- routing known
- acceptance criteria known
- validation plan known
- no unresolved HIL gate
