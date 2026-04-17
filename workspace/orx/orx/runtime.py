"""Project-scoped ORX runtime control and executor handoff."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pathlib import Path

from .config import RuntimePaths
from .continuity import ContinuityService
from .executor import ExecutorService, ExecutorStore, TmuxTransport
from .executor import SliceApplyGate
from .mirror import MirroredIssueRecord
from .ownership import OwnershipService
from .registry import ProjectRegistration
from .repository import CommandRecord, OrxRepository
from .runtime_state import DaemonStateService
from .storage import Storage
from .validation import ValidationLedgerService


DEFAULT_RUNNER_ID = "main"


@dataclass(frozen=True)
class RuntimeDispatchResult:
    project_key: str
    project_display_name: str
    runtime_home: str
    runner_id: str
    issue_key: str
    issue_title: str
    action: str
    session_name: str | None
    pane_target: str | None
    queue_depth: int
    daemon_tick: str | None


@dataclass(frozen=True)
class RuntimeSliceResult:
    project_key: str
    runner_id: str
    issue_key: str
    status: str
    verified: bool
    next_slice: str | None
    finalized: bool
    queue_depth: int
    submitted_at: str
    session_name: str | None
    pane_target: str | None
    daemon_tick: str | None
    apply_status: str
    stale_reason: str | None


class ProjectRuntimeService:
    def __init__(
        self,
        *,
        registration: ProjectRegistration,
        transport: TmuxTransport | None = None,
    ) -> None:
        self.registration = registration
        runtime_home = Path(registration.runtime_home)
        self.paths = RuntimePaths(
            home=runtime_home,
            db_path=runtime_home / "orx.sqlite3",
            log_dir=runtime_home / "logs",
            run_dir=runtime_home / "run",
        )
        self.storage = Storage(self.paths)
        self.storage.bootstrap()
        self.repository = OrxRepository(self.storage)
        self.ownership = OwnershipService(self.repository)
        self.continuity = ContinuityService(self.storage)
        self.executor = ExecutorService(
            storage=self.storage,
            repository=self.repository,
            ownership=self.ownership,
            continuity=self.continuity,
            transport=transport,
            session_namespace=registration.project_key,
        )
        self.runtime_state = DaemonStateService(self.storage)
        self.store = ExecutorStore(self.storage)
        self.validation = ValidationLedgerService(self.storage)

    def ensure_runner(self) -> None:
        self.repository.upsert_runner(
            DEFAULT_RUNNER_ID,
            transport="tmux-codex",
            display_name=f"{self.registration.display_name} main",
            state="ready",
            metadata={
                "project_key": self.registration.project_key,
                "project_display_name": self.registration.display_name,
                "repo_root": self.registration.repo_root,
                "owning_bot": self.registration.owning_bot,
                "assigned_bot": self.registration.assigned_bot,
            },
        )

    def is_busy(self) -> bool:
        return len(self.repository.list_active_leases(runner_id=DEFAULT_RUNNER_ID)) > 0

    def active_issue_key(self) -> str | None:
        leases = self.repository.list_active_leases(runner_id=DEFAULT_RUNNER_ID)
        if not leases:
            return None
        return leases[0].issue_key

    def queue_depth(self) -> int:
        return len(self.repository.list_commands(status="pending", runner_id=DEFAULT_RUNNER_ID))

    def effective_session(
        self,
        *,
        active_issue_key: str | None = None,
        continuity: Any = None,
        prune_stale: bool = True,
    ):
        session = self.store.get_session(DEFAULT_RUNNER_ID)
        if session is None:
            return None
        if self.executor.transport.has_session(session.session_name):
            return session
        recoverable = continuity is not None and continuity.active_slice_id is not None
        if prune_stale and active_issue_key is None and not recoverable:
            self.store.clear_session(DEFAULT_RUNNER_ID)
        return None

    def dispatch_issue(self, issue: MirroredIssueRecord) -> RuntimeDispatchResult:
        self.ensure_runner()
        session = self.store.get_session(DEFAULT_RUNNER_ID)
        active_issue = self.active_issue_key()
        continuity = self.continuity.get_state(issue.identifier, DEFAULT_RUNNER_ID)

        if active_issue and active_issue != issue.identifier:
            return self._build_result(
                issue=issue,
                action="busy",
                session_name=session.session_name if session is not None else None,
                pane_target=session.pane_target if session is not None else None,
            )

        if active_issue == issue.identifier and continuity is not None and continuity.active_slice_id is not None:
            if session is None or not self.executor.transport.has_session(session.session_name):
                recovered = self.executor.replay_slice_request(continuity.active_slice_id)
                return self._build_result(
                    issue=issue,
                    action="recovered",
                    session_name=recovered.session_name,
                    pane_target=recovered.pane_target,
                )
            self.executor.heartbeat(DEFAULT_RUNNER_ID)
            current = self.store.get_session(DEFAULT_RUNNER_ID)
            return self._build_result(
                issue=issue,
                action="resumed",
                session_name=current.session_name if current is not None else None,
                pane_target=current.pane_target if current is not None else None,
            )

        request = self.executor.dispatch_slice(
            issue_key=issue.identifier,
            runner_id=DEFAULT_RUNNER_ID,
            objective=issue.title,
            slice_goal=issue.title,
            acceptance=_issue_acceptance(issue),
            validation_plan=[f"Confirm {issue.identifier} remains on the intended ORX project runtime."],
            context={
                "project_key": self.registration.project_key,
                "project_display_name": self.registration.display_name,
                "repo_root": self.registration.repo_root,
                "linear": {
                    "identifier": issue.identifier,
                    "title": issue.title,
                    "project_id": issue.project_id,
                    "project_name": issue.project_name,
                },
            },
        )
        current = self.store.get_session(DEFAULT_RUNNER_ID)
        return self._build_result(
            issue=issue,
            action="started",
            session_name=current.session_name if current is not None else request.session_name,
            pane_target=current.pane_target if current is not None else None,
        )

    def recover_active(self) -> RuntimeDispatchResult | None:
        active_issue = self.active_issue_key()
        if active_issue is None:
            return None
        continuity = self.continuity.get_state(active_issue, DEFAULT_RUNNER_ID)
        if continuity is None or continuity.active_slice_id is None:
            return None
        session = self.store.get_session(DEFAULT_RUNNER_ID)
        if session is not None and self.executor.transport.has_session(session.session_name):
            return None

        recovered = self.executor.replay_slice_request(continuity.active_slice_id)
        return RuntimeDispatchResult(
            project_key=self.registration.project_key,
            project_display_name=self.registration.display_name,
            runtime_home=str(self.paths.home),
            runner_id=DEFAULT_RUNNER_ID,
            issue_key=active_issue,
            issue_title=continuity.objective,
            action="recovered",
            session_name=recovered.session_name,
            pane_target=recovered.pane_target,
            queue_depth=self.queue_depth(),
            daemon_tick=_current_daemon_tick(self.runtime_state),
        )

    def queue_control_command(
        self,
        *,
        command_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> CommandRecord:
        self.ensure_runner()
        active_issue = self.active_issue_key()
        if active_issue is None:
            raise ValueError(f"No active issue for project {self.registration.project_key}.")
        from .commands import normalize_command

        command = normalize_command(
            command_kind,
            issue_key=active_issue,
            runner_id=DEFAULT_RUNNER_ID,
            payload=payload or {"project_key": self.registration.project_key},
        )
        return self.repository.enqueue_normalized_command(command)

    def advance(self) -> RuntimeDispatchResult | None:
        active_issue = self.active_issue_key()
        if active_issue is None:
            return None
        state = self.continuity.get_state(active_issue, DEFAULT_RUNNER_ID)
        if state is None or state.active_slice_id is not None:
            return None
        if state.last_result_status != "success":
            return None
        next_slice = (state.next_slice or "").strip()
        if not next_slice:
            return None

        request = self.executor.dispatch_slice(
            issue_key=active_issue,
            runner_id=DEFAULT_RUNNER_ID,
            objective=state.objective,
            slice_goal=next_slice,
            acceptance=list(state.acceptance),
            validation_plan=list(state.validation_plan),
            blockers=list(state.blockers),
            discovered_gaps=list(state.discovered_gaps),
            context=state.resume_context,
            resume_context=state.resume_context,
        )
        current = self.store.get_session(DEFAULT_RUNNER_ID)
        return RuntimeDispatchResult(
            project_key=self.registration.project_key,
            project_display_name=self.registration.display_name,
            runtime_home=str(self.paths.home),
            runner_id=DEFAULT_RUNNER_ID,
            issue_key=active_issue,
            issue_title=state.objective,
            action="continued",
            session_name=current.session_name if current is not None else request.session_name,
            pane_target=current.pane_target if current is not None else None,
            queue_depth=self.queue_depth(),
            daemon_tick=_current_daemon_tick(self.runtime_state),
        )

    def submit_slice_result(
        self,
        *,
        slice_id: str,
        payload: dict[str, Any],
        gate: SliceApplyGate | None = None,
    ) -> RuntimeSliceResult:
        result = self.executor.submit_slice_result(slice_id, payload, gate=gate)
        finalized = (
            result.apply_status == "applied"
            and result.status == "success"
            and result.verified
            and result.next_slice is None
        )
        session = self.store.get_session(DEFAULT_RUNNER_ID)
        if finalized:
            self.repository.release_issue_lease(result.issue_key, DEFAULT_RUNNER_ID)
            if session is not None:
                self.store.upsert_session(
                    runner_id=session.runner_id,
                    issue_key=session.issue_key,
                    session_name=session.session_name,
                    pane_target=session.pane_target,
                    transport=session.transport,
                    heartbeat_at=session.heartbeat_at,
                    last_result_at=result.submitted_at,
                    state="idle",
                    metadata=session.metadata,
                )
        return RuntimeSliceResult(
            project_key=self.registration.project_key,
            runner_id=DEFAULT_RUNNER_ID,
            issue_key=result.issue_key,
            status=result.status,
            verified=result.verified,
            next_slice=result.next_slice,
            finalized=finalized,
            queue_depth=self.queue_depth(),
            submitted_at=result.submitted_at,
            session_name=session.session_name if session is not None else None,
            pane_target=session.pane_target if session is not None else None,
            daemon_tick=_current_daemon_tick(self.runtime_state),
            apply_status=result.apply_status,
            stale_reason=result.stale_reason,
        )

    def status_payload(self) -> dict[str, Any]:
        active_issue = self.active_issue_key()
        continuity = (
            self.continuity.get_state(active_issue, DEFAULT_RUNNER_ID)
            if active_issue is not None
            else None
        )
        session = self.effective_session(
            active_issue_key=active_issue,
            continuity=continuity,
        )
        latest_validation = (
            self.validation.latest(issue_key=active_issue, runner_id=DEFAULT_RUNNER_ID)
            if active_issue is not None
            else None
        )
        payload = {
            "ok": True,
            "schema_version": self.storage.current_version(),
            "runners": [
                {
                    "runner_id": record.runner_id,
                    "transport": record.transport,
                    "display_name": record.display_name,
                    "state": record.state,
                    "metadata": record.metadata,
                }
                for record in self.repository.list_runners(runner_id=DEFAULT_RUNNER_ID)
            ],
            "leases": [
                {
                    "lease_id": record.lease_id,
                    "issue_key": record.issue_key,
                    "runner_id": record.runner_id,
                    "acquired_at": record.acquired_at,
                    "released_at": record.released_at,
                }
                for record in self.repository.list_active_leases(runner_id=DEFAULT_RUNNER_ID)
            ],
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
                    runner_id=DEFAULT_RUNNER_ID,
                )
            ],
            "continuity": None
            if continuity is None
            else {
                "issue_key": continuity.issue_key,
                "runner_id": continuity.runner_id,
                "objective": continuity.objective,
                "slice_goal": continuity.slice_goal,
                "next_slice": continuity.next_slice,
                "active_slice_id": continuity.active_slice_id,
                "last_result_status": continuity.last_result_status,
                "last_result_summary": continuity.last_result_summary,
                "updated_at": continuity.updated_at,
            },
            "daemon": None if self.runtime_state.get_last_tick() is None else self.runtime_state.get_last_tick().value,
            "validation": None
            if latest_validation is None
            else {
                "validation_id": latest_validation.validation_id,
                "summary": latest_validation.summary,
                "surface": latest_validation.surface,
                "tool": latest_validation.tool,
                "result": latest_validation.result,
                "confidence": latest_validation.confidence,
                "created_at": latest_validation.created_at,
            },
        }
        payload["project"] = _serialize_project(self.registration)
        payload["active_issue_key"] = active_issue
        payload["queue_depth"] = self.queue_depth()
        payload["session"] = None if session is None else _serialize_session(session)
        return payload

    def dashboard_entry(self) -> dict[str, Any]:
        active_issue = self.active_issue_key()
        continuity = (
            self.continuity.get_state(active_issue, DEFAULT_RUNNER_ID)
            if active_issue is not None
            else None
        )
        session = self.effective_session(
            active_issue_key=active_issue,
            continuity=continuity,
        )
        daemon = self.runtime_state.get_last_tick()
        return {
            "project": _serialize_project(self.registration),
            "runner_id": DEFAULT_RUNNER_ID,
            "active_issue_key": active_issue,
            "queue_depth": self.queue_depth(),
            "busy": active_issue is not None,
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
            "daemon": None if daemon is None else daemon.value,
        }

    def _build_result(
        self,
        *,
        issue: MirroredIssueRecord,
        action: str,
        session_name: str | None,
        pane_target: str | None,
    ) -> RuntimeDispatchResult:
        daemon = self.runtime_state.get_last_tick()
        return RuntimeDispatchResult(
            project_key=self.registration.project_key,
            project_display_name=self.registration.display_name,
            runtime_home=str(self.paths.home),
            runner_id=DEFAULT_RUNNER_ID,
            issue_key=issue.identifier,
            issue_title=issue.title,
            action=action,
            session_name=session_name,
            pane_target=pane_target,
            queue_depth=self.queue_depth(),
            daemon_tick=None if daemon is None else str(daemon.value.get("tick")),
        )


def _serialize_project(project: ProjectRegistration) -> dict[str, Any]:
    execution_thread_id = None
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    raw_execution_thread = metadata.get("execution_thread_id")
    if isinstance(raw_execution_thread, int):
        execution_thread_id = raw_execution_thread
    elif isinstance(raw_execution_thread, str):
        try:
            execution_thread_id = int(raw_execution_thread)
        except ValueError:
            execution_thread_id = None
    if execution_thread_id is None:
        execution_thread_id = project.owner_thread_id
    return {
        "project_key": project.project_key,
        "display_name": project.display_name,
        "repo_root": project.repo_root,
        "runtime_home": project.runtime_home,
        "owning_bot": project.owning_bot,
        "assigned_bot": project.assigned_bot,
        "owner_chat_id": project.owner_chat_id,
        "owner_thread_id": project.owner_thread_id,
        "execution_thread_id": execution_thread_id,
        "metadata": project.metadata,
    }


def _serialize_session(session) -> dict[str, Any]:
    return {
        "issue_key": session.issue_key,
        "session_name": session.session_name,
        "pane_target": session.pane_target,
        "state": session.state,
        "heartbeat_at": session.heartbeat_at,
        "last_result_at": session.last_result_at,
    }


def _issue_acceptance(issue: MirroredIssueRecord) -> list[str]:
    first_line = issue.description.strip().splitlines()[0].strip() if issue.description.strip() else ""
    if first_line:
        return [first_line]
    return [f"Advance {issue.identifier} on the correct project runtime."]


def _current_daemon_tick(runtime_state: DaemonStateService) -> str | None:
    daemon = runtime_state.get_last_tick()
    if daemon is None or not isinstance(daemon.value, dict):
        return None
    tick = daemon.value.get("tick")
    return str(tick) if tick is not None else None
