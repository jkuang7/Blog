"""SSH/local operator inspection surface for ORX."""

from __future__ import annotations

from typing import Any

from .api import OrxApiService
from .continuity import ContinuityService
from .executor import ExecutorStore, TmuxTransport
from .linear_client import LinearGraphQLClient, LinearIssue
from .mirror import LinearMirrorRepository
from .proposal_materialization import ProposalMaterializationService
from .proposals import ProposalService
from .recovery import RecoveryService
from .repository import OrxRepository
from .storage import Storage
from .takeover import TakeoverService
from .tmux_client import TmuxClient
from .validation import ValidationLedgerService


class OperatorService:
    def __init__(
        self,
        *,
        storage: Storage,
        repository: OrxRepository,
        continuity: ContinuityService | None = None,
        proposals: ProposalService | None = None,
        materializer: ProposalMaterializationService | None = None,
        linear_client: LinearGraphQLClient | None = None,
        recovery: RecoveryService | None = None,
        transport: TmuxTransport | None = None,
    ) -> None:
        self.storage = storage
        self.repository = repository
        self.continuity = continuity or ContinuityService(storage)
        self.proposals = proposals or ProposalService(storage, continuity=self.continuity)
        self.materializer = materializer
        self.linear_client = linear_client
        self.recovery = recovery or RecoveryService(
            storage,
            continuity=self.continuity,
            proposals=self.proposals,
        )
        self.validation = ValidationLedgerService(storage)
        self.transport = transport or TmuxClient()
        self.store = ExecutorStore(storage)
        self.takeovers = TakeoverService(storage, repository)
        self.api = OrxApiService(
            storage=storage,
            repository=repository,
            continuity=self.continuity,
            proposals=self.proposals,
            materializer=self.materializer,
        )

    def runners_payload(self) -> dict[str, Any]:
        runners = []
        for record in self.repository.list_runners():
            session = self.store.get_session(record.runner_id)
            runners.append(
                {
                    "runner": {
                        "runner_id": record.runner_id,
                        "transport": record.transport,
                        "display_name": record.display_name,
                        "state": record.state,
                        "metadata": record.metadata,
                    },
                    "session": None
                    if session is None
                    else {
                        "issue_key": session.issue_key,
                        "session_name": session.session_name,
                        "pane_target": session.pane_target,
                        "state": session.state,
                        "heartbeat_at": session.heartbeat_at,
                        "last_result_at": session.last_result_at,
                    },
                }
            )
        return {"runners": runners}

    def queue_payload(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "queue": [
                {
                    "command_id": record.command_id,
                    "issue_key": record.issue_key,
                    "runner_id": record.runner_id,
                    "command_kind": record.command_kind,
                    "payload": record.payload,
                    "status": record.status,
                    "priority": record.priority,
                }
                for record in self.repository.list_commands(
                    status="pending",
                    issue_key=issue_key,
                    runner_id=runner_id,
                )
            ]
        }

    def status_payload(self, *, issue_key: str, runner_id: str) -> dict[str, Any]:
        status = self.api.status_payload(issue_key=issue_key, runner_id=runner_id)
        status["proposals"] = self.api.proposals_payload(issue_key=issue_key)["proposals"]
        return status

    def daemon_payload(self) -> dict[str, Any]:
        return self.api.daemon_payload()

    def validations_payload(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self.api.validation_payload(
            issue_key=issue_key,
            runner_id=runner_id,
            limit=limit,
        )

    def record_validation_payload(
        self,
        *,
        issue_key: str,
        runner_id: str,
        surface: str,
        tool: str,
        result: str,
        confidence: str,
        summary: str,
        details: dict[str, Any] | None,
        blockers: list[str] | None,
    ) -> dict[str, Any]:
        return self.api.record_validation(
            {
                "issue_key": issue_key,
                "runner_id": runner_id,
                "surface": surface,
                "tool": tool,
                "result": result,
                "confidence": confidence,
                "summary": summary,
                "details": details or {},
                "blockers": blockers or [],
            }
        )

    def attach_target_payload(self, *, runner_id: str) -> dict[str, Any]:
        session = self.store.get_session(runner_id)
        if session is None:
            raise ValueError(f"No executor session for runner {runner_id}")
        return {
            "runner_id": runner_id,
            "attach_target": session.session_name,
            "pane_target": session.pane_target,
        }

    def pane_payload(self, *, runner_id: str, lines: int) -> dict[str, Any]:
        session = self.store.get_session(runner_id)
        if session is None:
            raise ValueError(f"No executor session for runner {runner_id}")
        return {
            "runner_id": runner_id,
            "session_name": session.session_name,
            "pane": self.transport.capture_pane(session.session_name, lines=lines),
        }

    def recovery_payload(self, *, stale_after_seconds: int) -> dict[str, Any]:
        return {
            "recovery": [
                {
                    "issue_key": state.issue_key,
                    "runner_id": state.runner_id,
                    "active_slice_id": state.active_slice_id,
                    "next_slice": state.next_slice,
                    "updated_at": state.updated_at,
                }
                for state in self.recovery.list_stale_recovery_candidates(
                    stale_after_seconds=stale_after_seconds
                )
            ]
        }

    def takeovers_payload(self) -> dict[str, Any]:
        return {
            "takeovers": [
                {
                    "takeover_id": record.takeover_id,
                    "issue_key": record.issue_key,
                    "runner_id": record.runner_id,
                    "operator_id": record.operator_id,
                    "rationale": record.rationale,
                    "status": record.status,
                    "acquired_at": record.acquired_at,
                }
                for record in self.takeovers.list_active()
            ]
        }

    def takeover_payload(
        self,
        *,
        issue_key: str,
        runner_id: str,
        operator_id: str,
        rationale: str,
    ) -> dict[str, Any]:
        record = self.takeovers.begin(
            issue_key=issue_key,
            runner_id=runner_id,
            operator_id=operator_id,
            rationale=rationale,
        )
        return {"takeover": _serialize_takeover(record)}

    def proposals_payload(
        self,
        *,
        issue_key: str | None = None,
        status: str | None = "open",
    ) -> dict[str, Any]:
        records = (
            self.proposals.list_open_proposals(issue_key=issue_key)
            if status in {None, "open"}
            else self.proposals.list_proposals(issue_key=issue_key, status=status)
        )
        return {"proposals": [_serialize_proposal(record) for record in records]}

    def materialize_proposal_payload(self, *, proposal_id: int) -> dict[str, Any]:
        materializer = self.materializer or ProposalMaterializationService(
            self.storage,
            proposals=self.proposals,
        )
        result = materializer.materialize(proposal_id=proposal_id)
        return {
            "proposal": _serialize_proposal(result.proposal),
            "created_issue": _serialize_created_issue(result.created_issue),
            "idempotent": result.idempotent,
        }

    def linear_issue_get_payload(self, *, issue_ref: str) -> dict[str, Any]:
        issue = self._linear_client().get_issue(issue_ref=issue_ref)
        return {
            "ok": issue is not None,
            "issue": None if issue is None else _serialize_linear_issue(issue),
        }

    def linear_issue_create_payload(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        parent_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        issue = self._linear_client().create_issue(
            team_id=team_id,
            title=title,
            description=description,
            parent_id=parent_id,
            project_id=project_id,
        )
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_update_payload(
        self,
        *,
        issue_ref: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
    ) -> dict[str, Any]:
        issue = self._linear_client().update_issue(
            issue_ref=issue_ref,
            title=title,
            description=description,
            state_id=state_id,
        )
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_archive_payload(
        self,
        *,
        issue_ref: str,
        trash: bool = False,
    ) -> dict[str, Any]:
        issue = self._linear_client().archive_issue(issue_ref=issue_ref, trash=trash)
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_delete_payload(self, *, issue_ref: str) -> dict[str, Any]:
        issue = self._linear_client().delete_issue(issue_ref=issue_ref)
        LinearMirrorRepository(self.storage).delete_issue(identifier=issue_ref)
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def _linear_client(self) -> LinearGraphQLClient:
        return self.linear_client or LinearGraphQLClient.from_env()

    def return_control_payload(
        self,
        *,
        issue_key: str,
        runner_id: str,
        operator_id: str,
        note: str | None,
    ) -> dict[str, Any]:
        record = self.takeovers.return_control(
            issue_key=issue_key,
            runner_id=runner_id,
            operator_id=operator_id,
            note=note,
        )
        return {"takeover": _serialize_takeover(record)}

    def control_payload(
        self,
        *,
        operator_id: str,
        command_kind: str,
        issue_key: str,
        runner_id: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record = self.takeovers.queue_control_command(
            operator_id=operator_id,
            command_kind=command_kind,
            issue_key=issue_key,
            runner_id=runner_id,
            payload=payload,
        )
        return {
            "command": {
                "command_id": record.command_id,
                "issue_key": record.issue_key,
                "runner_id": record.runner_id,
                "command_kind": record.command_kind,
                "payload": record.payload,
                "status": record.status,
                "priority": record.priority,
            }
        }


def _serialize_takeover(record: Any) -> dict[str, Any]:
    return {
        "takeover_id": record.takeover_id,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "operator_id": record.operator_id,
        "rationale": record.rationale,
        "status": record.status,
        "release_note": record.release_note,
        "acquired_at": record.acquired_at,
        "released_at": record.released_at,
    }


def _serialize_proposal(record: Any) -> dict[str, Any]:
    return {
        "proposal_id": record.proposal_id,
        "proposal_key": record.proposal_key,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "proposal_kind": record.proposal_kind,
        "decomposition_class": record.decomposition_class,
        "workflow_mode": record.workflow_mode,
        "suggested_parent_issue_key": record.suggested_parent_issue_key,
        "suggested_phase_issue_key": record.suggested_phase_issue_key,
        "target_issue_key": record.target_issue_key,
        "status": record.status,
        "title": record.title,
        "rationale": record.rationale,
        "context": record.context,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _serialize_created_issue(record: Any) -> dict[str, Any]:
    return {
        "linear_id": record.linear_id,
        "identifier": record.identifier,
        "title": record.title,
        "url": record.url,
    }


def _serialize_linear_issue(record: LinearIssue) -> dict[str, Any]:
    return {
        "linear_id": record.linear_id,
        "identifier": record.identifier,
        "title": record.title,
        "description": record.description,
        "url": record.url,
        "state_id": record.state_id,
        "state_name": record.state_name,
        "state_type": record.state_type,
        "parent_id": record.parent_id,
        "parent_identifier": record.parent_identifier,
        "project_id": record.project_id,
        "project_name": record.project_name,
    }
