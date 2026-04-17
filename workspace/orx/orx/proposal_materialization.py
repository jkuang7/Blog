"""Materialize durable ORX proposals into Linear leaf tickets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from .linear_client import LinearClientError, LinearCreatedIssue, LinearGraphQLClient
from .metadata import METADATA_END, METADATA_START
from .mirror import LinearMirrorRepository, MirroredIssueRecord
from .proposals import ProposalRecord, ProposalService
from .storage import Storage


@dataclass(frozen=True)
class ProposalMaterializationResult:
    proposal: ProposalRecord
    created_issue: LinearCreatedIssue
    idempotent: bool


@dataclass(frozen=True)
class ProposalMaterializationBatch:
    status: str
    eligible: int
    materialized: int
    idempotent: int
    failed: int
    disabled_reason: str | None
    errors: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "materialized": self.materialized,
            "idempotent": self.idempotent,
            "failed": self.failed,
            "disabled_reason": self.disabled_reason,
            "errors": [dict(item) for item in self.errors],
        }


class ProposalMaterializationService:
    def __init__(
        self,
        storage: Storage,
        *,
        proposals: ProposalService | None = None,
        mirror: LinearMirrorRepository | None = None,
        client: LinearGraphQLClient | None = None,
    ) -> None:
        self.storage = storage
        self.proposals = proposals or ProposalService(storage)
        self.mirror = mirror or LinearMirrorRepository(storage)
        self.client_error: LinearClientError | None = None
        if client is not None:
            self.client = client
        else:
            try:
                self.client = LinearGraphQLClient.from_env()
            except LinearClientError as error:
                self.client = None
                self.client_error = error

    def materialize(
        self,
        *,
        proposal_id: int | None = None,
        proposal_key: str | None = None,
    ) -> ProposalMaterializationResult:
        proposal = self.proposals.get_proposal(proposal_id=proposal_id, proposal_key=proposal_key)
        if proposal is None:
            raise ValueError("Unknown proposal.")
        if proposal.workflow_mode != "leaf-ticket":
            raise ValueError(
                f"Proposal {proposal.proposal_key} is {proposal.workflow_mode!r} and cannot be materialized into a leaf ticket."
            )
        client = self._require_client()

        existing = _existing_created_issue(proposal)
        if proposal.status == "materialized" and existing is not None:
            return ProposalMaterializationResult(
                proposal=proposal,
                created_issue=existing,
                idempotent=True,
            )

        source_issue = self.mirror.get_issue(identifier=proposal.issue_key)
        if source_issue is None:
            raise ValueError(f"Source issue {proposal.issue_key} is missing from the Linear mirror.")

        phase_issue = self._resolve_phase_issue(proposal, source_issue)
        description = _build_issue_description(proposal, phase_issue=phase_issue)
        created_issue = client.create_issue(
            team_id=source_issue.team_id,
            title=proposal.title,
            description=description,
            parent_id=phase_issue.linear_id if phase_issue is not None else None,
            project_id=source_issue.project_id,
        )
        updated = self.proposals.update_proposal(
            proposal_key=proposal.proposal_key,
            status="materialized",
            context_patch={
                "materialized_at": _utc_now(),
                "materialized_linear_issue": {
                    "linear_id": created_issue.linear_id,
                    "identifier": created_issue.identifier,
                    "title": created_issue.title,
                    "url": created_issue.url,
                },
            },
        )
        return ProposalMaterializationResult(
            proposal=updated,
            created_issue=created_issue,
            idempotent=False,
        )

    def materialize_open_proposals(self) -> ProposalMaterializationBatch:
        proposals = [
            proposal
            for proposal in self.proposals.list_proposals(status="open")
            if proposal.workflow_mode == "leaf-ticket"
        ]
        if not proposals:
            return ProposalMaterializationBatch(
                status="idle",
                eligible=0,
                materialized=0,
                idempotent=0,
                failed=0,
                disabled_reason=None,
                errors=(),
            )
        if self.client is None:
            return ProposalMaterializationBatch(
                status="disabled",
                eligible=len(proposals),
                materialized=0,
                idempotent=0,
                failed=0,
                disabled_reason=self._client_error_message(),
                errors=(),
            )

        materialized = 0
        idempotent = 0
        errors: list[dict[str, str]] = []
        for proposal in proposals:
            try:
                result = self.materialize(proposal_id=proposal.proposal_id)
            except (LinearClientError, ValueError) as error:
                errors.append(
                    {
                        "proposal_key": proposal.proposal_key,
                        "message": str(error),
                    }
                )
                continue
            if result.idempotent:
                idempotent += 1
            else:
                materialized += 1

        status = "ok" if not errors else "partial"
        return ProposalMaterializationBatch(
            status=status,
            eligible=len(proposals),
            materialized=materialized,
            idempotent=idempotent,
            failed=len(errors),
            disabled_reason=None,
            errors=tuple(errors),
        )

    def _resolve_phase_issue(
        self,
        proposal: ProposalRecord,
        source_issue: MirroredIssueRecord,
    ) -> MirroredIssueRecord | None:
        preferred_keys = [
            proposal.suggested_phase_issue_key,
            source_issue.parent_identifier,
        ]
        for issue_key in preferred_keys:
            if issue_key is None:
                continue
            record = self.mirror.get_issue(identifier=issue_key)
            if record is not None:
                return record
        return None

    def _require_client(self) -> LinearGraphQLClient:
        if self.client is None:
            raise LinearClientError(self._client_error_message())
        return self.client

    def _client_error_message(self) -> str:
        if self.client_error is not None:
            return str(self.client_error)
        return "Set ORX_LINEAR_API_KEY or LINEAR_API_KEY to materialize proposals into Linear."


def _build_issue_description(
    proposal: ProposalRecord,
    *,
    phase_issue: MirroredIssueRecord | None,
) -> str:
    metadata = {
        "selection_lane": "linear",
        "created_from_proposal": True,
        "proposal_key": proposal.proposal_key,
        "proposal_kind": proposal.proposal_kind,
        "decomposition_class": proposal.decomposition_class,
        "workflow_mode": proposal.workflow_mode,
        "source_issue_key": proposal.issue_key,
        "suggested_parent_issue_key": proposal.suggested_parent_issue_key,
        "suggested_phase_issue_key": phase_issue.identifier if phase_issue is not None else proposal.suggested_phase_issue_key,
    }
    context = proposal.context
    lines = [
        f"Generated by ORX from `{proposal.issue_key}`.",
        "",
        "Routing",
        "",
        f"* Proposal kind: `{proposal.proposal_kind}`",
        f"* Decomposition class: `{proposal.decomposition_class}`",
        f"* Workflow mode: `{proposal.workflow_mode}`",
    ]
    if phase_issue is not None:
        lines.append(f"* Parent phase: `{phase_issue.identifier}`")
    if proposal.suggested_parent_issue_key is not None:
        lines.append(f"* Source leaf: `{proposal.suggested_parent_issue_key}`")

    lines.extend(
        [
            "",
            "Rationale",
            "",
            proposal.rationale,
        ]
    )

    next_slice = context.get("next_slice")
    if isinstance(next_slice, str) and next_slice.strip():
        lines.extend(
            [
                "",
                "Next Slice",
                "",
                f"* {next_slice.strip()}",
            ]
        )

    blockers = context.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.extend(
            [
                "",
                "Blockers",
                "",
                *[f"* {item}" for item in blockers if isinstance(item, str) and item.strip()],
            ]
        )

    lines.extend(
        [
            "",
            METADATA_START,
            json.dumps(metadata, sort_keys=True),
            METADATA_END,
        ]
    )
    return "\n".join(lines)


def _existing_created_issue(proposal: ProposalRecord) -> LinearCreatedIssue | None:
    if proposal.materialized_issue_id is None:
        return None
    materialized_issue = proposal.context.get("materialized_linear_issue")
    title = materialized_issue.get("title") if isinstance(materialized_issue, dict) else None
    return LinearCreatedIssue(
        linear_id=proposal.materialized_issue_id,
        identifier=proposal.materialized_issue_identifier,
        title=title if isinstance(title, str) and title else proposal.title,
        url=proposal.materialized_issue_url,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
