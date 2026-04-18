"""Global ORX dispatch, cross-project arbitration, and restart context assembly."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Callable

from .config import normalize_project_key, resolve_project_runtime_paths
from .executor import TmuxTransport
from .handoff_interpreter import interpret_slice_handoff
from .linear_client import LinearGraphQLClient
from .mirror import LinearMirrorRepository, MirroredIssueRecord
from .ranking import LinearRankingService
from .recovery import RecoveryService
from .registry import (
    BotRegistration,
    DispatchNotification,
    ProjectRegistration,
    ProjectRegistry,
)
from .runtime import DEFAULT_RUNNER_ID, ProjectRuntimeService, RuntimeDispatchResult
from .executor import SliceApplyGate
from .storage import Storage
from .ticket_handoff import (
    build_raw_slice_facts_section,
    build_latest_handoff_section,
    extract_latest_handoff,
    latest_handoff_revision,
    replace_raw_slice_facts,
    replace_latest_handoff,
)
from .ui_policy import classify_ui_routing, evaluate_ui_gate


@dataclass(frozen=True)
class DispatchRunResult:
    decision: str
    issue_key: str | None = None
    issue_title: str | None = None
    project_key: str | None = None
    feature_key: str | None = None
    lane_state: str | None = None
    release_required: bool = False
    owning_bot: str | None = None
    assigned_bot: str | None = None
    assignment_action: str | None = None
    handoff_required: bool = False
    ingress_message: str = ""
    owner_message: str | None = None
    runtime: RuntimeDispatchResult | None = None
    notification_id: int | None = None


@dataclass(frozen=True)
class DrainProjectResult:
    project_key: str
    issue_key: str
    action: str
    session_name: str | None


@dataclass(frozen=True)
class DispatchSliceResult:
    project_key: str
    issue_key: str
    status: str
    verified: bool
    next_slice: str | None
    finalized: bool
    linear_completed: bool
    session_name: str | None
    pane_target: str | None
    apply_status: str
    stale_reason: str | None


@dataclass(frozen=True)
class RecoverySummary:
    action: str
    reason: str
    issue_key: str
    runner_id: str
    active_slice_id: str | None
    next_slice: str | None
    proposal_key: str | None = None


@dataclass(frozen=True)
class WorktreeEvidence:
    worktree_path: str
    branch: str | None
    git_head: str | None
    changed_files: tuple[str, ...]
    status_lines: tuple[str, ...]
    diff_excerpt: str


@dataclass(frozen=True)
class DriftedProjectSummary:
    project_key: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


class GlobalDispatchService:
    def __init__(
        self,
        *,
        storage: Storage,
        registry: ProjectRegistry | None = None,
        mirror: LinearMirrorRepository | None = None,
        linear_client: LinearGraphQLClient | None = None,
        transport_factory: Callable[[], TmuxTransport] | None = None,
    ) -> None:
        self.storage = storage
        self.registry = registry or ProjectRegistry(storage)
        self.mirror = mirror or LinearMirrorRepository(storage)
        self.linear_client = linear_client
        self.ranking = LinearRankingService(self.mirror)
        self.transport_factory = transport_factory

    def register_project(
        self,
        *,
        project_key: str,
        display_name: str,
        repo_root: str,
        owning_bot: str | None = None,
        owner_chat_id: int | None = None,
        owner_thread_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRegistration:
        if owning_bot is not None and owning_bot.strip():
            self.registry.upsert_bot(
                bot_identity=owning_bot,
                default_display_name=display_name.strip() or project_key,
                telegram_chat_id=owner_chat_id,
                telegram_thread_id=owner_thread_id,
                metadata={"source": "project-registration"},
            )
        runtime_home = str(resolve_project_runtime_paths(project_key, home=self.storage.paths.home).home)
        return self.registry.upsert_project(
            project_key=project_key,
            display_name=display_name,
            repo_root=repo_root,
            runtime_home=runtime_home,
            owning_bot=owning_bot,
            owner_chat_id=owner_chat_id,
            owner_thread_id=owner_thread_id,
            metadata=metadata,
        )

    def register_bot(
        self,
        *,
        bot_identity: str,
        default_display_name: str,
        telegram_chat_id: int | None = None,
        telegram_thread_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BotRegistration:
        return self.registry.upsert_bot(
            bot_identity=bot_identity,
            default_display_name=default_display_name,
            telegram_chat_id=telegram_chat_id,
            telegram_thread_id=telegram_thread_id,
            metadata=metadata,
        )

    def deregister_project(self, *, project_key: str) -> ProjectRegistration | None:
        return self.registry.delete_project(project_key)

    def dispatch_run(
        self,
        *,
        ingress_bot: str,
        ingress_chat_id: int | None = None,
        ingress_thread_id: int | None = None,
        explicit_issue_key: str | None = None,
        explicit_project_key: str | None = None,
    ) -> DispatchRunResult:
        owner_id = f"dispatch:{uuid.uuid4().hex}"
        self.registry.acquire_dispatch_lease(owner_id)
        try:
            requested_registration = (
                self.registry.get_project(explicit_project_key) if explicit_project_key else None
            )
            if explicit_project_key:
                if requested_registration is not None:
                    drift = self.build_project_drift(project_key=requested_registration.project_key)
                    if drift["blockers"]:
                        return DispatchRunResult(
                            decision="drift-blocked",
                            project_key=requested_registration.project_key,
                            feature_key=_lane_feature_key(requested_registration),
                            lane_state=_lane_state(requested_registration),
                            release_required=_lane_release_required(requested_registration),
                            owning_bot=requested_registration.owning_bot,
                            assigned_bot=requested_registration.assigned_bot,
                            ingress_message=(
                                f"Refusing to dispatch project `{requested_registration.project_key}` because it has drift blockers. "
                                "Inspect `orx dispatch drift` or `/control/drift` first."
                            ),
                        )
                    lane_blocked = self._lane_blocked_result(
                        registration=requested_registration,
                        explicit_issue_key=explicit_issue_key,
                    )
                    if lane_blocked is not None:
                        return lane_blocked
                    active_runs = self._active_runs(
                        ingress_bot=ingress_bot,
                        explicit_project_key=requested_registration.project_key,
                    )
                    if active_runs:
                        return self._already_running_result(
                            ingress_bot=ingress_bot,
                            registration=active_runs[0][0],
                            active_issue=active_runs[0][1],
                            session_name=active_runs[0][2],
                        )
            issue = self._select_issue(
                explicit_issue_key=explicit_issue_key,
                explicit_project_key=explicit_project_key,
            )
            if issue is None:
                ingress_project = self.registry.get_project_for_bot(ingress_bot)
                if ingress_project is not None:
                    lane_blocked = self._lane_blocked_result(
                        registration=ingress_project,
                        explicit_issue_key=explicit_issue_key,
                    )
                    if lane_blocked is not None:
                        return lane_blocked
                active_runs = self._active_runs(
                    ingress_bot=ingress_bot,
                    explicit_project_key=explicit_project_key,
                )
                if active_runs:
                    if len(active_runs) == 1:
                        registration, active_issue, session_name = active_runs[0]
                        return self._already_running_result(
                            ingress_bot=ingress_bot,
                            registration=registration,
                            active_issue=active_issue,
                            session_name=session_name,
                        )
                    return DispatchRunResult(
                        decision="already-running",
                        assignment_action="active",
                        ingress_message=_active_runs_summary(active_runs),
                    )
                return DispatchRunResult(
                    decision="no-work",
                    ingress_message="No runnable Linear work is currently available.",
                )

            project_key = _issue_project_key(issue)
            registration = self.registry.get_project(project_key)
            if registration is None:
                return DispatchRunResult(
                    decision="unregistered-project",
                    issue_key=issue.identifier,
                    issue_title=issue.title,
                    project_key=project_key,
                    feature_key=_feature_key_from_issue(issue),
                    ingress_message=(
                        f"Selected {issue.identifier}, but project `{project_key}` is not registered in ORX."
                    ),
                )

            drift = self.build_project_drift(project_key=registration.project_key)
            if drift["blockers"]:
                return DispatchRunResult(
                    decision="drift-blocked",
                    issue_key=issue.identifier,
                    issue_title=issue.title,
                    project_key=registration.project_key,
                    feature_key=_feature_key_from_issue(issue),
                    lane_state=_lane_state(registration),
                    release_required=_lane_release_required(registration),
                    owning_bot=registration.owning_bot,
                    assigned_bot=registration.assigned_bot,
                    ingress_message=(
                        f"Refusing to dispatch {issue.identifier} because project `{registration.project_key}` "
                        "has drift blockers. Inspect `orx dispatch drift` or `/control/drift` first."
                    ),
                )

            assignment = self.registry.assign_project_bot(
                project_key=registration.project_key,
                preferred_bot=ingress_bot,
            )
            if assignment is None:
                return DispatchRunResult(
                    decision="no-available-bot",
                    issue_key=issue.identifier,
                    issue_title=issue.title,
                    project_key=registration.project_key,
                    feature_key=_feature_key_from_issue(issue),
                    ingress_message=(
                        f"Project `{registration.project_key}` has runnable work (`{issue.identifier}`), "
                        "but no Telegram bot is currently available to own the run."
                    ),
                )
            registration = assignment.project
            runtime_service = self._runtime_service(registration)
            previous_continuity = runtime_service.continuity.get_state(
                issue.identifier,
                DEFAULT_RUNNER_ID,
            )
            try:
                runtime = runtime_service.dispatch_issue(issue)
                if not self._wait_for_durable_runner_session(
                    runtime_service=runtime_service,
                    issue_key=issue.identifier,
                ):
                    self._rollback_runtime_dispatch_attempt(
                        runtime_service=runtime_service,
                        issue_key=issue.identifier,
                        previous_continuity=previous_continuity,
                    )
                    raise RuntimeError(
                        "Managed runner session exited before ORX could confirm a durable start."
                    )
            except Exception as error:
                registration = self._record_launch_failure(
                    registration=registration,
                    issue=issue,
                    assignment_action=assignment.action,
                    error=error,
                )
                return DispatchRunResult(
                    decision="launch-failed",
                    issue_key=issue.identifier,
                    issue_title=issue.title,
                    project_key=registration.project_key,
                    feature_key=_feature_key_from_issue(issue),
                    lane_state=_lane_state(registration),
                    release_required=_lane_release_required(registration),
                    owning_bot=registration.owning_bot,
                    assigned_bot=registration.assigned_bot,
                    assignment_action=assignment.action,
                    ingress_message=(
                        f"Managed start failed for `{issue.identifier}` on `{registration.project_key}`: {error}"
                    ),
                )
            registration = self.registry.set_project_feature_lane(
                project_key=registration.project_key,
                lane=_feature_lane_payload(
                    issue=issue,
                    lane_state="executing",
                    release_required=False,
                ),
            )
            registration = self.registry.set_project_reconciliation(
                project_key=registration.project_key,
                reconciliation=None,
            )
            bot = self.registry.set_bot_display_target(
                bot_identity=assignment.bot.bot_identity,
                desired_display_name=_project_issue_display_name(
                    project_key=registration.project_key,
                    issue_title=issue.title,
                ),
                assignment_id=assignment.bot.assignment_id,
            ) or assignment.bot
            handoff_required = ingress_bot.strip() != registration.owning_bot.strip()
            owner_message = _owner_message(issue, runtime)
            notification_id: int | None = None
            if registration.assigned_bot is not None:
                notification = self.registry.create_notification(
                    project_key=registration.project_key,
                    target_bot=registration.owning_bot,
                    assignment_id=bot.assignment_id,
                    ingress_bot=ingress_bot,
                    ingress_chat_id=None if ingress_chat_id is None else str(ingress_chat_id),
                    ingress_thread_id=None if ingress_thread_id is None else str(ingress_thread_id),
                    issue_key=issue.identifier,
                    kind="dispatch-handoff" if handoff_required else "dispatch-started",
                    payload={
                        "message": owner_message,
                        "issue_key": issue.identifier,
                        "issue_title": issue.title,
                        "project_key": registration.project_key,
                        "assigned_bot": registration.assigned_bot,
                        "assignment_action": assignment.action,
                        "desired_bot_display_name": bot.desired_display_name,
                        "action": runtime.action,
                        "target_chat_id": registration.owner_chat_id,
                        "target_thread_id": _project_execution_thread_id(registration),
                        "control_thread_id": registration.owner_thread_id,
                        "execution_thread_id": _project_execution_thread_id(registration),
                    },
                )
                notification_id = notification.notification_id
            return DispatchRunResult(
                decision="dispatched",
                issue_key=issue.identifier,
                issue_title=issue.title,
                project_key=registration.project_key,
                feature_key=_feature_key_from_issue(issue),
                lane_state="executing",
                release_required=False,
                owning_bot=registration.owning_bot,
                assigned_bot=registration.assigned_bot,
                assignment_action=assignment.action,
                handoff_required=handoff_required,
                ingress_message=_ingress_message(
                    issue=issue,
                    registration=registration,
                    runtime=runtime,
                    handoff_required=handoff_required,
                    assignment_action=assignment.action,
                ),
                owner_message=owner_message,
                runtime=runtime,
                notification_id=notification_id,
            )
        finally:
            self.registry.release_dispatch_lease(owner_id)

    def _lane_blocked_result(
        self,
        *,
        registration: ProjectRegistration,
        explicit_issue_key: str | None,
    ) -> DispatchRunResult | None:
        lane = _project_feature_lane(registration)
        if lane is None:
            return None
        lane_state = lane["lane_state"]
        if lane_state == "awaiting_hil_release":
            issue_label = lane.get("last_issue_key") or "the finished feature"
            return DispatchRunResult(
                decision="awaiting-hil-release",
                issue_key=lane.get("last_issue_key"),
                issue_title=lane.get("last_issue_title"),
                project_key=registration.project_key,
                feature_key=lane["feature_key"],
                lane_state=lane_state,
                release_required=bool(lane.get("release_required")),
                owning_bot=registration.owning_bot,
                assigned_bot=registration.assigned_bot,
                ingress_message=(
                    f"Project `{registration.project_key}` is reserved for feature `{lane['feature_key']}` "
                    f"after finishing `{issue_label}`. Release that bot through HIL before assigning unrelated work."
                ),
            )
        if lane_state == "launch_failed":
            return DispatchRunResult(
                decision="launch-failed",
                issue_key=lane.get("last_issue_key"),
                issue_title=lane.get("last_issue_title"),
                project_key=registration.project_key,
                feature_key=lane["feature_key"],
                lane_state=lane_state,
                release_required=bool(lane.get("release_required")),
                owning_bot=registration.owning_bot,
                assigned_bot=registration.assigned_bot,
                ingress_message=(
                    f"Project `{registration.project_key}` is blocked by a failed managed start for "
                    f"`{lane.get('last_issue_key') or lane['feature_key']}`. Run ORX failed-start recovery first."
                ),
            )
        if lane_state == "awaiting_orx_review":
            return DispatchRunResult(
                decision="awaiting-orx-review",
                issue_key=lane.get("last_issue_key"),
                issue_title=lane.get("last_issue_title"),
                project_key=registration.project_key,
                feature_key=lane["feature_key"],
                lane_state=lane_state,
                release_required=False,
                owning_bot=registration.owning_bot,
                assigned_bot=registration.assigned_bot,
                ingress_message=(
                    f"Project `{registration.project_key}` is paused for ORX reconciliation after "
                    f"`{lane.get('last_issue_key') or lane['feature_key']}`. Wait for ORX to interpret the last slice."
                ),
            )
        return None

    def _already_running_result(
        self,
        *,
        ingress_bot: str,
        registration: ProjectRegistration,
        active_issue: MirroredIssueRecord,
        session_name: str | None,
    ) -> DispatchRunResult:
        handoff_required = ingress_bot.strip() != registration.owning_bot.strip()
        return DispatchRunResult(
            decision="already-running",
            issue_key=active_issue.identifier,
            issue_title=active_issue.title,
            project_key=registration.project_key,
            feature_key=_feature_key_from_issue(active_issue),
            lane_state=_lane_state(registration) or "executing",
            release_required=_lane_release_required(registration),
            owning_bot=registration.owning_bot,
            assigned_bot=registration.assigned_bot,
            assignment_action="active",
            handoff_required=handoff_required,
            ingress_message=_active_run_message(
                registration=registration,
                issue=active_issue,
                session_name=session_name,
                handoff_required=handoff_required,
            ),
            owner_message=None,
            runtime=None,
            notification_id=None,
        )

    def _record_launch_failure(
        self,
        *,
        registration: ProjectRegistration,
        issue: MirroredIssueRecord,
        assignment_action: str,
        error: Exception,
    ) -> ProjectRegistration:
        updated = self.registry.set_project_feature_lane(
            project_key=registration.project_key,
            lane=_feature_lane_payload(
                issue=issue,
                lane_state="launch_failed",
                release_required=False,
                release_action="failed_start",
                release_note=str(error),
            ),
        )
        if assignment_action == "assigned":
            released = self.registry.release_project_bot(project_key=registration.project_key)
            return released.project if released is not None else self.registry.get_project(registration.project_key) or updated
        return updated

    def _active_runs(
        self,
        *,
        ingress_bot: str,
        explicit_project_key: str | None,
    ) -> list[tuple[ProjectRegistration, MirroredIssueRecord, str | None]]:
        active_runs: list[tuple[ProjectRegistration, MirroredIssueRecord, str | None]] = []
        requested_project = (
            normalize_project_key(explicit_project_key) if explicit_project_key else None
        )
        for registration in self.registry.list_projects():
            if requested_project is not None and registration.project_key != requested_project:
                continue
            runtime = self._runtime_service(registration)
            active_issue_key = runtime.active_issue_key()
            if active_issue_key is None:
                continue
            issue = self.mirror.get_issue(identifier=active_issue_key)
            if issue is None:
                continue
            continuity = runtime.continuity.get_state(active_issue_key, DEFAULT_RUNNER_ID)
            session = runtime.effective_session(
                active_issue_key=active_issue_key,
                continuity=continuity,
            )
            active_runs.append(
                (
                    registration,
                    issue,
                    None if session is None else session.session_name,
                )
            )

        ingress_project = self.registry.get_project_for_bot(ingress_bot)
        if ingress_project is not None:
            for index, (registration, _, _) in enumerate(active_runs):
                if registration.project_key == ingress_project.project_key:
                    if index:
                        active_runs.insert(0, active_runs.pop(index))
                    break
        return active_runs

    def control_status(self, *, project_key: str) -> dict[str, Any]:
        registration = self._require_project(project_key)
        payload = self._runtime_service(registration).status_payload()
        payload["reconciliation"] = _project_reconciliation(registration)
        payload["restart_context"] = self.build_restart_context(project_key=project_key)
        payload["drift"] = self.build_project_drift(project_key=project_key)
        return payload

    def control_queue_payload_for_project(self, *, project_key: str) -> dict[str, Any]:
        status = self.control_status(project_key=project_key)
        return {
            "ok": True,
            "project": status["project"],
            "active_issue_key": status["active_issue_key"],
            "queue_depth": status["queue_depth"],
            "queue": status["queue"],
        }

    def build_restart_context(self, *, project_key: str) -> dict[str, Any]:
        registration = self._require_project(project_key)
        runtime = self._runtime_service(registration)
        active_issue_key, continuity, active_request, stored_session = self._runtime_recovery_state(runtime)
        session = runtime.effective_session(
            active_issue_key=active_issue_key,
            continuity=continuity,
        )
        latest_validation = (
            runtime.validation.latest(issue_key=active_issue_key, runner_id=DEFAULT_RUNNER_ID)
            if active_issue_key is not None
            else None
        )
        issue = self.mirror.get_issue(identifier=active_issue_key) if active_issue_key is not None else None
        ancestors = self.mirror.get_ancestor_chain(issue) if issue is not None else ()
        recovery = (
            RecoveryService(
                runtime.storage,
                continuity=runtime.continuity,
            ).assess(active_issue_key, DEFAULT_RUNNER_ID)
            if active_issue_key is not None and continuity is not None
            else None
        )
        next_candidate = None
        if active_issue_key is None:
            next_candidate = self._select_issue(
                explicit_issue_key=None,
                explicit_project_key=registration.project_key,
            )
        daemon = runtime.runtime_state.get_last_tick()
        drift = self._build_drift_report(
            registration=registration,
            runtime=runtime,
            active_issue_key=active_issue_key,
            issue=issue,
            continuity=continuity,
            active_request=active_request,
            session=stored_session,
        )
        execution_packet = _build_execution_packet(
            registration=registration,
            issue=issue,
            continuity=continuity,
            active_request=active_request,
        )
        start_state, remediation = self._restart_start_state(
            registration=registration,
            project_key=registration.project_key,
            active_issue_key=active_issue_key,
            session=session,
            execution_packet=execution_packet,
            drift=drift,
        )

        return {
            "project": _serialize_project(registration),
            "runtime": {
                "runner_id": DEFAULT_RUNNER_ID,
                "active_issue_key": active_issue_key,
                "queue_depth": runtime.queue_depth(),
                "busy": runtime.is_busy(),
                "session": None if session is None else _serialize_session(session),
                "daemon": None if daemon is None else daemon.value,
            },
            "start_state": start_state,
            "remediation": remediation,
            "issue": None if issue is None else _serialize_issue(issue),
            "execution_packet": execution_packet,
            "ancestors": [_serialize_issue(record) for record in ancestors],
            "continuity": None if continuity is None else _serialize_continuity(continuity),
            "active_slice_request": (
                None if active_request is None else _serialize_slice_request(active_request)
            ),
            "latest_validation": None
            if latest_validation is None
            else {
                "validation_id": latest_validation.validation_id,
                "surface": latest_validation.surface,
                "tool": latest_validation.tool,
                "result": latest_validation.result,
                "confidence": latest_validation.confidence,
                "summary": latest_validation.summary,
                "details": latest_validation.details,
                "blockers": list(latest_validation.blockers),
                "created_at": latest_validation.created_at,
            },
            "recovery": None
            if recovery is None
            else _serialize_recovery(
                _override_recovery_for_drift(
                    recovery=recovery,
                    drift=drift,
                    project_key=registration.project_key,
                )
            ),
            "reconciliation": _project_reconciliation(registration),
            "next_candidate": None if next_candidate is None else _serialize_issue(next_candidate),
            "drift": drift,
            "durable_sources": [
                "linear_mirror",
                "project_registry",
                "project_runtime",
                "continuity_state",
                "slice_requests",
                "runner_reconciliation",
                "validation_ledger",
            ],
        }

    def _restart_start_state(
        self,
        *,
        registration: ProjectRegistration,
        project_key: str,
        active_issue_key: str | None,
        session: Any,
        execution_packet: dict[str, Any] | None,
        drift: dict[str, Any],
    ) -> tuple[str, str]:
        blockers = drift.get("blockers") if isinstance(drift, dict) else None
        if isinstance(blockers, list) and blockers:
            return (
                "drift_blocked",
                (
                    f"Project `{project_key}` has ORX drift blockers. Repair ORX drift for this project "
                    "before starting or resuming a managed runner."
                ),
            )

        lane = _project_feature_lane(registration)
        if lane is not None and lane["lane_state"] == "launch_failed":
            return (
                "launch_failed",
                (
                    f"Project `{project_key}` is blocked by a failed managed start for "
                    f"`{lane.get('last_issue_key') or lane['feature_key']}`. Repair the failed start before retrying."
                ),
            )

        if lane is not None and lane["lane_state"] == "awaiting_hil_release":
            return (
                "awaiting_hil_release",
                (
                    f"Project `{project_key}` is reserved for feature `{lane['feature_key']}` until HIL releases it. "
                    "Do not start unrelated managed work on this bot yet."
                ),
            )
        if lane is not None and lane["lane_state"] == "awaiting_orx_review":
            return (
                "awaiting_orx_review",
                (
                    f"Project `{project_key}` is waiting for ORX reconciliation for "
                    f"`{lane.get('last_issue_key') or lane['feature_key']}` before another managed start."
                ),
            )

        if active_issue_key is None or execution_packet is None:
            return (
                "no_work",
                (
                    f"Project `{project_key}` has no active ORX execution packet. "
                    "Dispatch work through ORX first, then rerun the managed runner."
                ),
            )

        if session is not None:
            return (
                "already_running",
                (
                    f"Project `{project_key}` is already running `{active_issue_key}`. "
                    "Attach to the existing managed runner instead of starting a second session."
                ),
            )

        return (
            "runnable",
            (
                f"Project `{project_key}` has a valid ORX execution packet for `{active_issue_key}`. "
                "Start or resume the managed runner from this packet only."
            ),
        )

    def build_project_drift(self, *, project_key: str) -> dict[str, Any]:
        registration = self._require_project(project_key)
        runtime = self._runtime_service(registration)
        active_issue_key, continuity, active_request, session = self._runtime_recovery_state(runtime)
        issue = self.mirror.get_issue(identifier=active_issue_key) if active_issue_key is not None else None
        return self._build_drift_report(
            registration=registration,
            runtime=runtime,
            active_issue_key=active_issue_key,
            issue=issue,
            continuity=continuity,
            active_request=active_request,
            session=session,
        )

    def control_queue_command(
        self,
        *,
        project_key: str,
        command_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        registration = self._require_project(project_key)
        record = self._runtime_service(registration).queue_control_command(
            command_kind=command_kind,
            payload=payload,
        )
        return {
            "ok": True,
            "project": _serialize_project(registration),
            "command": {
                "command_id": record.command_id,
                "issue_key": record.issue_key,
                "runner_id": record.runner_id,
                "command_kind": record.command_kind,
                "payload": record.payload,
                "status": record.status,
                "priority": record.priority,
            },
        }

    def bot_status(self, *, bot_identity: str) -> dict[str, Any]:
        bot = self.registry.get_bot(bot_identity)
        if bot is None:
            raise ValueError(f"Unknown bot {bot_identity}.")
        project = (
            self.registry.get_project(bot.assigned_project_key)
            if bot.assigned_project_key is not None
            else None
        )
        issue = None
        runtime_status = None
        if project is not None:
            runtime_status = self.control_status(project_key=project.project_key)
            active_issue_key = runtime_status.get("active_issue_key")
            if isinstance(active_issue_key, str) and active_issue_key.strip():
                issue = self.mirror.get_issue(identifier=active_issue_key)
        return {
            "ok": True,
            "bot": _serialize_bot(bot),
            "project": None if project is None else _serialize_project(project),
            "active_issue": None if issue is None else _serialize_issue(issue),
            "status": runtime_status,
        }

    def bot_queue(self, *, bot_identity: str) -> dict[str, Any]:
        bot = self.registry.get_bot(bot_identity)
        if bot is None:
            raise ValueError(f"Unknown bot {bot_identity}.")
        if bot.assigned_project_key is None:
            return {
                "ok": True,
                "bot": _serialize_bot(bot),
                "project": None,
                "active_issue_key": None,
                "queue_depth": 0,
                "queue": [],
            }
        payload = self.control_queue_payload_for_project(project_key=bot.assigned_project_key)
        payload["bot"] = _serialize_bot(bot)
        return payload

    def bot_queue_command(
        self,
        *,
        bot_identity: str,
        command_kind: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bot = self.registry.get_bot(bot_identity)
        if bot is None:
            raise ValueError(f"Unknown bot {bot_identity}.")
        if bot.assigned_project_key is None:
            raise ValueError(f"Bot `{bot_identity}` is not assigned to a project.")
        result = self.control_queue_command(
            project_key=bot.assigned_project_key,
            command_kind=command_kind,
            payload=payload,
        )
        result["bot"] = _serialize_bot(bot)
        return result

    def release_feature_lane(
        self,
        *,
        project_key: str,
        action: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        registration = self._require_project(project_key)
        lane = _project_feature_lane(registration)
        if lane is None:
            return {
                "ok": True,
                "project": _serialize_project(registration),
                "feature_lane": None,
                "released": False,
                "action": action,
            }
        if action == "keep_reserved":
            registration = self.registry.set_project_feature_lane(
                project_key=project_key,
                lane={
                    **lane,
                    "lane_state": "awaiting_hil_release",
                    "release_required": True,
                    "release_action": action,
                    "release_note": note,
                    "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
            return {
                "ok": True,
                "project": _serialize_project(registration),
                "feature_lane": _project_feature_lane(registration),
                "released": False,
                "action": action,
            }
        if action not in {"merge_to_main_and_release", "cherry_pick_and_release", "discard_and_release"}:
            raise ValueError(f"Unsupported HIL release action: {action}")
        registration = self.registry.set_project_feature_lane(project_key=project_key, lane=None)
        registration = self.registry.set_project_reconciliation(project_key=project_key, reconciliation=None)
        released = self.registry.release_project_bot(project_key=project_key)
        project = released.project if released is not None else registration
        return {
            "ok": True,
            "project": _serialize_project(project),
            "feature_lane": None,
            "released": released is not None,
            "action": action,
            "released_bot": None if released is None else _serialize_bot(released.bot),
            "note": note,
        }

    def resume_reviewed_lane(
        self,
        *,
        project_key: str,
        next_slice: str | None = None,
    ) -> dict[str, Any]:
        registration = self._require_project(project_key)
        lane = _project_feature_lane(registration)
        reconciliation = _project_reconciliation(registration)
        if lane is None or lane["lane_state"] != "awaiting_orx_review":
            return {
                "ok": True,
                "project": _serialize_project(registration),
                "feature_lane": lane,
                "resumed": False,
                "reason": "lane_not_waiting_for_review",
            }
        issue_key = lane.get("last_issue_key")
        if not issue_key:
            raise ValueError(f"Project `{project_key}` has no issue bound to the review lane.")
        issue = self.mirror.get_issue(identifier=issue_key)
        if issue is None:
            raise ValueError(f"Mirrored issue `{issue_key}` is missing for project `{project_key}`.")
        runtime = self._runtime_service(registration)
        continuity = runtime.continuity.get_state(issue.identifier, DEFAULT_RUNNER_ID)
        previous_continuity = continuity
        if continuity is None:
            raise ValueError(f"Continuity is missing for `{issue.identifier}`.")
        if continuity.active_slice_id is not None:
            raise ValueError(
                f"Refusing to resume `{issue.identifier}` while slice `{continuity.active_slice_id}` is still active."
            )
        fresh_ui_routing = classify_ui_routing(issue=issue, resume_context={})
        review_resume_updates: dict[str, Any] = {
            "ui_mode": fresh_ui_routing.ui_mode,
            "design_state": fresh_ui_routing.design_state,
            "contract_state": fresh_ui_routing.contract_state,
            "ui_evidence_required": fresh_ui_routing.ui_evidence_required,
        }
        if fresh_ui_routing.design_reference is not None:
            review_resume_updates["design_reference"] = fresh_ui_routing.design_reference
        if fresh_ui_routing.contract_reference is not None:
            review_resume_updates["contract_reference"] = fresh_ui_routing.contract_reference
        if reconciliation is not None and reconciliation.get("review_kind") == "design_review_required":
            review_resume_updates["design_state"] = "approved"
            review_resume_updates["contract_state"] = "pending"
            review_resume_updates["ui_evidence_required"] = False
            design_reference = _normalize_optional_text(reconciliation.get("design_reference"))
            if design_reference is None:
                artifacts = _normalize_text_list(reconciliation.get("design_artifacts"))
                if artifacts:
                    design_reference = artifacts[0]
            if design_reference is not None:
                review_resume_updates["design_reference"] = design_reference
        if reconciliation is not None and reconciliation.get("review_kind") == "contract_review_required":
            review_resume_updates["design_state"] = "approved"
            review_resume_updates["contract_state"] = "approved"
            review_resume_updates["ui_evidence_required"] = True
            contract_reference = _normalize_optional_text(reconciliation.get("contract_reference"))
            if contract_reference is None:
                artifacts = _normalize_text_list(reconciliation.get("contract_artifacts"))
                if artifacts:
                    contract_reference = artifacts[0]
            if contract_reference is not None:
                review_resume_updates["contract_reference"] = contract_reference
        if next_slice is not None and next_slice.strip():
            continuity = runtime.continuity.apply_handoff_interpretation(
                issue_key=issue.identifier,
                runner_id=DEFAULT_RUNNER_ID,
                next_slice=next_slice.strip(),
                resume_context_updates=review_resume_updates,
            )
        elif review_resume_updates:
            continuity = runtime.continuity.apply_handoff_interpretation(
                issue_key=issue.identifier,
                runner_id=DEFAULT_RUNNER_ID,
                next_slice=continuity.next_slice,
                resume_context_updates=review_resume_updates,
            )
        registration = self.registry.set_project_feature_lane(
            project_key=registration.project_key,
            lane=_feature_lane_payload(
                issue=issue,
                lane_state="executing",
                release_required=False,
            ),
        )
        registration = self.registry.set_project_reconciliation(
            project_key=registration.project_key,
            reconciliation=None,
        )
        try:
            dispatched = runtime.dispatch_issue(issue)
            if not self._wait_for_durable_runner_session(
                runtime_service=runtime,
                issue_key=issue.identifier,
            ):
                self._rollback_runtime_dispatch_attempt(
                    runtime_service=runtime,
                    issue_key=issue.identifier,
                    previous_continuity=previous_continuity,
                )
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=lane,
                )
                registration = self.registry.set_project_reconciliation(
                    project_key=registration.project_key,
                    reconciliation=reconciliation,
                )
                return {
                    "ok": True,
                    "project": _serialize_project(registration),
                    "feature_lane": _project_feature_lane(registration),
                    "reconciliation": _project_reconciliation(registration),
                    "resumed": False,
                    "reason": "runner_not_durable",
                }
        except Exception:
            registration = self.registry.set_project_feature_lane(
                project_key=registration.project_key,
                lane=lane,
            )
            registration = self.registry.set_project_reconciliation(
                project_key=registration.project_key,
                reconciliation=reconciliation,
            )
            raise
        return {
            "ok": True,
            "project": _serialize_project(registration),
            "feature_lane": _project_feature_lane(registration),
            "reconciliation": _project_reconciliation(registration),
            "resumed": True,
            "runtime": {
                "project_key": dispatched.project_key,
                "project_display_name": dispatched.project_display_name,
                "runtime_home": dispatched.runtime_home,
                "runner_id": dispatched.runner_id,
                "issue_key": dispatched.issue_key,
                "issue_title": dispatched.issue_title,
                "action": dispatched.action,
                "session_name": dispatched.session_name,
                "pane_target": dispatched.pane_target,
                "queue_depth": dispatched.queue_depth,
                "daemon_tick": dispatched.daemon_tick,
            },
        }

    def recover_failed_start(self, *, project_key: str) -> dict[str, Any]:
        registration = self._require_project(project_key)
        lane = _project_feature_lane(registration)
        runtime = self._runtime_service(registration)
        session = runtime.store.get_session(DEFAULT_RUNNER_ID)
        if session is not None and runtime.executor.transport.has_session(session.session_name):
            raise ValueError(
                f"Refusing failed-start recovery for `{project_key}` while tmux session `{session.session_name}` is alive."
            )
        if lane is not None and lane["lane_state"] != "launch_failed":
            return {
                "ok": True,
                "project": _serialize_project(registration),
                "feature_lane": lane,
                "recovered": False,
            }
        issue_key = None if lane is None else lane.get("last_issue_key")
        if issue_key:
            runtime.repository.release_issue_lease(issue_key, DEFAULT_RUNNER_ID)
        if session is not None:
            runtime.store.clear_session(DEFAULT_RUNNER_ID)
        registration = self.registry.set_project_feature_lane(project_key=project_key, lane=None)
        registration = self.registry.set_project_reconciliation(project_key=project_key, reconciliation=None)
        released = None
        if registration.assigned_bot is not None:
            released = self.registry.release_project_bot(project_key=project_key)
            registration = released.project
        return {
            "ok": True,
            "project": _serialize_project(registration),
            "feature_lane": None,
            "recovered": True,
            "released_bot": None if released is None else _serialize_bot(released.bot),
        }

    def submit_runner_event(
        self,
        *,
        project_key: str,
        event_kind: str,
        issue_key: str | None = None,
        final_summary: str | None = None,
        transcript_excerpt: str | None = None,
        raw_status: str | None = None,
        verification_ran: list[str] | None = None,
        verification_failed: list[str] | None = None,
        artifacts: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        registration = self._require_project(project_key)
        runtime = self._runtime_service(registration)
        active_issue_key, continuity, active_request, _ = self._runtime_recovery_state(runtime)
        resolved_issue_key = issue_key or active_issue_key
        lane = _project_feature_lane(registration)
        if resolved_issue_key is None and lane is not None:
            resolved_issue_key = lane.get("last_issue_key")
        issue = self.mirror.get_issue(identifier=resolved_issue_key) if resolved_issue_key is not None else None
        session = runtime.store.get_session(DEFAULT_RUNNER_ID)
        stale_reason = _stale_runner_event_reason(issue=issue, lane=lane)
        if stale_reason is not None:
            return {
                "ok": True,
                "ignored": True,
                "reason": stale_reason,
                "project": _serialize_project(registration),
                "feature_lane": lane,
                "reconciliation": _project_reconciliation(registration),
                "issue_key": resolved_issue_key,
                "active_issue_key": active_issue_key,
                "has_active_slice": bool(continuity and continuity.active_slice_id),
                "active_slice_id": None if continuity is None else continuity.active_slice_id,
                "active_request_present": active_request is not None,
            }
        submitted_at = datetime.now(UTC).isoformat(timespec="seconds")
        event_status = _runner_event_status(event_kind=event_kind, raw_status=raw_status)
        if continuity is not None and resolved_issue_key is not None:
            active_slice_id = continuity.active_slice_id
            continuity = runtime.continuity.record_runner_terminal_event(
                issue_key=resolved_issue_key,
                runner_id=DEFAULT_RUNNER_ID,
                status=event_status,
                summary=_normalize_optional_text(final_summary)
                or f"Runner emitted `{event_kind}` before ORX received a structured result.",
                submitted_at=submitted_at,
                session_name=None if session is None else session.session_name,
                pane_target=None if session is None else session.pane_target,
                transport=None if session is None else session.transport,
            )
            if active_slice_id is not None:
                runtime.store.delete_slice_request(active_slice_id)
        if resolved_issue_key is not None:
            runtime.repository.release_issue_lease(resolved_issue_key, DEFAULT_RUNNER_ID)
        if session is not None:
            runtime.executor.transport.kill_session(session.session_name)
            runtime.store.clear_session(DEFAULT_RUNNER_ID)
        worktree_evidence = (
            None
            if issue is None
            else _capture_worktree_evidence(
                registration=registration,
                issue=issue,
            )
        )
        interpreted = None
        synthetic_payload = None
        ui_gate_decision = None
        if issue is not None:
            synthetic_payload = _runner_event_slice_payload(
                event_kind=event_kind,
                final_summary=final_summary,
                raw_status=raw_status,
                verification_ran=verification_ran,
                verification_failed=verification_failed,
                artifacts=artifacts,
                reason=reason,
                touched_paths=() if worktree_evidence is None else worktree_evidence.changed_files,
            )
            interpreted = interpret_slice_handoff(
                issue=issue,
                payload=synthetic_payload,
                continuity=continuity,
                transcript_excerpt=transcript_excerpt,
                worktree_status_lines=() if worktree_evidence is None else worktree_evidence.status_lines,
                worktree_diff_excerpt=None if worktree_evidence is None else worktree_evidence.diff_excerpt,
            )
            if continuity is not None:
                continuity = runtime.continuity.apply_handoff_interpretation(
                    issue_key=issue.identifier,
                    runner_id=DEFAULT_RUNNER_ID,
                    next_slice=interpreted.next_slice,
                    blockers=list(interpreted.blockers),
                    discovered_gaps=list(interpreted.discovered_gaps),
                    resume_context_updates=interpreted.resume_context_updates,
                )
            issue = self._update_issue_handoff(
                issue=issue,
                raw_payload=synthetic_payload,
                interpreted_payload=interpreted.payload,
                continuity=continuity,
            )
            self._materialize_follow_ups(
                registration=registration,
                issue=issue,
                payload=interpreted.payload,
            )
            ui_gate_decision = evaluate_ui_gate(
                routing=classify_ui_routing(
                    issue=issue,
                    resume_context=getattr(continuity, "resume_context", {}) if continuity is not None else {},
                ),
                payload=interpreted.payload,
                interpreted_action=interpreted.action,
            )
        registration = self.registry.set_project_reconciliation(
            project_key=registration.project_key,
            reconciliation=_reconciliation_payload(
                status="awaiting_orx_review",
                event_kind=event_kind,
                issue_key=resolved_issue_key,
                action="awaiting_orx_review" if interpreted is None else interpreted.action,
                reason=(
                    interpreted.status_label
                    if interpreted is not None
                    else (
                        reason
                        or f"Runner emitted `{event_kind}` and ORX must reconcile the worktree before continuing."
                    )
                ),
                final_summary=(
                    None
                    if interpreted is None
                    else str(interpreted.payload.get("summary") or "").strip() or None
                )
                or final_summary,
                raw_status=raw_status,
                checkpoint_commit=None,
                transcript_excerpt=transcript_excerpt,
                changed_files=() if worktree_evidence is None else worktree_evidence.changed_files,
                verification_ran=verification_ran,
                verification_failed=verification_failed,
                artifacts=artifacts,
                ui_mode=None if interpreted is None else interpreted.payload.get("ui_mode"),
                design_state=None if ui_gate_decision is None else ui_gate_decision.design_state,
                design_reference=None if ui_gate_decision is None else ui_gate_decision.design_reference,
                review_kind=None if ui_gate_decision is None else ui_gate_decision.review_kind,
                verification_surface=None if ui_gate_decision is None else ui_gate_decision.verification_surface,
                design_artifacts=() if ui_gate_decision is None else ui_gate_decision.design_artifacts,
            ),
        )
        if issue is not None and (lane is None or lane["lane_state"] not in {"launch_failed", "awaiting_hil_release"}):
            registration = self.registry.set_project_feature_lane(
                project_key=registration.project_key,
                lane=_feature_lane_payload(
                    issue=issue,
                    lane_state="awaiting_orx_review",
                    release_required=False,
                    release_action=event_kind if interpreted is None else interpreted.action,
                    release_note=(
                        interpreted.status_label
                        if interpreted is not None
                        else (
                            reason
                            or f"Runner emitted `{event_kind}` and requires ORX reconciliation before resume."
                        )
                    ),
                ),
            )
        return {
            "ok": True,
            "project": _serialize_project(registration),
            "feature_lane": _project_feature_lane(registration),
            "reconciliation": _project_reconciliation(registration),
            "issue_key": resolved_issue_key,
            "active_issue_key": active_issue_key,
            "has_active_slice": bool(continuity and continuity.active_slice_id),
            "active_slice_id": None if continuity is None else continuity.active_slice_id,
            "active_request_present": active_request is not None,
        }

    def sync_bot_name(
        self,
        *,
        bot_identity: str,
        current_display_name: str | None,
        desired_display_name: str | None = None,
        sync_state: str,
        retry_at: str | None = None,
    ) -> dict[str, Any]:
        bot = self.registry.record_bot_name_sync(
            bot_identity=bot_identity,
            current_display_name=current_display_name,
            desired_display_name=desired_display_name,
            sync_state=sync_state,
            retry_at=retry_at,
        )
        return {"ok": bot is not None, "bot": None if bot is None else _serialize_bot(bot)}

    def notifications_payload(
        self,
        *,
        target_bot: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        notifications = self.registry.list_pending_notifications(
            target_bot=target_bot,
            limit=limit,
        )
        return {
            "ok": True,
            "notifications": [_serialize_notification(record) for record in notifications],
        }

    def acknowledge_notifications(self, *, notification_ids: list[int]) -> dict[str, Any]:
        self.registry.acknowledge_notifications(notification_ids)
        return {"ok": True, "acknowledged": notification_ids}

    def dashboard_payload(self) -> dict[str, Any]:
        projects = []
        for registration in self.registry.list_projects():
            runtime = self._runtime_service(registration)
            drift = self.build_project_drift(project_key=registration.project_key)
            entry = runtime.dashboard_entry()
            entry["feature_lane"] = _project_feature_lane(registration)
            entry["reconciliation"] = _project_reconciliation(registration)
            entry["drift"] = drift
            entry["health_state"] = _health_state(entry=entry, drift=drift)
            projects.append(entry)
        return {"ok": True, "projects": projects, "bots": [_serialize_bot(bot) for bot in self.registry.list_bots()]}

    def list_drifted_projects(self) -> list[DriftedProjectSummary]:
        results: list[DriftedProjectSummary] = []
        for registration in self.registry.list_projects():
            drift = self.build_project_drift(project_key=registration.project_key)
            if drift["blockers"]:
                results.append(
                    DriftedProjectSummary(
                        project_key=registration.project_key,
                        blockers=tuple(str(item) for item in drift["blockers"]),
                        warnings=tuple(str(item) for item in drift["warnings"]),
                    )
                )
        return results

    def drain_projects(self) -> list[DrainProjectResult]:
        results: list[DrainProjectResult] = []
        owner_id = f"drain:{uuid.uuid4().hex}"
        self.registry.acquire_dispatch_lease(owner_id)
        try:
            for registration in self.registry.list_projects():
                drift = self.build_project_drift(project_key=registration.project_key)
                if drift["blockers"]:
                    continue
                lane = _project_feature_lane(registration)
                if lane is not None and lane["lane_state"] == "awaiting_orx_review":
                    continue
                runtime = self._runtime_service(registration)
                recovered = runtime.recover_active()
                if recovered is not None:
                    results.append(
                        DrainProjectResult(
                            project_key=registration.project_key,
                            issue_key=recovered.issue_key,
                            action=recovered.action,
                            session_name=recovered.session_name,
                        )
                    )
                    continue
                advanced = runtime.advance()
                if advanced is not None:
                    results.append(
                        DrainProjectResult(
                            project_key=registration.project_key,
                            issue_key=advanced.issue_key,
                            action=advanced.action,
                            session_name=advanced.session_name,
                        )
                    )
                    continue
                if runtime.is_busy():
                    continue
                issue = self._select_issue(
                    explicit_issue_key=None,
                    explicit_project_key=registration.project_key,
                )
                if issue is None:
                    if registration.assigned_bot is not None and _project_feature_lane(registration) is None:
                        self.registry.release_project_bot(project_key=registration.project_key)
                    continue
                assignment = self.registry.assign_project_bot(project_key=registration.project_key)
                if assignment is None:
                    continue
                registration = assignment.project
                try:
                    dispatched = runtime.dispatch_issue(issue)
                except Exception as error:
                    self._record_launch_failure(
                        registration=registration,
                        issue=issue,
                        assignment_action=assignment.action,
                        error=error,
                    )
                    continue
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=_feature_lane_payload(
                        issue=issue,
                        lane_state="executing",
                        release_required=False,
                    ),
                )
                registration = self.registry.set_project_reconciliation(
                    project_key=registration.project_key,
                    reconciliation=None,
                )
                self.registry.set_bot_display_target(
                    bot_identity=registration.owning_bot,
                    desired_display_name=_project_issue_display_name(
                        project_key=registration.project_key,
                        issue_title=issue.title,
                    ),
                    assignment_id=assignment.bot.assignment_id,
                )
                results.append(
                    DrainProjectResult(
                        project_key=registration.project_key,
                        issue_key=issue.identifier,
                        action=dispatched.action,
                        session_name=dispatched.session_name,
                    )
                )
            return results
        finally:
            self.registry.release_dispatch_lease(owner_id)

    def submit_slice_result(
        self,
        *,
        project_key: str,
        slice_id: str,
        payload: dict[str, Any],
    ) -> DispatchSliceResult:
        registration = self._require_project(project_key)
        runtime = self._runtime_service(registration)
        active_issue_key, continuity, active_request, _ = self._runtime_recovery_state(runtime)
        issue = self.mirror.get_issue(identifier=active_issue_key) if active_issue_key is not None else None
        packet = _build_execution_packet(
            registration=registration,
            issue=issue,
            continuity=continuity,
            active_request=active_request,
        )
        gate = _build_slice_apply_gate(
            active_issue_key=active_issue_key,
            continuity=continuity,
            execution_packet=packet,
        )
        result = runtime.submit_slice_result(
            slice_id=slice_id,
            payload=payload,
            gate=gate,
        )
        linear_completed = False
        issue = self.mirror.get_issue(identifier=result.issue_key)
        continuity = runtime.continuity.get_state(result.issue_key, DEFAULT_RUNNER_ID)
        interpreted = None
        ui_gate_decision = None
        worktree_evidence = (
            None
            if issue is None
            else _capture_worktree_evidence(
                registration=registration,
                issue=issue,
            )
        )
        if result.apply_status == "applied" and issue is not None:
            interpreted = interpret_slice_handoff(
                issue=issue,
                payload=payload,
                continuity=continuity,
                transcript_excerpt=_normalize_optional_text(payload.get("transcript_excerpt")),
                worktree_status_lines=() if worktree_evidence is None else worktree_evidence.status_lines,
                worktree_diff_excerpt=None if worktree_evidence is None else worktree_evidence.diff_excerpt,
            )
            continuity = runtime.continuity.apply_handoff_interpretation(
                issue_key=result.issue_key,
                runner_id=DEFAULT_RUNNER_ID,
                next_slice=interpreted.next_slice,
                blockers=list(interpreted.blockers),
                discovered_gaps=list(interpreted.discovered_gaps),
                resume_context_updates=interpreted.resume_context_updates,
            )
            issue = self._update_issue_handoff(
                issue=issue,
                raw_payload=payload,
                interpreted_payload=interpreted.payload,
                continuity=continuity,
            )
            self._materialize_follow_ups(
                registration=registration,
                issue=issue,
                payload=interpreted.payload,
            )
            ui_gate_decision = evaluate_ui_gate(
                routing=classify_ui_routing(
                    issue=issue,
                    resume_context=getattr(continuity, "resume_context", {}) if continuity is not None else {},
                ),
                payload=interpreted.payload,
                interpreted_action=interpreted.action,
            )
            registration = self.registry.set_project_reconciliation(
                project_key=registration.project_key,
                reconciliation=_reconciliation_payload(
                    status="awaiting_orx_review" if ui_gate_decision.gate_required else "reconciled",
                    event_kind="slice_result",
                    issue_key=result.issue_key,
                    action=interpreted.action,
                    reason=ui_gate_decision.reason if ui_gate_decision.gate_required else interpreted.status_label,
                    final_summary=str(interpreted.payload.get("summary") or payload.get("summary") or "").strip() or None,
                    raw_status=result.status,
                    checkpoint_commit=None,
                    transcript_excerpt=_normalize_optional_text(payload.get("transcript_excerpt")),
                    changed_files=() if worktree_evidence is None else worktree_evidence.changed_files,
                    verification_ran=_normalize_text_list(payload.get("verification_ran")),
                    verification_failed=_normalize_text_list(payload.get("verification_failed")),
                    artifacts=_normalize_text_list(payload.get("artifacts")),
                    ui_mode=interpreted.payload.get("ui_mode"),
                    design_state=ui_gate_decision.design_state,
                    contract_state=ui_gate_decision.contract_state,
                    design_reference=ui_gate_decision.design_reference,
                    contract_reference=ui_gate_decision.contract_reference,
                    review_kind=ui_gate_decision.review_kind,
                    verification_surface=ui_gate_decision.verification_surface,
                    design_artifacts=list(ui_gate_decision.design_artifacts),
                    contract_artifacts=list(ui_gate_decision.contract_artifacts),
                ),
            )
            if interpreted.action in {"blocked", "reroute", "replan", "needs_human_help"} or ui_gate_decision.gate_required:
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=_feature_lane_payload(
                        issue=issue,
                        lane_state="awaiting_orx_review",
                        release_required=False,
                        release_action=ui_gate_decision.review_kind if ui_gate_decision.gate_required else interpreted.action,
                        release_note=ui_gate_decision.reason if ui_gate_decision.gate_required else interpreted.status_label,
                    ),
                )
        elif issue is not None:
            registration = self.registry.set_project_reconciliation(
                project_key=registration.project_key,
                reconciliation=_reconciliation_payload(
                    status=result.apply_status,
                    event_kind="slice_result",
                    issue_key=result.issue_key,
                    action=result.apply_status,
                    reason=result.stale_reason,
                    final_summary=_normalize_optional_text(payload.get("summary")),
                    raw_status=result.status,
                    checkpoint_commit=None,
                    transcript_excerpt=_normalize_optional_text(payload.get("transcript_excerpt")),
                    changed_files=() if worktree_evidence is None else worktree_evidence.changed_files,
                    verification_ran=_normalize_text_list(payload.get("verification_ran")),
                    verification_failed=_normalize_text_list(payload.get("verification_failed")),
                    artifacts=_normalize_text_list(payload.get("artifacts")),
                ),
            )
        if result.finalized and issue is not None:
            checkpoint_commit = None
            checkpoint_error = None
            if ui_gate_decision is not None and ui_gate_decision.gate_required:
                checkpoint_error = ui_gate_decision.reason
            elif interpreted is not None and interpreted.action == "complete":
                checkpoint_commit, checkpoint_error = _create_checkpoint_commit(
                    issue=issue,
                    evidence=worktree_evidence,
                )
            elif interpreted is not None:
                checkpoint_error = (
                    f"Refusing checkpoint completion for `{issue.identifier}` because ORX reconciliation "
                    f"returned `{interpreted.action}` instead of `complete`."
                )
            if checkpoint_error:
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=_feature_lane_payload(
                        issue=issue,
                        lane_state="awaiting_orx_review",
                        release_required=False,
                        release_action="checkpoint_blocked",
                        release_note=checkpoint_error,
                    ),
                )
                registration = self.registry.set_project_reconciliation(
                    project_key=registration.project_key,
                    reconciliation=_reconciliation_payload(
                        status="awaiting_orx_review",
                        event_kind="slice_result",
                        issue_key=result.issue_key,
                        action="awaiting_orx_review",
                        reason=checkpoint_error,
                        final_summary=_normalize_optional_text(payload.get("summary")),
                        raw_status=result.status,
                        checkpoint_commit=checkpoint_commit,
                        transcript_excerpt=_normalize_optional_text(payload.get("transcript_excerpt")),
                        changed_files=() if worktree_evidence is None else worktree_evidence.changed_files,
                        verification_ran=_normalize_text_list(payload.get("verification_ran")),
                        verification_failed=_normalize_text_list(payload.get("verification_failed")),
                        artifacts=_normalize_text_list(payload.get("artifacts")),
                        ui_mode=None if interpreted is None else interpreted.payload.get("ui_mode"),
                        design_state=None if ui_gate_decision is None else ui_gate_decision.design_state,
                        design_reference=None if ui_gate_decision is None else ui_gate_decision.design_reference,
                        review_kind=None if ui_gate_decision is None else ui_gate_decision.review_kind,
                        verification_surface=None if ui_gate_decision is None else ui_gate_decision.verification_surface,
                        design_artifacts=() if ui_gate_decision is None else ui_gate_decision.design_artifacts,
                    ),
                )
                return DispatchSliceResult(
                    project_key=registration.project_key,
                    issue_key=result.issue_key,
                    status="awaiting_orx_review",
                    verified=result.verified,
                    next_slice=None,
                    finalized=False,
                    linear_completed=False,
                    session_name=result.session_name,
                    pane_target=result.pane_target,
                    apply_status=result.apply_status,
                    stale_reason=checkpoint_error,
                )
            completed = self._mark_issue_completed(
                issue=issue,
                completed_at=result.submitted_at,
            )
            linear_completed = completed.completed_at is not None
            feature_key = _feature_key_from_issue(completed)
            pending_same_feature = self._next_feature_issue(
                project_key=registration.project_key,
                feature_key=feature_key,
                exclude_issue_key=completed.identifier,
            )
            if pending_same_feature is None:
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=_feature_lane_payload(
                        issue=completed,
                        lane_state="awaiting_hil_release",
                        release_required=True,
                    ),
                )
            else:
                registration = self.registry.set_project_feature_lane(
                    project_key=registration.project_key,
                    lane=_feature_lane_payload(
                        issue=pending_same_feature,
                        lane_state="executing",
                        release_required=False,
                    ),
                )
            registration = self.registry.set_project_reconciliation(
                project_key=registration.project_key,
                reconciliation=_reconciliation_payload(
                    status="checkpointed",
                    event_kind="slice_result",
                    issue_key=completed.identifier,
                    action="complete",
                    reason="Ticket acceptance satisfied and ORX checkpointed the feature worktree.",
                    final_summary=_normalize_optional_text(payload.get("summary")),
                    raw_status=result.status,
                    checkpoint_commit=checkpoint_commit,
                    transcript_excerpt=_normalize_optional_text(payload.get("transcript_excerpt")),
                    changed_files=() if worktree_evidence is None else worktree_evidence.changed_files,
                    verification_ran=_normalize_text_list(payload.get("verification_ran")),
                    verification_failed=_normalize_text_list(payload.get("verification_failed")),
                    artifacts=_normalize_text_list(payload.get("artifacts")),
                    ui_mode=None if interpreted is None else interpreted.payload.get("ui_mode"),
                    design_state=None if ui_gate_decision is None else ui_gate_decision.design_state,
                    design_reference=None if ui_gate_decision is None else ui_gate_decision.design_reference,
                    review_kind=None if ui_gate_decision is None else ui_gate_decision.review_kind,
                    verification_surface=None if ui_gate_decision is None else ui_gate_decision.verification_surface,
                    design_artifacts=() if ui_gate_decision is None else ui_gate_decision.design_artifacts,
                ),
            )
        next_slice = interpreted.next_slice if interpreted is not None else result.next_slice
        return DispatchSliceResult(
            project_key=registration.project_key,
            issue_key=result.issue_key,
            status=(
                "awaiting_orx_review"
                if ui_gate_decision is not None and ui_gate_decision.gate_required
                else (
                    interpreted.action
                    if interpreted is not None
                    else (result.status if result.apply_status == "applied" else result.apply_status)
                )
            ),
            verified=result.verified,
            next_slice=None if ui_gate_decision is not None and ui_gate_decision.gate_required else next_slice,
            finalized=False if ui_gate_decision is not None and ui_gate_decision.gate_required else result.finalized,
            linear_completed=linear_completed,
            session_name=result.session_name,
            pane_target=result.pane_target,
            apply_status=result.apply_status,
            stale_reason=result.stale_reason,
        )

    def _update_issue_handoff(
        self,
        *,
        issue: MirroredIssueRecord,
        raw_payload: dict[str, Any],
        interpreted_payload: dict[str, Any],
        continuity: Any,
    ) -> MirroredIssueRecord:
        continuity_summary = None
        if continuity is not None:
            next_slice = str(getattr(continuity, "next_slice", "") or "").strip()
            continuity_summary = next_slice or None
        updated_description = replace_raw_slice_facts(
            issue.description,
            build_raw_slice_facts_section(
                issue_key=issue.identifier,
                payload=raw_payload,
            ),
        )
        updated_description = replace_latest_handoff(
            updated_description,
            build_latest_handoff_section(
                issue_key=issue.identifier,
                payload=interpreted_payload,
                continuity_summary=continuity_summary,
            ),
        )
        if updated_description == issue.description:
            return issue
        updated_issue = None
        if self.linear_client is not None and hasattr(self.linear_client, "update_issue"):
            updated_issue = self.linear_client.update_issue(
                issue_ref=issue.identifier,
                description=updated_description,
            )
        if updated_issue is None:
            updated_issue = issue
        return self.mirror.upsert_issue(
            linear_id=issue.linear_id,
            identifier=issue.identifier,
            title=getattr(updated_issue, "title", issue.title),
            description=updated_description,
            team_id=issue.team_id,
            team_name=issue.team_name,
            state_id=getattr(updated_issue, "state_id", None) or issue.state_id,
            state_name=getattr(updated_issue, "state_name", None) or issue.state_name,
            state_type=getattr(updated_issue, "state_type", None) or issue.state_type,
            priority=issue.priority,
            project_id=getattr(updated_issue, "project_id", None) or issue.project_id,
            project_name=getattr(updated_issue, "project_name", None) or issue.project_name,
            parent_linear_id=getattr(updated_issue, "parent_id", None) or issue.parent_linear_id,
            parent_identifier=getattr(updated_issue, "parent_identifier", None) or issue.parent_identifier,
            assignee_id=issue.assignee_id,
            assignee_name=issue.assignee_name,
            labels=issue.labels,
            metadata=issue.metadata,
            source_updated_at=issue.source_updated_at,
            created_at=issue.created_at,
            completed_at=issue.completed_at,
            canceled_at=issue.canceled_at,
        )

    def _materialize_follow_ups(
        self,
        *,
        registration: ProjectRegistration,
        issue: MirroredIssueRecord,
        payload: dict[str, Any],
    ) -> None:
        if self.linear_client is None:
            return
        raw_follow_ups = payload.get("follow_ups")
        if not isinstance(raw_follow_ups, list):
            return
        seen_titles: set[str] = set()
        for raw in raw_follow_ups:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            dedupe_key = _follow_up_dedupe_key(current_issue=issue, follow_up=raw)
            existing = self._find_existing_follow_up(issue=issue, title=title, dedupe_key=dedupe_key)
            if existing is not None:
                continue
            if not self._reserve_follow_up_operation(
                issue=issue,
                registration=registration,
                dedupe_key=dedupe_key,
                title=title,
            ):
                continue
            try:
                created = self.linear_client.create_issue(
                    team_id=issue.team_id,
                    title=title,
                    description=_follow_up_description(current_issue=issue, follow_up=raw),
                    parent_id=issue.linear_id,
                    project_id=issue.project_id,
                )
            except Exception:
                self._release_follow_up_operation(dedupe_key=dedupe_key)
                raise
            self.mirror.upsert_issue(
                linear_id=created.linear_id,
                identifier=created.identifier,
                title=created.title,
                description=created.description,
                team_id=created.team_id or issue.team_id,
                team_name=created.team_name or issue.team_name,
                state_id=created.state_id,
                state_name=created.state_name,
                state_type=created.state_type,
                priority=issue.priority,
                project_id=created.project_id or issue.project_id,
                project_name=created.project_name or issue.project_name,
                parent_linear_id=created.parent_id or issue.linear_id,
                parent_identifier=created.parent_identifier or issue.identifier,
                assignee_id=issue.assignee_id,
                assignee_name=issue.assignee_name,
                labels=issue.labels,
                metadata={
                    **issue.metadata,
                    "follow_up_origin": issue.identifier,
                    "follow_up_title_key": _normalize_follow_up_title(title),
                    "follow_up_dedupe_key": dedupe_key,
                    "follow_up_class": str(raw.get("follow_up_class") or "").strip() or None,
                    "follow_up_relationship": str(raw.get("relationship") or "").strip() or "parent_child",
                    "project_key": registration.project_key,
                },
                source_updated_at=issue.source_updated_at,
                created_at=issue.created_at,
                completed_at=None,
                canceled_at=None,
            )
            self._record_follow_up_issue_key(
                dedupe_key=dedupe_key,
                issue_key=created.identifier,
            )

    def _reserve_follow_up_operation(
        self,
        *,
        issue: MirroredIssueRecord,
        registration: ProjectRegistration,
        dedupe_key: str,
        title: str,
    ) -> bool:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.storage.session() as connection:
            existing = connection.execute(
                """
                SELECT follow_up_issue_key FROM follow_up_operations
                WHERE dedupe_key = ?
                """,
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE follow_up_operations
                    SET updated_at = ?
                    WHERE dedupe_key = ?
                    """,
                    (now, dedupe_key),
                )
                return False
            try:
                connection.execute(
                    """
                    INSERT INTO follow_up_operations(
                        dedupe_key,
                        origin_issue_key,
                        project_key,
                        follow_up_title,
                        follow_up_issue_key,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (dedupe_key, issue.identifier, registration.project_key, title, now, now),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def _record_follow_up_issue_key(self, *, dedupe_key: str, issue_key: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.storage.session() as connection:
            connection.execute(
                """
                UPDATE follow_up_operations
                SET follow_up_issue_key = ?, updated_at = ?
                WHERE dedupe_key = ?
                """,
                (issue_key, now, dedupe_key),
            )

    def _release_follow_up_operation(self, *, dedupe_key: str) -> None:
        with self.storage.session() as connection:
            connection.execute(
                "DELETE FROM follow_up_operations WHERE dedupe_key = ?",
                (dedupe_key,),
            )

    def _find_existing_follow_up(
        self,
        *,
        issue: MirroredIssueRecord,
        title: str,
        dedupe_key: str | None = None,
    ) -> MirroredIssueRecord | None:
        title_key = _normalize_follow_up_title(title)
        for child in self.mirror.list_child_issues(issue):
            child_metadata = child.metadata if isinstance(child.metadata, dict) else {}
            if str(child_metadata.get("follow_up_origin") or "").strip() != issue.identifier:
                continue
            child_dedupe_key = str(child_metadata.get("follow_up_dedupe_key") or "").strip()
            if dedupe_key and child_dedupe_key and child_dedupe_key == dedupe_key:
                return child
            child_title_key = str(child_metadata.get("follow_up_title_key") or "").strip()
            if child_title_key:
                if child_title_key == title_key:
                    return child
                continue
            if _normalize_follow_up_title(child.title) == title_key:
                return child
        return None

    def _select_issue(
        self,
        *,
        explicit_issue_key: str | None,
        explicit_project_key: str | None,
    ) -> MirroredIssueRecord | None:
        registered = {record.project_key for record in self.registry.list_projects()}
        if not registered:
            return None
        if explicit_issue_key:
            issue = self.mirror.get_issue(identifier=explicit_issue_key)
            if issue is None:
                return None
            if explicit_project_key and _issue_project_key(issue) != normalize_project_key(explicit_project_key):
                return None
            registration = self.registry.get_project(_issue_project_key(issue))
            if registration is None:
                return issue
            if not _lane_allows_issue(registration=registration, issue=issue):
                return None
            return issue

        if explicit_project_key:
            registration = self.registry.get_project(explicit_project_key)
            if registration is not None:
                runtime = self._runtime_service(registration)
                active_issue_key = runtime.active_issue_key()
                if active_issue_key is not None:
                    active_issue = self.mirror.get_issue(identifier=active_issue_key)
                    if active_issue is not None:
                        return active_issue
                reserved_feature_key = _lane_feature_key(registration)
                lane_state = _lane_state(registration)
                if reserved_feature_key and lane_state in {"executing", "awaiting_hil_release"}:
                    for ranked in self.ranking.rank_issues():
                        issue = ranked.issue
                        if _issue_project_key(issue) != registration.project_key:
                            continue
                        if _feature_key_from_issue(issue) != reserved_feature_key:
                            continue
                        if ranked.sort_key[0] == 0:
                            return issue

        requested_project = (
            normalize_project_key(explicit_project_key) if explicit_project_key else None
        )
        for ranked in self.ranking.rank_issues():
            issue = ranked.issue
            project_key = _issue_project_key(issue)
            if project_key not in registered:
                continue
            if requested_project is not None and project_key != requested_project:
                continue
            registration = self.registry.get_project(project_key)
            if registration is None:
                continue
            if self._runtime_service(registration).is_busy():
                continue
            if not _lane_allows_issue(registration=registration, issue=issue):
                continue
            if ranked.sort_key[0] == 0:
                return issue
        return None

    def _require_project(self, project_key: str) -> ProjectRegistration:
        registration = self.registry.get_project(project_key)
        if registration is None:
            raise ValueError(f"Unknown project {project_key}.")
        return registration

    def _runtime_service(self, registration: ProjectRegistration) -> ProjectRuntimeService:
        transport = self.transport_factory() if self.transport_factory is not None else None
        return ProjectRuntimeService(registration=registration, transport=transport)

    def _mark_issue_completed(
        self,
        *,
        issue: MirroredIssueRecord,
        completed_at: str,
    ) -> MirroredIssueRecord:
        if self.linear_client is not None:
            completed = self.linear_client.complete_issue(
                issue_ref=issue.identifier,
                team_id=issue.team_id,
            )
            return self.mirror.upsert_issue(
                linear_id=issue.linear_id,
                identifier=issue.identifier,
                title=completed.title,
                description=completed.description,
                team_id=issue.team_id,
                team_name=issue.team_name,
                state_id=completed.state_id,
                state_name=completed.state_name or "Done",
                state_type=completed.state_type or "completed",
                priority=issue.priority,
                project_id=completed.project_id or issue.project_id,
                project_name=completed.project_name or issue.project_name,
                parent_linear_id=completed.parent_id or issue.parent_linear_id,
                parent_identifier=completed.parent_identifier or issue.parent_identifier,
                assignee_id=issue.assignee_id,
                assignee_name=issue.assignee_name,
                labels=issue.labels,
                metadata=issue.metadata,
                source_updated_at=completed_at,
                created_at=issue.created_at,
                completed_at=completed_at,
                canceled_at=issue.canceled_at,
            )
        return self.mirror.mark_issue_completed(issue, completed_at=completed_at)

    def _next_feature_issue(
        self,
        *,
        project_key: str,
        feature_key: str,
        exclude_issue_key: str | None = None,
    ) -> MirroredIssueRecord | None:
        for ranked in self.ranking.rank_issues():
            issue = ranked.issue
            if ranked.sort_key[0] != 0:
                continue
            if _issue_project_key(issue) != project_key:
                continue
            if exclude_issue_key and issue.identifier == exclude_issue_key:
                continue
            if _feature_key_from_issue(issue) != feature_key:
                continue
            return issue
        return None

    def _runtime_recovery_state(
        self,
        runtime: ProjectRuntimeService,
    ) -> tuple[str | None, Any, Any, Any]:
        session = runtime.store.get_session(DEFAULT_RUNNER_ID)
        active_issue_key = runtime.active_issue_key()
        continuity = (
            runtime.continuity.get_state(active_issue_key, DEFAULT_RUNNER_ID)
            if active_issue_key is not None
            else None
        )
        if (
            session is not None
            and active_issue_key is None
            and continuity is None
            and not runtime.executor.transport.has_session(session.session_name)
        ):
            runtime.store.clear_session(DEFAULT_RUNNER_ID)
            session = None
        if (
            active_issue_key is None
            and session is not None
            and session.state != "idle"
            and runtime.executor.transport.has_session(session.session_name)
        ):
            active_issue_key = session.issue_key
            continuity = runtime.continuity.get_state(active_issue_key, DEFAULT_RUNNER_ID)
        if active_issue_key is None and continuity is None:
            candidates = runtime.continuity.list_recovery_candidates()
            if candidates:
                continuity = next(
                    (record for record in candidates if record.runner_id == DEFAULT_RUNNER_ID),
                    candidates[0],
                )
                active_issue_key = continuity.issue_key
        active_request = (
            runtime.store.get_slice_request(continuity.active_slice_id)
            if continuity is not None and continuity.active_slice_id is not None
            else None
        )
        return active_issue_key, continuity, active_request, session

    def _wait_for_durable_runner_session(
        self,
        *,
        runtime_service: ProjectRuntimeService,
        issue_key: str,
        timeout_seconds: float = 3.0,
        stable_seconds: float = 0.3,
    ) -> bool:
        deadline = monotonic() + timeout_seconds
        stable_started_at: float | None = None
        stable_session_name: str | None = None
        while monotonic() <= deadline:
            continuity = runtime_service.continuity.get_state(issue_key, DEFAULT_RUNNER_ID)
            active_issue_key = runtime_service.active_issue_key()
            if continuity is None or continuity.active_slice_id is None or active_issue_key != issue_key:
                stable_started_at = None
                stable_session_name = None
            else:
                effective = runtime_service.effective_session(
                    active_issue_key=active_issue_key,
                    continuity=continuity,
                    prune_stale=False,
                )
                if effective is not None and effective.issue_key == issue_key:
                    session_name = effective.session_name
                    if stable_session_name != session_name:
                        stable_session_name = session_name
                        stable_started_at = monotonic()
                    elif stable_started_at is not None and monotonic() - stable_started_at >= stable_seconds:
                        return True
                else:
                    stable_started_at = None
                    stable_session_name = None
            sleep(0.05)
        return False

    def _rollback_runtime_dispatch_attempt(
        self,
        *,
        runtime_service: ProjectRuntimeService,
        issue_key: str,
        previous_continuity: Any,
    ) -> None:
        current = runtime_service.continuity.get_state(issue_key, DEFAULT_RUNNER_ID)
        if current is not None and current.active_slice_id is not None:
            runtime_service.store.delete_slice_request(current.active_slice_id)
        session = runtime_service.store.get_session(DEFAULT_RUNNER_ID)
        if session is not None:
            runtime_service.executor.transport.kill_session(session.session_name)
            runtime_service.store.clear_session(DEFAULT_RUNNER_ID)
        runtime_service.repository.release_issue_lease(issue_key, DEFAULT_RUNNER_ID)
        if previous_continuity is None:
            runtime_service.continuity.clear_state(issue_key, DEFAULT_RUNNER_ID)
        else:
            runtime_service.continuity.restore_state(previous_continuity)

    def _build_drift_report(
        self,
        *,
        registration: ProjectRegistration,
        runtime: ProjectRuntimeService,
        active_issue_key: str | None,
        issue: MirroredIssueRecord | None,
        continuity: Any,
        active_request: Any,
        session: Any,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        blockers: list[str] = []
        checks: dict[str, Any] = {}

        expected_runtime_home = str(
            resolve_project_runtime_paths(registration.project_key, home=self.storage.paths.home).home
        )
        checks["repo_root_exists"] = Path(registration.repo_root).exists()
        if not checks["repo_root_exists"]:
            warnings.append(f"Registered repo_root does not exist on disk: {registration.repo_root}")

        checks["runtime_home_matches"] = registration.runtime_home == expected_runtime_home
        checks["expected_runtime_home"] = expected_runtime_home
        if not checks["runtime_home_matches"]:
            blockers.append(
                "Registered runtime_home does not match the deterministic ORX project runtime path."
            )

        checks["assigned_bot_present"] = registration.assigned_bot is not None
        checks["owner_chat_bound"] = registration.owner_chat_id is not None
        checks["control_thread_bound"] = registration.owner_thread_id is not None
        checks["execution_thread_bound"] = _project_execution_thread_id(registration) is not None
        if registration.assigned_bot is not None:
            bot = self.registry.get_bot(registration.assigned_bot)
            checks["assigned_bot_registered"] = bot is not None
            if bot is None:
                blockers.append(
                    f"Assigned bot `{registration.assigned_bot}` is missing from the bot registry."
                )
            if not checks["owner_chat_bound"]:
                warnings.append("Assigned bot chat binding is missing for the active project bot.")
            if not checks["control_thread_bound"]:
                warnings.append("Assigned bot control thread binding is missing for the active project bot.")
            if not checks["execution_thread_bound"]:
                warnings.append("Assigned project execution thread binding is missing for the active project bot.")

        checks["active_issue_mirrored"] = active_issue_key is None or issue is not None
        if active_issue_key is not None and issue is None:
            blockers.append(f"Active issue {active_issue_key} is missing from the Linear mirror.")

        checks["mirror_project_matches"] = True
        if issue is not None:
            issue_project_key = _issue_project_key(issue)
            checks["mirror_project_key"] = issue_project_key
            checks["mirror_project_matches"] = issue_project_key == registration.project_key
            if not checks["mirror_project_matches"]:
                blockers.append(
                    f"Mirrored active issue {issue.identifier} is assigned to `{issue_project_key}` instead of `{registration.project_key}`."
                )

        expected_session_name = f"runner-{registration.project_key}"
        checks["session_namespace_matches"] = True
        if session is not None:
            checks["session_name"] = session.session_name
            session_exists = runtime.executor.transport.has_session(session.session_name)
            checks["session_exists"] = session_exists
            if not checks["session_exists"]:
                recoverable = continuity is not None and continuity.active_slice_id is not None
                checks["session_recoverable"] = recoverable
                message = (
                    f"Active session `{session.session_name}` is missing from the shared tmux transport."
                )
                if recoverable:
                    warnings.append(f"{message} ORX can recreate it from continuity.")
                else:
                    blockers.append(message)
            else:
                checks["session_recoverable"] = False
            checks["session_namespace_matches"] = session.session_name == expected_session_name
            if not checks["session_namespace_matches"]:
                blockers.append(
                    f"Active session `{session.session_name}` does not match the expected runner session `{expected_session_name}`."
                )
            checks["session_issue_matches"] = active_issue_key is None or session.issue_key == active_issue_key
            if not checks["session_issue_matches"]:
                blockers.append(
                    f"Active session issue `{session.issue_key}` does not match runtime active issue `{active_issue_key}`."
                )
        else:
            checks["session_name"] = None
            checks["session_exists"] = False
            checks["session_recoverable"] = continuity is not None and continuity.active_slice_id is not None
            if active_issue_key is not None and checks["session_recoverable"]:
                warnings.append(
                    f"Active issue `{active_issue_key}` has no tmux session; ORX will recreate it from continuity."
                )
            checks["session_issue_matches"] = active_issue_key is None

        checks["continuity_present_for_active_issue"] = active_issue_key is None or continuity is not None
        if active_issue_key is not None and continuity is None:
            blockers.append(f"Continuity state is missing for active issue {active_issue_key}.")

        checks["resume_context_project_matches"] = True
        checks["resume_context_project_key"] = None
        if continuity is not None:
            resume_project_key = continuity.resume_context.get("project_key")
            checks["resume_context_project_key"] = resume_project_key
            if active_issue_key is not None and not isinstance(resume_project_key, str):
                warnings.append("Continuity resume_context is missing project_key.")
                checks["resume_context_project_matches"] = False
            elif isinstance(resume_project_key, str):
                checks["resume_context_project_matches"] = (
                    normalize_project_key(resume_project_key) == registration.project_key
                )
                if not checks["resume_context_project_matches"]:
                    blockers.append(
                        f"Continuity resume_context project_key `{resume_project_key}` does not match `{registration.project_key}`."
                    )

            checks["active_slice_request_present"] = continuity.active_slice_id is None or active_request is not None
            if continuity.active_slice_id is not None and active_request is None:
                blockers.append(
                    f"Continuity references active slice {continuity.active_slice_id}, but no matching slice request exists."
                )
        else:
            checks["active_slice_request_present"] = active_request is not None

        checks["slice_request_project_matches"] = True
        checks["slice_request_project_key"] = None
        if active_request is not None:
            request_project_key = active_request.request.get("context", {}).get("project_key")
            checks["slice_request_project_key"] = request_project_key
            if not isinstance(request_project_key, str):
                warnings.append("Active slice request context is missing project_key.")
                checks["slice_request_project_matches"] = False
            else:
                checks["slice_request_project_matches"] = (
                    normalize_project_key(request_project_key) == registration.project_key
                )
                if not checks["slice_request_project_matches"]:
                    blockers.append(
                        f"Active slice request project_key `{request_project_key}` does not match `{registration.project_key}`."
                    )

        return {
            "ok": len(blockers) == 0,
            "warnings": warnings,
            "blockers": blockers,
            "checks": checks,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }


def _serialize_project(project: ProjectRegistration) -> dict[str, Any]:
    return {
        "project_key": project.project_key,
        "display_name": project.display_name,
        "repo_root": project.repo_root,
        "runtime_home": project.runtime_home,
        "owning_bot": project.owning_bot,
        "assigned_bot": project.assigned_bot,
        "owner_chat_id": project.owner_chat_id,
        "owner_thread_id": project.owner_thread_id,
        "execution_thread_id": _project_execution_thread_id(project),
        "feature_lane": _project_feature_lane(project),
        "reconciliation": _project_reconciliation(project),
        "metadata": project.metadata,
    }


def _serialize_bot(bot: BotRegistration) -> dict[str, Any]:
    return {
        "bot_identity": bot.bot_identity,
        "telegram_chat_id": bot.telegram_chat_id,
        "telegram_thread_id": bot.telegram_thread_id,
        "default_display_name": bot.default_display_name,
        "current_display_name": bot.current_display_name,
        "desired_display_name": bot.desired_display_name,
        "name_sync_state": bot.name_sync_state,
        "name_sync_retry_at": bot.name_sync_retry_at,
        "availability_state": bot.availability_state,
        "assigned_project_key": bot.assigned_project_key,
        "assignment_id": bot.assignment_id,
        "metadata": bot.metadata,
        "last_heartbeat_at": bot.last_heartbeat_at,
    }


def _project_execution_thread_id(project: ProjectRegistration) -> int | None:
    raw = project.metadata.get("execution_thread_id") if isinstance(project.metadata, dict) else None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return project.owner_thread_id
    return project.owner_thread_id


def _serialize_issue(issue: MirroredIssueRecord) -> dict[str, Any]:
    return {
        "linear_id": issue.linear_id,
        "identifier": issue.identifier,
        "title": issue.title,
        "description": issue.description,
        "team_id": issue.team_id,
        "team_name": issue.team_name,
        "state_id": issue.state_id,
        "state_name": issue.state_name,
        "state_type": issue.state_type,
        "priority": issue.priority,
        "project_id": issue.project_id,
        "project_name": issue.project_name,
        "parent_linear_id": issue.parent_linear_id,
        "parent_identifier": issue.parent_identifier,
        "assignee_id": issue.assignee_id,
        "assignee_name": issue.assignee_name,
        "labels": list(issue.labels),
        "metadata": issue.metadata,
        "source_updated_at": issue.source_updated_at,
        "created_at": issue.created_at,
        "completed_at": issue.completed_at,
        "canceled_at": issue.canceled_at,
        "last_synced_at": issue.last_synced_at,
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


def _serialize_slice_request(record: Any) -> dict[str, Any]:
    return {
        "slice_id": record.slice_id,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "command_id": record.command_id,
        "session_name": record.session_name,
        "request": record.request,
        "dispatched_at": record.dispatched_at,
        "status": record.status,
    }


def _serialize_session(record: Any) -> dict[str, Any]:
    return {
        "issue_key": record.issue_key,
        "session_name": record.session_name,
        "pane_target": record.pane_target,
        "transport": record.transport,
        "heartbeat_at": record.heartbeat_at,
        "last_result_at": record.last_result_at,
        "state": record.state,
        "metadata": record.metadata,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _serialize_recovery(record: Any) -> dict[str, Any]:
    return {
        "action": record.action,
        "reason": record.reason,
        "issue_key": record.issue_key,
        "runner_id": record.runner_id,
        "active_slice_id": record.active_slice_id,
        "next_slice": record.next_slice,
        "proposal_key": record.proposal_key,
    }


def _override_recovery_for_drift(
    *,
    recovery: Any,
    drift: dict[str, Any],
    project_key: str,
) -> RecoverySummary:
    blockers = drift.get("blockers") or []
    if not blockers:
        return RecoverySummary(
            action=recovery.action,
            reason=recovery.reason,
            issue_key=recovery.issue_key,
            runner_id=recovery.runner_id,
            active_slice_id=recovery.active_slice_id,
            next_slice=recovery.next_slice,
            proposal_key=recovery.proposal_key,
        )
    return RecoverySummary(
        action="drift",
        reason=(
            f"Project `{project_key}` has drift blockers and should not resume automatically: "
            + "; ".join(str(item) for item in blockers)
        ),
        issue_key=recovery.issue_key,
        runner_id=recovery.runner_id,
        active_slice_id=recovery.active_slice_id,
        next_slice=recovery.next_slice,
        proposal_key=recovery.proposal_key,
    )


def _health_state(*, entry: dict[str, Any], drift: dict[str, Any]) -> str:
    if drift.get("blockers"):
        return "drift-blocked"
    feature_lane = entry.get("feature_lane")
    if isinstance(feature_lane, dict):
        lane_state = str(feature_lane.get("lane_state") or "").strip()
        if lane_state == "launch_failed":
            return "launch-failed"
        if lane_state == "awaiting_hil_release":
            return "awaiting-hil-release"
        if lane_state == "awaiting_orx_review":
            return "awaiting-orx-review"
    if entry.get("busy"):
        return "busy"
    return "idle"


def _serialize_notification(notification: DispatchNotification) -> dict[str, Any]:
    return {
        "notification_id": notification.notification_id,
        "project_key": notification.project_key,
        "owning_bot": notification.target_bot,
        "target_bot": notification.target_bot,
        "assignment_id": notification.assignment_id,
        "ingress_bot": notification.ingress_bot,
        "ingress_chat_id": notification.ingress_chat_id,
        "ingress_thread_id": notification.ingress_thread_id,
        "issue_key": notification.issue_key,
        "kind": notification.kind,
        "payload": notification.payload,
        "created_at": notification.created_at,
        "delivered_at": notification.delivered_at,
    }


def _build_execution_packet(
    *,
    registration: ProjectRegistration,
    issue: MirroredIssueRecord | None,
    continuity: Any,
    active_request: Any,
) -> dict[str, Any] | None:
    if issue is None:
        return None
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    repo_root = str(metadata.get("repo_root") or registration.repo_root).strip()
    worktree_path = str(
        metadata.get("worktree_path")
        or metadata.get("worktree")
        or _default_issue_worktree_path(
            project_key=registration.project_key,
            issue_key=issue.identifier,
        )
        or repo_root
    ).strip()
    branch = str(metadata.get("branch") or _default_issue_branch(issue.identifier) or "").strip() or None
    active_slice_id = None
    if continuity is not None:
        active_slice_id = getattr(continuity, "active_slice_id", None)
    if active_slice_id is None and active_request is not None:
        active_slice_id = getattr(active_request, "slice_id", None)
    latest_handoff = extract_latest_handoff(issue.description)
    resume_context = getattr(continuity, "resume_context", {}) if continuity is not None else {}
    if not isinstance(resume_context, dict):
        resume_context = {}
    ui_routing = classify_ui_routing(issue=issue, resume_context=resume_context)
    execution_brief = metadata.get("codex_execution_brief")
    if not isinstance(execution_brief, dict):
        execution_brief = _fallback_execution_brief(
            issue=issue,
            continuity=continuity,
            active_request=active_request,
            latest_handoff=latest_handoff,
            verification_commands=metadata.get("verification_commands"),
        )
    return {
        "project_key": registration.project_key,
        "project_display_name": registration.display_name,
        "owning_bot": registration.owning_bot,
        "assigned_bot": registration.assigned_bot,
        "feature_lane": _project_feature_lane(registration),
        "feature_key": _feature_key_from_issue(issue),
        "packet_key": str(metadata.get("packet_key") or issue.identifier).strip(),
        "packet_scope": str(metadata.get("scope") or metadata.get("packet_scope") or "single_leaf").strip(),
        "packet_revision": _packet_revision(
            registration=registration,
            issue=issue,
            repo_root=repo_root,
            worktree_path=worktree_path,
            branch=branch,
        ),
        "issue_key": issue.identifier,
        "issue_title": issue.title,
        "issue_url": str(metadata.get("linear_url") or metadata.get("url") or "").strip() or None,
        "repo_root": repo_root,
        "worktree_path": worktree_path,
        "branch": branch,
        "merge_target": str(metadata.get("merge_into") or "main").strip(),
        "merge_strategy": str(metadata.get("merge_strategy") or "hil_merge_to_main").strip(),
        "execution_model": str(
            resume_context.get("execution_model")
            or metadata.get("codex_execution_model")
            or "gpt-5.4"
        ).strip(),
        "execution_reasoning_effort": str(
            resume_context.get("execution_reasoning_effort")
            or metadata.get("codex_execution_reasoning_effort")
            or "medium"
        ).strip(),
        "execution_reasoning_source": str(
            resume_context.get("execution_reasoning_source") or "ticket_default"
        ).strip(),
        "execution_reasoning_reason": str(
            resume_context.get("execution_reasoning_reason")
            or "Using the ticket-default execution tier for a runnable leaf."
        ).strip(),
        "execution_escalation_trigger": str(
            resume_context.get("execution_escalation_trigger") or ""
        ).strip()
        or None,
        "execution_brief": execution_brief,
        "ui_mode": ui_routing.ui_mode,
        "ui_reason": ui_routing.ui_reason,
        "design_state": ui_routing.design_state,
        "contract_state": ui_routing.contract_state,
        "ui_evidence_required": ui_routing.ui_evidence_required,
        "design_reference": ui_routing.design_reference,
        "contract_reference": ui_routing.contract_reference,
        "latest_handoff": latest_handoff,
        "latest_handoff_revision": latest_handoff_revision(issue.description),
        "continuity_revision": None if continuity is None else getattr(continuity, "updated_at", None),
        "decision_epoch": str(
            resume_context.get("decision_epoch")
            or active_slice_id
            or ""
        ).strip()
        or None,
        "interpreted_action": str(resume_context.get("interpreted_action") or "").strip() or None,
        "interpreted_status": str(resume_context.get("interpreted_status") or "").strip() or None,
        "interpreted_next_direction": str(
            resume_context.get("interpreted_next_direction") or ""
        ).strip()
        or None,
        "active_slice_id": active_slice_id,
        "verification_commands": metadata.get("verification_commands") or [],
}


def _fallback_execution_brief(
    *,
    issue: MirroredIssueRecord,
    continuity: Any,
    active_request: Any,
    latest_handoff: str | None,
    verification_commands: Any,
) -> dict[str, Any] | None:
    bounded_goal = ""
    if continuity is not None:
        bounded_goal = str(
            getattr(continuity, "next_slice", None)
            or getattr(continuity, "slice_goal", None)
            or getattr(continuity, "objective", None)
            or ""
        ).strip()
    if not bounded_goal and active_request is not None:
        request_payload = getattr(active_request, "request", None)
        if isinstance(request_payload, dict):
            bounded_goal = str(
                request_payload.get("slice_goal")
                or request_payload.get("objective")
                or ""
            ).strip()
    if not bounded_goal:
        bounded_goal = issue.title.strip()
    if not bounded_goal:
        return None

    success_criteria = []
    verification = []
    if continuity is not None:
        success_criteria = _normalize_text_list(getattr(continuity, "acceptance", ()))
        verification = _normalize_text_list(getattr(continuity, "validation_plan", ()))
    if active_request is not None:
        request_payload = getattr(active_request, "request", None)
        if isinstance(request_payload, dict):
            if not success_criteria:
                success_criteria = _normalize_text_list(request_payload.get("acceptance"))
            if not verification:
                verification = _normalize_text_list(request_payload.get("validation_plan"))
    if not success_criteria:
        first_line = _normalize_optional_text(issue.description.strip().splitlines()[0] if issue.description.strip() else "")
        if first_line is not None:
            success_criteria = [first_line]

    constraints = ["Stay within the active ORX slice and current packet worktree."]
    if latest_handoff:
        constraints.append("Use the latest ORX handoff as the resume surface for blocker context.")
    if not verification:
        verification = [
            str(command).strip()
            for command in (verification_commands or [])
            if str(command).strip()
        ]
    return {
        "objective_title": issue.title,
        "goal": bounded_goal,
        "scope_in": [bounded_goal],
        "success_criteria": success_criteria or [f"Advance `{issue.identifier}` without broadening beyond the active ORX slice."],
        "constraints": constraints,
        "ordered_steps": [bounded_goal],
        "verification": verification or None,
    }


def _build_slice_apply_gate(
    *,
    active_issue_key: str | None,
    continuity: Any,
    execution_packet: dict[str, Any] | None,
) -> SliceApplyGate | None:
    if active_issue_key is None or continuity is None:
        return None
    return SliceApplyGate(
        expected_issue_key=active_issue_key,
        expected_active_slice_id=str(getattr(continuity, "active_slice_id", "") or "").strip(),
        expected_packet_key=None
        if not isinstance(execution_packet, dict)
        else str(execution_packet.get("packet_key") or "").strip() or None,
        expected_packet_revision=None
        if not isinstance(execution_packet, dict)
        else str(execution_packet.get("packet_revision") or "").strip() or None,
        expected_latest_handoff_revision=None
        if not isinstance(execution_packet, dict)
        else str(execution_packet.get("latest_handoff_revision") or "").strip() or None,
        expected_continuity_revision=None
        if not isinstance(execution_packet, dict)
        else str(execution_packet.get("continuity_revision") or "").strip() or None,
        expected_decision_epoch=None
        if not isinstance(execution_packet, dict)
        else str(execution_packet.get("decision_epoch") or "").strip() or None,
    )


def _packet_revision(
    *,
    registration: ProjectRegistration,
    issue: MirroredIssueRecord,
    repo_root: str,
    worktree_path: str,
    branch: str | None,
) -> str:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    payload = {
        "project_key": registration.project_key,
        "issue_key": issue.identifier,
        "packet_key": str(metadata.get("packet_key") or issue.identifier).strip(),
        "packet_scope": str(metadata.get("scope") or metadata.get("packet_scope") or "single_leaf").strip(),
        "repo_root": repo_root,
        "worktree_path": worktree_path,
        "branch": branch or "",
        "merge_target": str(metadata.get("merge_into") or "main").strip(),
        "merge_strategy": str(metadata.get("merge_strategy") or "hil_merge_to_main").strip(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _issue_project_key(issue: MirroredIssueRecord) -> str:
    metadata_key = issue.metadata.get("project_key")
    if isinstance(metadata_key, str) and metadata_key.strip():
        return normalize_project_key(metadata_key)
    if issue.project_name:
        return normalize_project_key(issue.project_name)
    if issue.project_id:
        return normalize_project_key(issue.project_id)
    return normalize_project_key(issue.team_name or "default")


def _feature_key_from_issue(issue: MirroredIssueRecord) -> str:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    return str(metadata.get("packet_key") or issue.identifier).strip()


def _default_issue_worktree_path(*, project_key: str, issue_key: str) -> str:
    dev_root = Path(os.environ.get("DEV", str(Path.home() / "Dev"))).expanduser().resolve()
    return str(dev_root / "worktrees" / project_key / issue_key.lower())


def _default_issue_branch(issue_key: str) -> str:
    slug = re.sub(r"[^a-z0-9._/-]+", "-", issue_key.strip().lower()).strip("-")
    return f"linear/{slug or 'issue'}"


def _feature_lane_payload(
    *,
    issue: MirroredIssueRecord,
    lane_state: str,
    release_required: bool,
    release_action: str | None = None,
    release_note: str | None = None,
) -> dict[str, Any]:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    return {
        "feature_key": _feature_key_from_issue(issue),
        "packet_key": str(metadata.get("packet_key") or issue.identifier).strip(),
        "lane_state": lane_state,
        "release_required": release_required,
        "last_issue_key": issue.identifier,
        "last_issue_title": issue.title,
        "merge_target": str(metadata.get("merge_into") or "main").strip(),
        "merge_strategy": str(metadata.get("merge_strategy") or "hil_merge_to_main").strip(),
        "release_action": release_action,
        "release_note": release_note,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _stale_runner_event_reason(
    *,
    issue: MirroredIssueRecord | None,
    lane: dict[str, Any] | None,
) -> str | None:
    if issue is not None and issue.completed_at is not None:
        return "feature_already_completed"
    if lane is not None and lane.get("lane_state") == "awaiting_hil_release":
        return "feature_waiting_for_hil_release"
    return None


def _project_feature_lane(project: ProjectRegistration) -> dict[str, Any] | None:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    lane = metadata.get("feature_lane")
    if not isinstance(lane, dict):
        return None
    feature_key = str(lane.get("feature_key") or "").strip()
    if not feature_key:
        return None
    return {
        "feature_key": feature_key,
        "packet_key": str(lane.get("packet_key") or feature_key).strip() or feature_key,
        "lane_state": str(lane.get("lane_state") or "idle").strip() or "idle",
        "release_required": bool(lane.get("release_required")),
        "last_issue_key": str(lane.get("last_issue_key") or "").strip() or None,
        "last_issue_title": str(lane.get("last_issue_title") or "").strip() or None,
        "merge_target": str(lane.get("merge_target") or "main").strip() or "main",
        "merge_strategy": str(lane.get("merge_strategy") or "hil_merge_to_main").strip() or "hil_merge_to_main",
        "release_action": str(lane.get("release_action") or "").strip() or None,
        "release_note": str(lane.get("release_note") or "").strip() or None,
        "updated_at": str(lane.get("updated_at") or "").strip() or None,
    }


def _project_reconciliation(project: ProjectRegistration) -> dict[str, Any] | None:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    record = metadata.get("reconciliation")
    if not isinstance(record, dict):
        return None
    return _reconciliation_payload(
        status=_normalize_optional_text(record.get("status")),
        event_kind=_normalize_optional_text(record.get("event_kind")),
        issue_key=_normalize_optional_text(record.get("issue_key")),
        action=_normalize_optional_text(record.get("action")),
        reason=_normalize_optional_text(record.get("reason")),
        final_summary=_normalize_optional_text(record.get("final_summary")),
        raw_status=_normalize_optional_text(record.get("raw_status")),
        checkpoint_commit=_normalize_optional_text(record.get("checkpoint_commit")),
        transcript_excerpt=_normalize_optional_text(record.get("transcript_excerpt")),
        changed_files=_normalize_text_list(record.get("changed_files")),
        verification_ran=_normalize_text_list(record.get("verification_ran")),
        verification_failed=_normalize_text_list(record.get("verification_failed")),
        artifacts=_normalize_text_list(record.get("artifacts")),
        ui_mode=_normalize_optional_text(record.get("ui_mode")),
        design_state=_normalize_optional_text(record.get("design_state")),
        design_reference=_normalize_optional_text(record.get("design_reference")),
        review_kind=_normalize_optional_text(record.get("review_kind")),
        verification_surface=_normalize_optional_text(record.get("verification_surface")),
        design_artifacts=_normalize_text_list(record.get("design_artifacts")),
        checkpoint_created=bool(record.get("checkpoint_created")),
        updated_at=_normalize_optional_text(record.get("updated_at")),
    )


def _reconciliation_payload(
    *,
    status: str | None,
    event_kind: str | None,
    issue_key: str | None,
    action: str | None,
    reason: str | None,
    final_summary: str | None,
    raw_status: str | None,
    checkpoint_commit: str | None,
    transcript_excerpt: str | None,
    changed_files: list[str] | tuple[str, ...],
    verification_ran: list[str] | tuple[str, ...] | None,
    verification_failed: list[str] | tuple[str, ...] | None,
    artifacts: list[str] | tuple[str, ...] | None,
    ui_mode: str | None = None,
    design_state: str | None = None,
    contract_state: str | None = None,
    design_reference: str | None = None,
    contract_reference: str | None = None,
    review_kind: str | None = None,
    verification_surface: str | None = None,
    design_artifacts: list[str] | tuple[str, ...] | None = None,
    contract_artifacts: list[str] | tuple[str, ...] | None = None,
    checkpoint_created: bool | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    normalized = {
        "status": _normalize_optional_text(status),
        "event_kind": _normalize_optional_text(event_kind),
        "issue_key": _normalize_optional_text(issue_key),
        "action": _normalize_optional_text(action),
        "reason": _normalize_optional_text(reason),
        "final_summary": _normalize_optional_text(final_summary),
        "raw_status": _normalize_optional_text(raw_status),
        "checkpoint_commit": _normalize_optional_text(checkpoint_commit),
        "checkpoint_created": bool(checkpoint_created or checkpoint_commit),
        "transcript_excerpt": _normalize_optional_text(transcript_excerpt),
        "changed_files": _normalize_text_list(changed_files),
        "verification_ran": _normalize_text_list(verification_ran),
        "verification_failed": _normalize_text_list(verification_failed),
        "artifacts": _normalize_text_list(artifacts),
        "ui_mode": _normalize_optional_text(ui_mode),
        "design_state": _normalize_optional_text(design_state),
        "contract_state": _normalize_optional_text(contract_state),
        "design_reference": _normalize_optional_text(design_reference),
        "contract_reference": _normalize_optional_text(contract_reference),
        "review_kind": _normalize_optional_text(review_kind),
        "verification_surface": _normalize_optional_text(verification_surface),
        "design_artifacts": _normalize_text_list(design_artifacts),
        "contract_artifacts": _normalize_text_list(contract_artifacts),
        "updated_at": _normalize_optional_text(updated_at) or datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if not any(
        (
            normalized["status"],
            normalized["event_kind"],
            normalized["issue_key"],
            normalized["action"],
            normalized["reason"],
            normalized["final_summary"],
            normalized["raw_status"],
            normalized["checkpoint_commit"],
            normalized["transcript_excerpt"],
            normalized["changed_files"],
            normalized["verification_ran"],
            normalized["verification_failed"],
            normalized["artifacts"],
            normalized["ui_mode"],
            normalized["design_state"],
            normalized["contract_state"],
            normalized["design_reference"],
            normalized["contract_reference"],
            normalized["review_kind"],
            normalized["verification_surface"],
            normalized["design_artifacts"],
            normalized["contract_artifacts"],
        )
    ):
        return None
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _runner_event_slice_payload(
    *,
    event_kind: str,
    final_summary: str | None,
    raw_status: str | None,
    verification_ran: list[str] | None,
    verification_failed: list[str] | None,
    artifacts: list[str] | None,
    reason: str | None,
    touched_paths: tuple[str, ...],
) -> dict[str, Any]:
    normalized_reason = _normalize_optional_text(reason)
    return {
        "status": _runner_event_status(event_kind=event_kind, raw_status=raw_status),
        "summary": _normalize_optional_text(final_summary)
        or f"Runner emitted `{event_kind}` before ORX received a structured result.",
        "verified": False,
        "verification_ran": _normalize_text_list(verification_ran),
        "verification_failed": _normalize_text_list(verification_failed),
        "artifacts": _normalize_text_list(artifacts),
        "touched_paths": list(touched_paths),
        "lessons": [] if normalized_reason is None else [normalized_reason],
    }


def _runner_event_status(*, event_kind: str, raw_status: str | None) -> str:
    normalized = _normalize_optional_text(raw_status)
    if normalized:
        return normalized
    mapping = {
        "result_missing": "failed",
        "result_submit_failed": "success",
        "runner_lost": "interrupted",
        "stalled": "blocked",
        "interrupted": "interrupted",
    }
    return mapping.get(event_kind, "failed")


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in (_normalize_optional_text(entry) for entry in value) if item]


def _capture_worktree_evidence(
    *,
    registration: ProjectRegistration,
    issue: MirroredIssueRecord,
) -> WorktreeEvidence | None:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    worktree_path = str(
        metadata.get("worktree_path")
        or metadata.get("worktree")
        or _default_issue_worktree_path(
            project_key=registration.project_key,
            issue_key=issue.identifier,
        )
        or registration.repo_root
    ).strip()
    if not worktree_path:
        return None
    worktree = Path(worktree_path).expanduser().resolve()
    if not worktree.exists():
        return None
    status_result = _run_git(worktree, "status", "--short")
    if status_result is None:
        return None
    status_lines = tuple(line.strip() for line in status_result.stdout.splitlines() if line.strip())
    changed_files = tuple(_status_line_path(line) for line in status_lines if _status_line_path(line))
    git_head = _git_stdout(worktree, "rev-parse", "HEAD")
    branch = (
        _normalize_optional_text(metadata.get("branch"))
        or _git_stdout(worktree, "branch", "--show-current")
        or _default_issue_branch(issue.identifier)
    )
    diff_chunks: list[str] = []
    for changed in changed_files[:12]:
        diff = _git_diff_excerpt(worktree, changed)
        if diff:
            diff_chunks.append(f"--- {changed} ---\n{diff}")
    return WorktreeEvidence(
        worktree_path=str(worktree),
        branch=branch,
        git_head=git_head,
        changed_files=changed_files,
        status_lines=status_lines,
        diff_excerpt="\n\n".join(diff_chunks).strip(),
    )


def _status_line_path(line: str) -> str | None:
    text = str(line or "").strip()
    if not text:
        return None
    if len(text) > 3:
        return text[3:].strip() or None
    return None


def _git_stdout(worktree: Path, *args: str) -> str | None:
    result = _run_git(worktree, *args)
    if result is None or result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _git_diff_excerpt(worktree: Path, changed_path: str) -> str:
    result = _run_git(worktree, "diff", "--", changed_path)
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()[:4000]
    absolute_path = (worktree / changed_path).resolve()
    if absolute_path.exists() and absolute_path.is_file():
        try:
            return absolute_path.read_text(encoding="utf-8")[:2000]
        except UnicodeDecodeError:
            return "<binary or non-utf8 file>"
    return ""


def _run_git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None


def _create_checkpoint_commit(
    *,
    issue: MirroredIssueRecord,
    evidence: WorktreeEvidence | None,
) -> tuple[str | None, str | None]:
    if evidence is None:
        return None, None
    if not evidence.changed_files:
        return evidence.git_head, None
    add_result = _run_git(Path(evidence.worktree_path), "add", "-A")
    if add_result is None or add_result.returncode != 0:
        detail = "" if add_result is None else (add_result.stderr.strip() or add_result.stdout.strip())
        if "not a git repository" in detail.lower():
            return None, None
        return None, detail or f"git add -A failed in {evidence.worktree_path}"
    message = f"orx checkpoint: {issue.identifier} {issue.title}".strip()
    commit_result = _run_git(
        Path(evidence.worktree_path),
        "-c",
        "user.name=ORX",
        "-c",
        "user.email=orx@example.com",
        "commit",
        "-m",
        message,
    )
    if commit_result is None:
        return None, f"git commit failed in {evidence.worktree_path}"
    if commit_result.returncode != 0:
        detail = commit_result.stderr.strip() or commit_result.stdout.strip()
        if "nothing to commit" in detail.lower():
            return evidence.git_head, None
        if "not a git repository" in detail.lower():
            return None, None
        return None, detail or f"git commit failed in {evidence.worktree_path}"
    checkpoint_commit = _git_stdout(Path(evidence.worktree_path), "rev-parse", "HEAD")
    if checkpoint_commit is None:
        return None, f"git rev-parse HEAD failed after checkpoint commit in {evidence.worktree_path}"
    return checkpoint_commit, None


def _lane_state(project: ProjectRegistration) -> str | None:
    lane = _project_feature_lane(project)
    return None if lane is None else str(lane["lane_state"])


def _lane_feature_key(project: ProjectRegistration) -> str | None:
    lane = _project_feature_lane(project)
    return None if lane is None else str(lane["feature_key"])


def _lane_release_required(project: ProjectRegistration) -> bool:
    lane = _project_feature_lane(project)
    return bool(lane and lane.get("release_required"))


def _lane_allows_issue(
    *,
    registration: ProjectRegistration,
    issue: MirroredIssueRecord,
) -> bool:
    lane = _project_feature_lane(registration)
    if lane is None:
        return True
    lane_state = str(lane["lane_state"])
    if lane_state == "launch_failed":
        return False
    if lane_state == "awaiting_orx_review":
        return False
    if lane_state in {"executing", "awaiting_hil_release"}:
        return lane["feature_key"] == _feature_key_from_issue(issue)
    return True


def _normalize_follow_up_title(title: str) -> str:
    return " ".join(str(title or "").lower().split()).strip()


def _follow_up_dedupe_key(*, current_issue: MirroredIssueRecord, follow_up: dict[str, Any]) -> str:
    relationship = str(follow_up.get("relationship") or "parent_child").strip().lower()
    follow_up_class = str(follow_up.get("follow_up_class") or "generic").strip().lower()
    title_key = _normalize_follow_up_title(str(follow_up.get("title") or ""))
    return "::".join(
        part for part in (current_issue.identifier, relationship, follow_up_class, title_key) if part
    )


def _ingress_message(
    *,
    issue: MirroredIssueRecord,
    registration: ProjectRegistration,
    runtime: RuntimeDispatchResult,
    handoff_required: bool,
    assignment_action: str | None,
) -> str:
    if handoff_required:
        return (
            f"Handed off {issue.identifier} to `{registration.owning_bot}` "
            f"for project `{registration.project_key}` ({assignment_action or runtime.action})."
        )
    return _owner_message(issue, runtime)


def _owner_message(issue: MirroredIssueRecord, runtime: RuntimeDispatchResult) -> str:
    return (
        f"{runtime.action.title()} `{issue.identifier}`: {issue.title}\n"
        f"Session: `{runtime.session_name or 'pending'}`"
    )


def _active_run_message(
    *,
    registration: ProjectRegistration,
    issue: MirroredIssueRecord,
    session_name: str | None,
    handoff_required: bool,
) -> str:
    session = session_name or "pending"
    if handoff_required:
        return (
            f"Work is already running: `{issue.identifier}` is active on `{registration.project_key}` "
            f"via `{registration.owning_bot}`.\nSession: `{session}`"
        )
    return (
        f"Already running `{issue.identifier}` on `{registration.project_key}`.\n"
        f"Session: `{session}`"
    )


def _active_runs_summary(
    active_runs: list[tuple[ProjectRegistration, MirroredIssueRecord, str | None]]
) -> str:
    lines = ["No new runnable Linear work is available.", "Currently running:"]
    for registration, issue, session_name in active_runs:
        bot = registration.assigned_bot or registration.owning_bot or "unassigned"
        session = session_name or "pending"
        lines.append(
            f"- `{registration.project_key}` `{issue.identifier}` via `{bot}` (session `{session}`)"
        )
    return "\n".join(lines)


def _project_issue_display_name(*, project_key: str, issue_title: str) -> str:
    normalized = " ".join(
        word
        for word in issue_title.replace("`", " ").replace(":", " ").split()
        if not word.upper().startswith("PRO-")
    )
    summary_words = normalized.split()[:5]
    summary = " ".join(summary_words) if summary_words else "active work"
    display = f"{project_key} - {summary}"
    return display[:64].rstrip()


def _follow_up_description(*, current_issue: MirroredIssueRecord, follow_up: dict[str, Any]) -> str:
    relationship = str(follow_up.get("relationship") or "parent_child").strip() or "parent_child"
    follow_up_class = str(follow_up.get("follow_up_class") or "generic").strip() or "generic"
    acceptance = [
        str(item).strip()
        for item in (follow_up.get("acceptance") or [])
        if str(item).strip()
    ]
    if not acceptance:
        acceptance = [f"Resolve the follow-up cleanly and update `{current_issue.identifier}` with the result."]
    lines = [
        "## Objective",
        str(follow_up.get("goal") or f"Resolve the follow-up discovered while executing {current_issue.identifier}.").strip(),
        "",
        "## Success Criteria",
        *(f"- {item}" for item in acceptance),
        "",
        "## Why",
        str(follow_up.get("why") or f"Follow-up discovered while executing {current_issue.identifier}.").strip(),
        "",
        "## Scope",
        "### In scope",
    ]
    scope_in = follow_up.get("scope_in")
    if isinstance(scope_in, list) and any(str(item).strip() for item in scope_in):
        lines.extend(f"- {str(item).strip()}" for item in scope_in if str(item).strip())
    lines.extend(
        [
            "",
            "## Ordered Steps",
            f"1. Read the origin handoff and current blocker from `{current_issue.identifier}`.",
            "2. Confirm the missing prerequisite, reroute, or split boundary before changing code.",
            "3. Land the smallest change that resolves the follow-up without widening scope.",
            "4. Verify the declared success criteria and capture evidence back into the ticket handoff.",
            "",
            "## Verification",
            "- Run the narrowest verification surface that proves the blocker or dependency is resolved.",
            "- Fail closed if the fix requires broader architecture work than this follow-up allows.",
            "",
            "## Stopping Conditions",
            "- Stop if the required owner or repo is still unclear.",
            "- Stop if resolving this follow-up would require a second independent ticket.",
            "",
            "## Blocked / Escalation",
            "1. Do not keep retrying the origin ticket while this follow-up remains unresolved.",
            "2. Escalate back through ORX if the owning repo, dependency order, or verification surface is still ambiguous.",
            "",
            "## Dependencies / Risks",
            f"- Origin ticket: {current_issue.identifier}",
            f"- Relationship: {relationship}",
            f"- Follow-up class: {follow_up_class}",
        ]
    )
    return "\n".join(lines).strip() + "\n"
