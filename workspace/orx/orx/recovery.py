"""Anti-spin enforcement and stale-run recovery for ORX."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .continuity import ContinuityRecord, ContinuityService
from .proposals import ProposalService
from .storage import Storage


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    issue_key: str
    runner_id: str
    active_slice_id: str | None
    next_slice: str | None
    proposal_key: str | None = None


class RecoveryService:
    def __init__(
        self,
        storage: Storage,
        *,
        continuity: ContinuityService | None = None,
        proposals: ProposalService | None = None,
    ) -> None:
        self.continuity = continuity or ContinuityService(storage)
        self.proposals = proposals or ProposalService(storage, continuity=self.continuity)

    def assess(
        self,
        issue_key: str,
        runner_id: str,
        *,
        no_delta_limit: int = 2,
        failure_limit: int = 2,
    ) -> RecoveryDecision:
        state = self.continuity.get_state(issue_key, runner_id)
        if state is None:
            raise ValueError(
                f"No continuity state exists for issue {issue_key} and runner {runner_id}."
            )

        if state.active_slice_id is not None:
            return RecoveryDecision(
                action="resume",
                reason="Active slice is still in flight and should be replayed from continuity state.",
                issue_key=issue_key,
                runner_id=runner_id,
                active_slice_id=state.active_slice_id,
                next_slice=state.next_slice,
            )

        if state.consecutive_failure_count >= failure_limit:
            proposal = self.proposals.route(
                issue_key,
                runner_id,
                hil_reason="Repeated failure signatures require explicit human review.",
            )
            return RecoveryDecision(
                action="hil",
                reason="Repeated failures require human intervention before continuing.",
                issue_key=issue_key,
                runner_id=runner_id,
                active_slice_id=None,
                next_slice=state.next_slice,
                proposal_key=proposal.proposal_key,
            )

        if state.no_delta_count >= no_delta_limit:
            return RecoveryDecision(
                action="verify",
                reason="Repeated no-delta slices require explicit verification before continuing.",
                issue_key=issue_key,
                runner_id=runner_id,
                active_slice_id=None,
                next_slice=state.next_slice,
            )

        if state.last_result_status == "blocked":
            return RecoveryDecision(
                action="refine",
                reason="Blocked slice should be refined or routed as dependency work.",
                issue_key=issue_key,
                runner_id=runner_id,
                active_slice_id=None,
                next_slice=state.next_slice,
            )

        return RecoveryDecision(
            action="continue",
            reason="No anti-spin or recovery action required.",
            issue_key=issue_key,
            runner_id=runner_id,
            active_slice_id=None,
            next_slice=state.next_slice,
        )

    def list_stale_recovery_candidates(
        self,
        *,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> list[ContinuityRecord]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(seconds=stale_after_seconds)
        return [
            state
            for state in self.continuity.list_recovery_candidates()
            if _parse_timestamp(state.updated_at) <= cutoff
        ]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)
