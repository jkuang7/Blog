"""Local HTTP API contract for ORX control-plane access."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .commands import normalize_command
from .continuity import ContinuityService
from .dispatch import GlobalDispatchService
from .executor import TmuxTransport
from .intake import IntakeService
from .linear_client import LinearGraphQLClient, LinearIssue
from .mirror import LinearMirrorRepository
from .proposal_materialization import ProposalMaterializationService
from .proposals import ProposalService
from .registry import ProjectRegistry
from .repository import CommandRecord, LeaseRecord, OrxRepository, RunnerRecord
from .runtime_state import DaemonStateService, RuntimeStateRecord
from .storage import Storage
from .telegram_adapter import TelegramCommandAdapter
from .validation import ValidationLedgerService, ValidationRecord


class OrxApiService:
    def __init__(
        self,
        *,
        storage: Storage,
        repository: OrxRepository,
        continuity: ContinuityService | None = None,
        proposals: ProposalService | None = None,
        materializer: ProposalMaterializationService | None = None,
        linear_client: LinearGraphQLClient | None = None,
        dispatch_transport_factory: Callable[[], TmuxTransport] | None = None,
    ) -> None:
        self.storage = storage
        self.repository = repository
        self.continuity = continuity or ContinuityService(storage)
        self.proposals = proposals or ProposalService(storage, continuity=self.continuity)
        self.materializer = materializer
        self.linear_client = linear_client
        self.runtime_state = DaemonStateService(storage)
        self.validation = ValidationLedgerService(storage)
        self.registry = ProjectRegistry(storage)
        self.dispatch = GlobalDispatchService(
            storage=storage,
            registry=self.registry,
            linear_client=linear_client,
            transport_factory=dispatch_transport_factory,
        )
        self.intake = IntakeService(
            storage,
            registry=self.registry,
            mirror=self.dispatch.mirror,
            linear_client=linear_client,
        )
        self.telegram = TelegramCommandAdapter(
            repository=repository,
            continuity=self.continuity,
            proposals=self.proposals,
        )

    def health_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema_version": self.storage.current_version(),
            "daemon": _serialize_runtime_state(self.runtime_state.get_last_tick()),
        }

    def status_payload(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
    ) -> dict[str, Any]:
        continuity = None
        if issue_key is not None and runner_id is not None:
            record = self.continuity.get_state(issue_key, runner_id)
            continuity = _serialize_continuity(record) if record is not None else None

        return {
            "ok": True,
            "schema_version": self.storage.current_version(),
            "runners": [
                _serialize_runner(record)
                for record in self.repository.list_runners(runner_id=runner_id)
            ],
            "leases": [
                _serialize_lease(record)
                for record in self.repository.list_active_leases(issue_key=issue_key, runner_id=runner_id)
            ],
            "queue": [
                _serialize_command(record)
                for record in self.repository.list_commands(
                    status="pending",
                    issue_key=issue_key,
                    runner_id=runner_id,
                )
            ],
            "continuity": continuity,
            "daemon": _serialize_runtime_state(self.runtime_state.get_last_tick()),
            "validation": None
            if issue_key is None or runner_id is None
            else _serialize_validation(self.validation.latest(issue_key=issue_key, runner_id=runner_id)),
        }

    def proposals_payload(self, *, issue_key: str | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "proposals": [
                _serialize_proposal(record)
                for record in self.proposals.list_open_proposals(issue_key=issue_key)
            ],
        }

    def daemon_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "daemon": _serialize_runtime_state(self.runtime_state.get_last_tick()),
        }

    def validation_payload(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "validation": [
                _serialize_validation(record)
                for record in self.validation.list(
                    issue_key=issue_key,
                    runner_id=runner_id,
                    limit=limit,
                )
            ],
        }

    def submit_command(self, body: dict[str, Any]) -> dict[str, Any]:
        command = normalize_command(
            body["command_kind"],
            issue_key=body.get("issue_key"),
            runner_id=body.get("runner_id"),
            payload=body.get("payload"),
            replacement_key=body.get("replacement_key"),
        )
        record = self.repository.enqueue_normalized_command(command)
        return {
            "ok": True,
            "command": _serialize_command(record),
        }

    def submit_telegram_command(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.telegram.handle(body)

    def submit_telegram_idea(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.telegram.intake_idea(body)

    def materialize_proposal(self, body: dict[str, Any]) -> dict[str, Any]:
        proposal_id = body.get("proposal_id")
        if proposal_id is None:
            raise ValueError("proposal_id is required")
        materializer = self.materializer or ProposalMaterializationService(
            self.storage,
            proposals=self.proposals,
        )
        result = materializer.materialize(proposal_id=int(proposal_id))
        return {
            "ok": True,
            "idempotent": result.idempotent,
            "proposal": _serialize_proposal(result.proposal),
            "created_issue": _serialize_created_issue(result.created_issue),
        }

    def intake_submit_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self.intake.submit(
            request_text=_required_string(body, "request_text"),
            ingress_bot=_required_string(body, "ingress_bot"),
            ingress_chat_id=_optional_int(body.get("ingress_chat_id"), field_name="ingress_chat_id"),
            ingress_thread_id=_optional_int(body.get("ingress_thread_id"), field_name="ingress_thread_id"),
            explicit_project_key=_optional_string(body, "project_key"),
        )
        return {"ok": True, "intake": _serialize_intake(record)}

    def intake_payload(
        self,
        *,
        intake_id: int | None = None,
        intake_key: str | None = None,
    ) -> dict[str, Any]:
        record = self.intake.get_intake(intake_id=intake_id, intake_key=intake_key)
        return {
            "ok": record is not None,
            "intake": None if record is None else _serialize_intake(record),
        }

    def intake_approve_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.intake.approve(
            intake_id=_optional_int(body.get("intake_id"), field_name="intake_id"),
            intake_key=_optional_string(body, "intake_key"),
        )
        return {
            "ok": True,
            "intake": _serialize_intake(result.intake),
            "created_issues": [_serialize_linear_issue(issue) for issue in result.created_issues],
        }

    def intake_reject_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self.intake.reject(
            intake_id=_optional_int(body.get("intake_id"), field_name="intake_id"),
            intake_key=_optional_string(body, "intake_key"),
            note=_optional_string(body, "note"),
        )
        return {"ok": True, "intake": _serialize_intake(record)}

    def record_validation(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self.validation.record(
            issue_key=_required_string(body, "issue_key"),
            runner_id=_required_string(body, "runner_id"),
            surface=_required_string(body, "surface"),
            tool=_required_string(body, "tool"),
            result=_required_string(body, "result"),
            confidence=_required_string(body, "confidence"),
            summary=_required_string(body, "summary"),
            details=_optional_object(body.get("details"), field_name="details"),
            blockers=_optional_string_list(body.get("blockers"), field_name="blockers"),
        )
        return {
            "ok": True,
            "record": _serialize_validation(record),
        }

    def linear_issue_get_payload(self, *, issue_ref: str) -> dict[str, Any]:
        issue = self._linear_client().get_issue(issue_ref=issue_ref)
        return {
            "ok": issue is not None,
            "issue": None if issue is None else _serialize_linear_issue(issue),
        }

    def linear_issue_create_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        issue = self._linear_client().create_issue(
            team_id=_required_string(body, "team_id"),
            title=_required_string(body, "title"),
            description=_required_string(body, "description"),
            parent_id=_optional_string(body, "parent_id"),
            project_id=_optional_string(body, "project_id"),
        )
        self._mirror_linear_issue(issue, metadata=_issue_metadata_overrides(body))
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_update_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        issue = self._linear_client().update_issue(
            issue_ref=_required_string(body, "issue"),
            title=_optional_string(body, "title"),
            description=_optional_string(body, "description"),
            state_id=_optional_string(body, "state_id"),
        )
        self._mirror_linear_issue(issue, metadata=_issue_metadata_overrides(body))
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_archive_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        issue_ref = _required_string(body, "issue")
        issue = self._linear_client().archive_issue(
            issue_ref=issue_ref,
            trash=_optional_bool(body.get("trash"), field_name="trash"),
        )
        LinearMirrorRepository(self.storage).delete_issue(
            linear_id=issue.linear_id,
            identifier=issue_ref,
        )
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def linear_issue_delete_payload(self, *, issue_ref: str) -> dict[str, Any]:
        issue = self._linear_client().delete_issue(issue_ref=issue_ref)
        LinearMirrorRepository(self.storage).delete_issue(identifier=issue_ref)
        return {"ok": True, "issue": _serialize_linear_issue(issue)}

    def _mirror_linear_issue(
        self,
        issue: LinearIssue,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        mirror = LinearMirrorRepository(self.storage)
        existing = None if issue.identifier is None else mirror.get_issue(identifier=issue.identifier)
        merged_metadata = dict(existing.metadata) if existing is not None else {}
        if metadata:
            merged_metadata.update(metadata)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        mirror.upsert_issue(
            linear_id=issue.linear_id,
            identifier=issue.identifier,
            title=issue.title,
            description=issue.description,
            team_id=issue.team_id,
            team_name=issue.team_name,
            state_id=issue.state_id,
            state_name=issue.state_name,
            state_type=issue.state_type,
            priority=0 if existing is None else existing.priority,
            project_id=issue.project_id,
            project_name=issue.project_name,
            parent_linear_id=issue.parent_id,
            parent_identifier=issue.parent_identifier,
            assignee_id=None if existing is None else existing.assignee_id,
            assignee_name=None if existing is None else existing.assignee_name,
            labels=[] if existing is None else list(existing.labels),
            metadata=merged_metadata,
            source_updated_at=now,
            created_at=now if existing is None else existing.created_at,
            completed_at=None if existing is None else existing.completed_at,
            canceled_at=None if existing is None else existing.canceled_at,
        )

    def register_project_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self.dispatch.register_project(
            project_key=_required_string(body, "project_key"),
            display_name=_required_string(body, "display_name"),
            repo_root=_required_string(body, "repo_root"),
            owning_bot=_optional_string(body, "owning_bot"),
            owner_chat_id=_optional_int(body.get("owner_chat_id"), field_name="owner_chat_id"),
            owner_thread_id=_optional_int(body.get("owner_thread_id"), field_name="owner_thread_id"),
            metadata=_optional_object(body.get("metadata"), field_name="metadata"),
        )
        return {"ok": True, "project": _serialize_project(record)}

    def register_bot_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        record = self.dispatch.register_bot(
            bot_identity=_required_string(body, "bot_identity"),
            default_display_name=_required_string(body, "default_display_name"),
            telegram_chat_id=_optional_int(body.get("telegram_chat_id"), field_name="telegram_chat_id"),
            telegram_thread_id=_optional_int(body.get("telegram_thread_id"), field_name="telegram_thread_id"),
            metadata=_optional_object(body.get("metadata"), field_name="metadata"),
        )
        return {"ok": True, "bot": _serialize_bot(record)}

    def deregister_project_payload(self, *, project_key: str) -> dict[str, Any]:
        record = self.dispatch.deregister_project(project_key=project_key)
        return {
            "ok": record is not None,
            "project": None if record is None else _serialize_project(record),
        }

    def dispatch_run_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.dispatch.dispatch_run(
            ingress_bot=_required_string(body, "ingress_bot"),
            ingress_chat_id=_optional_int(body.get("ingress_chat_id"), field_name="ingress_chat_id"),
            ingress_thread_id=_optional_int(body.get("ingress_thread_id"), field_name="ingress_thread_id"),
            explicit_issue_key=_optional_string(body, "issue_key"),
            explicit_project_key=_optional_string(body, "project_key"),
        )
        return {"ok": True, "dispatch": _serialize_dispatch_result(result)}

    def release_feature_lane_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.release_feature_lane(
            project_key=_required_string(body, "project_key"),
            action=_required_string(body, "action"),
            note=_optional_string(body, "note"),
        )

    def recover_failed_start_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.recover_failed_start(
            project_key=_required_string(body, "project_key"),
        )

    def resume_reviewed_lane_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.resume_reviewed_lane(
            project_key=_required_string(body, "project_key"),
            next_slice=_optional_string(body, "next_slice"),
        )

    def runner_event_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.submit_runner_event(
            project_key=_required_string(body, "project_key"),
            event_kind=_required_string(body, "event_kind"),
            issue_key=_optional_string(body, "issue_key"),
            final_summary=_optional_string(body, "final_summary"),
            transcript_excerpt=_optional_string(body, "transcript_excerpt"),
            raw_status=_optional_string(body, "raw_status"),
            verification_ran=_optional_string_list(body.get("verification_ran"), field_name="verification_ran"),
            verification_failed=_optional_string_list(body.get("verification_failed"), field_name="verification_failed"),
            artifacts=_optional_string_list(body.get("artifacts"), field_name="artifacts"),
            reason=_optional_string(body, "reason"),
        )

    def dashboard_payload(self) -> dict[str, Any]:
        return self.dispatch.dashboard_payload()

    def control_status_payload(self, *, project_key: str) -> dict[str, Any]:
        return self.dispatch.control_status(project_key=project_key)

    def control_context_payload(self, *, project_key: str) -> dict[str, Any]:
        return {
            "ok": True,
            "context": self.dispatch.build_restart_context(project_key=project_key),
        }

    def control_drift_payload(self, *, project_key: str) -> dict[str, Any]:
        return {
            "ok": True,
            "drift": self.dispatch.build_project_drift(project_key=project_key),
        }

    def control_queue_payload(self, *, project_key: str) -> dict[str, Any]:
        return self.dispatch.control_queue_payload_for_project(project_key=project_key)

    def bot_status_payload(self, *, bot_identity: str) -> dict[str, Any]:
        return self.dispatch.bot_status(bot_identity=bot_identity)

    def bot_queue_payload(self, *, bot_identity: str) -> dict[str, Any]:
        return self.dispatch.bot_queue(bot_identity=bot_identity)

    def bot_pause_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.bot_queue_command(
            bot_identity=_required_string(body, "bot_identity"),
            command_kind="pause",
            payload=_optional_object(body.get("payload"), field_name="payload"),
        )

    def bot_resume_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.bot_queue_command(
            bot_identity=_required_string(body, "bot_identity"),
            command_kind="resume",
            payload=_optional_object(body.get("payload"), field_name="payload"),
        )

    def bot_name_sync_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.sync_bot_name(
            bot_identity=_required_string(body, "bot_identity"),
            current_display_name=_optional_string(body, "current_display_name"),
            desired_display_name=_optional_string(body, "desired_display_name"),
            sync_state=_required_string(body, "sync_state"),
            retry_at=_optional_string(body, "retry_at"),
        )

    def control_pause_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.control_queue_command(
            project_key=_required_string(body, "project_key"),
            command_kind="pause",
            payload=_optional_object(body.get("payload"), field_name="payload"),
        )

    def control_resume_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.dispatch.control_queue_command(
            project_key=_required_string(body, "project_key"),
            command_kind="resume",
            payload=_optional_object(body.get("payload"), field_name="payload"),
        )

    def notifications_payload(
        self,
        *,
        target_bot: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self.dispatch.notifications_payload(
            target_bot=target_bot,
            limit=limit,
        )

    def notifications_ack_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_ids = body.get("notification_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(item, int) for item in raw_ids):
            raise ValueError("notification_ids must be a list of integers")
        return self.dispatch.acknowledge_notifications(notification_ids=list(raw_ids))

    def submit_slice_result_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.dispatch.submit_slice_result(
            project_key=_required_string(body, "project_key"),
            slice_id=_required_string(body, "slice_id"),
            payload=_required_dict(body, "payload"),
        )
        return {
            "ok": True,
            "result": {
                "project_key": result.project_key,
                "issue_key": result.issue_key,
                "status": result.status,
                "verified": result.verified,
                "next_slice": result.next_slice,
                "finalized": result.finalized,
                "linear_completed": result.linear_completed,
                "session_name": result.session_name,
                "pane_target": result.pane_target,
                "apply_status": result.apply_status,
                "stale_reason": result.stale_reason,
            },
        }

    def _linear_client(self) -> LinearGraphQLClient:
        return self.linear_client or LinearGraphQLClient.from_env()


class OrxApiServer(ThreadingHTTPServer):
    # Keep request worker threads non-daemon so `api serve --max-requests N`
    # does not exit the process before the last HTTP response is flushed.
    daemon_threads = False

    def __init__(self, server_address: tuple[str, int], api: OrxApiService) -> None:
        super().__init__(server_address, OrxApiHandler)
        self.api = api


class OrxApiHandler(BaseHTTPRequestHandler):
    server: OrxApiServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self._write_json(HTTPStatus.OK, self.server.api.health_payload())
                return
            if parsed.path == "/status":
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.status_payload(
                        issue_key=_first(query, "issue_key"),
                        runner_id=_first(query, "runner_id"),
                    ),
                )
                return
            if parsed.path == "/proposals":
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.proposals_payload(issue_key=_first(query, "issue_key")),
                )
                return
            if parsed.path == "/daemon":
                self._write_json(HTTPStatus.OK, self.server.api.daemon_payload())
                return
            if parsed.path == "/validation":
                limit = int(_first(query, "limit") or "20")
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.validation_payload(
                        issue_key=_first(query, "issue_key"),
                        runner_id=_first(query, "runner_id"),
                        limit=limit,
                    ),
                )
                return
            if parsed.path == "/linear/issues":
                issue_ref = _first(query, "issue")
                if issue_ref is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "issue is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.linear_issue_get_payload(issue_ref=issue_ref),
                )
                return
            if parsed.path == "/intake":
                intake_key = _first(query, "intake_key")
                raw_id = _first(query, "intake_id")
                if intake_key is None and raw_id is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "intake_key or intake_id is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.intake_payload(
                        intake_key=intake_key,
                        intake_id=None if raw_id is None else int(raw_id),
                    ),
                )
                return
            if parsed.path == "/dashboard":
                self._write_json(HTTPStatus.OK, self.server.api.dashboard_payload())
                return
            if parsed.path == "/control/status":
                project_key = _first(query, "project_key")
                if project_key is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "project_key is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.control_status_payload(project_key=project_key),
                )
                return
            if parsed.path == "/control/context":
                project_key = _first(query, "project_key")
                if project_key is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "project_key is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.control_context_payload(project_key=project_key),
                )
                return
            if parsed.path == "/control/drift":
                project_key = _first(query, "project_key")
                if project_key is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "project_key is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.control_drift_payload(project_key=project_key),
                )
                return
            if parsed.path == "/control/queue":
                project_key = _first(query, "project_key")
                if project_key is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "project_key is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.control_queue_payload(project_key=project_key),
                )
                return
            if parsed.path == "/bot/status":
                bot_identity = _first(query, "bot")
                if bot_identity is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "bot is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.bot_status_payload(bot_identity=bot_identity),
                )
                return
            if parsed.path == "/bot/queue":
                bot_identity = _first(query, "bot")
                if bot_identity is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "bot is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.bot_queue_payload(bot_identity=bot_identity),
                )
                return
            if parsed.path == "/notifications":
                target_bot = _first(query, "bot")
                if target_bot is None:
                    target_bot = _first(query, "owning_bot")
                if target_bot is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "bot is required"},
                    )
                    return
                limit = int(_first(query, "limit") or "20")
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.notifications_payload(
                        target_bot=target_bot,
                        limit=limit,
                    ),
                )
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})
        except Exception as error:  # pragma: no cover - defensive boundary
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/commands":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.submit_command(body))
                return
            if parsed.path == "/telegram/commands":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.submit_telegram_command(body))
                return
            if parsed.path == "/telegram/ideas":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.submit_telegram_idea(body))
                return
            if parsed.path == "/proposals/materialize":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.materialize_proposal(body))
                return
            if parsed.path == "/intake/submit":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.intake_submit_payload(body))
                return
            if parsed.path == "/intake/approve":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.intake_approve_payload(body))
                return
            if parsed.path == "/intake/reject":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.intake_reject_payload(body))
                return
            if parsed.path == "/validation":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.record_validation(body))
                return
            if parsed.path == "/linear/issues":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.linear_issue_create_payload(body))
                return
            if parsed.path == "/linear/issues/archive":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.linear_issue_archive_payload(body))
                return
            if parsed.path == "/registry/projects":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.register_project_payload(body))
                return
            if parsed.path == "/registry/bots":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.register_bot_payload(body))
                return
            if parsed.path == "/dispatch/run":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.dispatch_run_payload(body))
                return
            if parsed.path == "/dispatch/release":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.release_feature_lane_payload(body))
                return
            if parsed.path == "/dispatch/recover-failed-start":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.recover_failed_start_payload(body))
                return
            if parsed.path == "/dispatch/resume-reviewed":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.resume_reviewed_lane_payload(body))
                return
            if parsed.path == "/runner-events":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.runner_event_payload(body))
                return
            if parsed.path == "/slice-results":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.submit_slice_result_payload(body))
                return
            if parsed.path == "/control/pause":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.control_pause_payload(body))
                return
            if parsed.path == "/control/resume":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.control_resume_payload(body))
                return
            if parsed.path == "/bot/pause":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.bot_pause_payload(body))
                return
            if parsed.path == "/bot/resume":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.bot_resume_payload(body))
                return
            if parsed.path == "/bot/name-sync":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.bot_name_sync_payload(body))
                return
            if parsed.path == "/notifications/ack":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.notifications_ack_payload(body))
                return
            if parsed.path != "/commands":
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})
                return
        except (KeyError, TypeError, ValueError) as error:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
        except Exception as error:  # pragma: no cover - defensive boundary
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/linear/issues":
                body = self._read_json()
                self._write_json(HTTPStatus.OK, self.server.api.linear_issue_update_payload(body))
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})
        except (KeyError, TypeError, ValueError) as error:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
        except Exception as error:  # pragma: no cover - defensive boundary
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/registry/projects":
                project_key = _first(query, "project_key")
                if project_key is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "project_key is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.deregister_project_payload(project_key=project_key),
                )
                return
            if parsed.path == "/linear/issues":
                issue_ref = _first(query, "issue")
                if issue_ref is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "issue is required"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    self.server.api.linear_issue_delete_payload(issue_ref=issue_ref),
                )
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown route"})
        except (KeyError, TypeError, ValueError) as error:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(error)})
        except Exception as error:  # pragma: no cover - defensive boundary
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(error)})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length) if length else b"{}"
        body = json.loads(payload.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_api_server(
    api: OrxApiService,
    *,
    host: str,
    port: int,
    max_requests: int | None = None,
) -> tuple[str, int]:
    server = OrxApiServer((host, port), api)
    try:
        if max_requests is None:
            server.serve_forever()
        else:
            for _ in range(max_requests):
                server.handle_request()
    finally:
        address = server.server_address
        server.server_close()
    return str(address[0]), int(address[1])


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _serialize_runner(record: RunnerRecord) -> dict[str, Any]:
    return {
        "runner_id": record.runner_id,
        "transport": record.transport,
        "display_name": record.display_name,
        "state": record.state,
        "metadata": record.metadata,
        "last_seen_at": record.last_seen_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _serialize_lease(record: LeaseRecord) -> dict[str, Any]:
    return {
        "lease_id": record.lease_id,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "acquired_at": record.acquired_at,
        "released_at": record.released_at,
    }


def _serialize_runtime_state(record: RuntimeStateRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "updated_at": record.updated_at,
        **record.value,
    }


def _serialize_validation(record: ValidationRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "validation_id": record.validation_id,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "surface": record.surface,
        "tool": record.tool,
        "result": record.result,
        "confidence": record.confidence,
        "summary": record.summary,
        "details": record.details,
        "blockers": record.blockers,
        "created_at": record.created_at,
    }


def _serialize_command(record: CommandRecord) -> dict[str, Any]:
    return {
        "command_id": record.command_id,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "command_kind": record.command_kind,
        "payload": record.payload,
        "status": record.status,
        "created_at": record.created_at,
        "available_at": record.available_at,
        "consumed_at": record.consumed_at,
        "priority": record.priority,
    }


def _serialize_continuity(record: Any) -> dict[str, Any]:
    return {
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "objective": record.objective,
        "slice_goal": record.slice_goal,
        "acceptance": list(record.acceptance),
        "validation_plan": list(record.validation_plan),
        "blockers": list(record.blockers),
        "discovered_gaps": list(record.discovered_gaps),
        "verified_delta": record.verified_delta,
        "next_slice": record.next_slice,
        "failure_signatures": list(record.failure_signatures),
        "artifact_pointers": list(record.artifact_pointers),
        "idempotency_key": record.idempotency_key,
        "resume_context": record.resume_context,
        "active_slice_id": record.active_slice_id,
        "active_command_id": record.active_command_id,
        "last_result_status": record.last_result_status,
        "last_result_summary": record.last_result_summary,
        "last_result_at": record.last_result_at,
        "no_delta_count": record.no_delta_count,
        "consecutive_failure_count": record.consecutive_failure_count,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
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


def _serialize_intake(record: Any) -> dict[str, Any]:
    return {
        "intake_id": record.intake_id,
        "intake_key": record.intake_key,
        "ingress_bot": record.ingress_bot,
        "ingress_chat_id": record.ingress_chat_id,
        "ingress_thread_id": record.ingress_thread_id,
        "explicit_project_key": record.explicit_project_key,
        "default_project_key": record.default_project_key,
        "request_text": record.request_text,
        "status": record.status,
        "planning_stage": record.planning_stage,
        "planning_model": record.planning_model,
        "planning_reasoning_effort": record.planning_reasoning_effort,
        "decomposition_model": record.decomposition_model,
        "decomposition_reasoning_effort": record.decomposition_reasoning_effort,
        "execution_model": record.execution_model,
        "execution_reasoning_effort": record.execution_reasoning_effort,
        "confidence": record.confidence,
        "requires_hil": record.requires_hil,
        "plan": record.plan,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
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


def _serialize_project(record: Any) -> dict[str, Any]:
    execution_thread_id = None
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    raw_execution_thread = metadata.get("execution_thread_id")
    if isinstance(raw_execution_thread, int):
        execution_thread_id = raw_execution_thread
    elif isinstance(raw_execution_thread, str):
        try:
            execution_thread_id = int(raw_execution_thread)
        except ValueError:
            execution_thread_id = None
    if execution_thread_id is None:
        execution_thread_id = record.owner_thread_id
    return {
        "project_key": record.project_key,
        "display_name": record.display_name,
        "repo_root": record.repo_root,
        "runtime_home": record.runtime_home,
        "owning_bot": record.owning_bot,
        "assigned_bot": record.assigned_bot,
        "owner_chat_id": record.owner_chat_id,
        "owner_thread_id": record.owner_thread_id,
        "execution_thread_id": execution_thread_id,
        "feature_lane": metadata.get("feature_lane") if isinstance(metadata.get("feature_lane"), dict) else None,
        "reconciliation": metadata.get("reconciliation") if isinstance(metadata.get("reconciliation"), dict) else None,
        "metadata": record.metadata,
    }


def _serialize_bot(record: Any) -> dict[str, Any]:
    return {
        "bot_identity": record.bot_identity,
        "telegram_chat_id": record.telegram_chat_id,
        "telegram_thread_id": record.telegram_thread_id,
        "default_display_name": record.default_display_name,
        "current_display_name": record.current_display_name,
        "desired_display_name": record.desired_display_name,
        "name_sync_state": record.name_sync_state,
        "name_sync_retry_at": record.name_sync_retry_at,
        "availability_state": record.availability_state,
        "assigned_project_key": record.assigned_project_key,
        "assignment_id": record.assignment_id,
        "metadata": record.metadata,
        "last_heartbeat_at": record.last_heartbeat_at,
    }


def _serialize_dispatch_result(result: Any) -> dict[str, Any]:
    return {
        "decision": result.decision,
        "issue_key": result.issue_key,
        "issue_title": result.issue_title,
        "project_key": result.project_key,
        "feature_key": result.feature_key,
        "lane_state": result.lane_state,
        "release_required": result.release_required,
        "owning_bot": result.owning_bot,
        "assigned_bot": result.assigned_bot,
        "assignment_action": result.assignment_action,
        "handoff_required": result.handoff_required,
        "ingress_message": result.ingress_message,
        "owner_message": result.owner_message,
        "notification_id": result.notification_id,
        "runtime": None
        if result.runtime is None
        else {
            "project_key": result.runtime.project_key,
            "project_display_name": result.runtime.project_display_name,
            "runtime_home": result.runtime.runtime_home,
            "runner_id": result.runtime.runner_id,
            "issue_key": result.runtime.issue_key,
            "issue_title": result.runtime.issue_title,
            "action": result.runtime.action,
            "session_name": result.runtime.session_name,
            "pane_target": result.runtime.pane_target,
            "queue_depth": result.runtime.queue_depth,
            "daemon_tick": result.runtime.daemon_tick,
        },
    }


def _required_string(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _required_dict(body: dict[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} is required")
    return value


def _optional_string(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    stripped = value.strip()
    return stripped or None


def _optional_bool(value: Any, *, field_name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _issue_metadata_overrides(body: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(_optional_object(body.get("metadata"), field_name="metadata"))
    project_key = _optional_string(body, "project_key")
    if project_key:
        metadata["project_key"] = project_key
    return metadata


def _optional_string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return [item for item in value if isinstance(item, str) and item.strip()]
