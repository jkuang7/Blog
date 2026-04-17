# Proposal Contracts

AI may only emit typed proposals.

## Proposal types

- `split_issue`
- `refine_issue`
- `create_followup`
- `update_issue_metadata`

`execution_summary` is not a proposal. It belongs to the executor slice result contract.

## Shared proposal fields

Every proposal must include:

- `proposal_type`
- `source_issue_key`
- `reason`
- `confidence`
- `dedupe_keys`
- `routing`
- `acceptance_criteria`
- `validation_plan`
- `dependencies`
- `risk_flags`

## Split proposal fields

Split proposals must also include:

- `parent_issue_key`
- `children`
- `dependency_edges`
- `parent_resolution`

Each child must include:

- `title`
- `summary`
- `routing`
- `acceptance_criteria`
- `validation_plan`
- `dedupe_keys`

## Validator outcomes

Deterministic validators may return only:

- `apply`
- `reject`
- `require_hil`

## HIL triggers

- large decompositions
- cross-repo mutations
- destructive ticket rewrites
- ambiguous dependencies
- low-confidence proposals
- parent closure with unclear downstream impact
