"""tmux-backed executor contract and session management for ORX."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .continuity import ContinuityService
from .ownership import OwnershipService
from .repository import OrxRepository
from .storage import Storage
from .tmux_client import TmuxClient


SLICE_RESULT_STATUS = {"success", "blocked", "failed"}
PLACEHOLDER_SLICE_SUMMARIES = {"...", "…", "tbd", "todo", "n/a", "placeholder"}


class SliceResultValidationError(ValueError):
    """Raised when executor slice results do not satisfy the ORX contract."""


@dataclass(frozen=True)
class ExecutorSessionRecord:
    runner_id: str
    issue_key: str
    session_name: str
    pane_target: str
    transport: str
    heartbeat_at: str | None
    last_result_at: str | None
    state: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SliceRequestRecord:
    slice_id: str
    issue_key: str
    runner_id: str
    command_id: int | None
    session_name: str
    request: dict[str, Any]
    dispatched_at: str
    status: str


@dataclass(frozen=True)
class SliceResultRecord:
    result_id: int
    slice_id: str
    runner_id: str
    issue_key: str
    status: str
    summary: str
    verified: bool
    next_slice: str | None
    artifacts: tuple[str, ...]
    metrics: dict[str, Any]
    submitted_at: str
    apply_status: str
    payload_hash: str | None
    stale_reason: str | None


@dataclass(frozen=True)
class SliceApplyGate:
    expected_issue_key: str
    expected_active_slice_id: str
    expected_packet_key: str | None = None
    expected_packet_revision: str | None = None
    expected_latest_handoff_revision: str | None = None
    expected_continuity_revision: str | None = None
    expected_decision_epoch: str | None = None


@dataclass(frozen=True)
class ValidatedSliceResult:
    status: str
    summary: str
    verified: bool
    next_slice: str | None
    artifacts: tuple[str, ...]
    metrics: dict[str, Any]


class TmuxTransport(Protocol):
    def has_session(self, name: str) -> bool: ...
    def create_session(self, name: str, cmd: str) -> str: ...
    def kill_session(self, name: str) -> bool: ...
    def send_keys(self, session: str, text: str, *, enter: bool = True) -> bool: ...
    def capture_pane(self, session: str, *, lines: int = 50) -> str: ...
    def list_panes(self, session: str) -> list[str]: ...


class RunnerSessionLauncher(Protocol):
    def ensure_session(
        self,
        *,
        project_key: str,
        repo_root: str | None,
        runner_id: str,
    ) -> tuple[str, str]: ...


class LocalRunnerSessionLauncher:
    def __init__(self, *, transport: TmuxTransport) -> None:
        self.transport = transport

    def ensure_session(
        self,
        *,
        project_key: str,
        repo_root: str | None,
        runner_id: str,
    ) -> tuple[str, str]:
        session_name = _runner_session_name(project_key or runner_id)
        if self.transport.has_session(session_name):
            return session_name, _pane_target(self.transport, session_name)

        if not isinstance(self.transport, TmuxClient):
            pane_target = self.transport.create_session(
                session_name,
                _runner_launch_command(project_key=project_key, repo_root=repo_root),
            )
            return session_name, pane_target

        repo_home = _tmux_codex_home()
        if not repo_home.exists():
            raise RuntimeError(f"tmux-codex repo home is missing: {repo_home}")

        target_root = repo_root.strip() if isinstance(repo_root, str) and repo_root.strip() else str(repo_home)
        env = os.environ.copy()
        dev_root = str(Path(os.environ.get("DEV", str(Path.home() / "Dev"))).expanduser().resolve())
        env["DEV"] = dev_root
        env["PYTHONPATH"] = str(repo_home) + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.main",
                "loop-bg",
                project_key,
                "--project-root",
                target_root,
            ],
            cwd=str(repo_home),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "unknown error").strip()
            raise RuntimeError(f"Failed to start tmux-codex runner for {project_key}: {error}")

        for _ in range(30):
            if self.transport.has_session(session_name):
                return session_name, _pane_target(self.transport, session_name)
            time.sleep(0.1)
        raise RuntimeError(
            f"tmux-codex reported success starting {session_name}, but the tmux session never appeared."
        )


class ExecutorStore:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_session(
        self,
        *,
        runner_id: str,
        issue_key: str,
        session_name: str,
        pane_target: str,
        transport: str,
        heartbeat_at: str | None = None,
        last_result_at: str | None = None,
        state: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutorSessionRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO executor_sessions(
                    runner_id,
                    issue_key,
                    session_name,
                    pane_target,
                    transport,
                    heartbeat_at,
                    last_result_at,
                    state,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(runner_id) DO UPDATE SET
                    issue_key = excluded.issue_key,
                    session_name = excluded.session_name,
                    pane_target = excluded.pane_target,
                    transport = excluded.transport,
                    heartbeat_at = excluded.heartbeat_at,
                    last_result_at = excluded.last_result_at,
                    state = excluded.state,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    runner_id,
                    issue_key,
                    session_name,
                    pane_target,
                    transport,
                    heartbeat_at,
                    last_result_at,
                    state,
                    json.dumps(metadata or {}, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM executor_sessions WHERE runner_id = ?",
                (runner_id,),
            ).fetchone()
        assert row is not None
        return _row_to_session(row)

    def get_session(self, runner_id: str) -> ExecutorSessionRecord | None:
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM executor_sessions WHERE runner_id = ?",
                (runner_id,),
            ).fetchone()
        return _row_to_session(row) if row is not None else None

    def clear_session(self, runner_id: str) -> None:
        with self.storage.session() as connection:
            connection.execute(
                "DELETE FROM executor_sessions WHERE runner_id = ?",
                (runner_id,),
            )

    def create_slice_request(
        self,
        *,
        issue_key: str,
        runner_id: str,
        session_name: str,
        request: dict[str, Any],
        command_id: int | None = None,
    ) -> SliceRequestRecord:
        slice_id = request.get("slice_id") or uuid.uuid4().hex
        dispatched_at = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO slice_requests(
                    slice_id,
                    issue_key,
                    runner_id,
                    command_id,
                    session_name,
                    request_json,
                    dispatched_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'dispatched')
                """,
                (
                    slice_id,
                    issue_key,
                    runner_id,
                    command_id,
                    session_name,
                    json.dumps({**request, "slice_id": slice_id}, sort_keys=True),
                    dispatched_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM slice_requests WHERE slice_id = ?",
                (slice_id,),
            ).fetchone()
        assert row is not None
        return _row_to_slice_request(row)

    def get_slice_request(self, slice_id: str) -> SliceRequestRecord | None:
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM slice_requests WHERE slice_id = ?",
                (slice_id,),
            ).fetchone()
        return _row_to_slice_request(row) if row is not None else None

    def delete_slice_request(self, slice_id: str) -> None:
        with self.storage.session() as connection:
            connection.execute(
                "DELETE FROM slice_requests WHERE slice_id = ?",
                (slice_id,),
            )

    def store_slice_result(
        self,
        *,
        slice_id: str,
        runner_id: str,
        issue_key: str,
        result: ValidatedSliceResult,
        apply_status: str,
        payload_hash: str | None = None,
        stale_reason: str | None = None,
    ) -> SliceResultRecord:
        submitted_at = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO slice_results(
                    slice_id,
                    runner_id,
                    issue_key,
                    status,
                    summary,
                    verified,
                    next_slice,
                    artifacts_json,
                    metrics_json,
                    submitted_at,
                    apply_status,
                    payload_hash,
                    stale_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slice_id,
                    runner_id,
                    issue_key,
                    result.status,
                    result.summary,
                    1 if result.verified else 0,
                    result.next_slice,
                    json.dumps(list(result.artifacts)),
                    json.dumps(result.metrics, sort_keys=True),
                    submitted_at,
                    apply_status,
                    payload_hash,
                    stale_reason,
                ),
            )
            connection.execute(
                """
                UPDATE slice_requests
                SET status = ?
                WHERE slice_id = ?
                """,
                (result.status, slice_id),
            )
            row = connection.execute(
                "SELECT * FROM slice_results WHERE result_id = last_insert_rowid()"
            ).fetchone()
        assert row is not None
        return _row_to_slice_result(row)

    def list_slice_results(self, slice_id: str) -> list[SliceResultRecord]:
        with self.storage.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM slice_results
                WHERE slice_id = ?
                ORDER BY result_id ASC
                """,
                (slice_id,),
            ).fetchall()
        return [_row_to_slice_result(row) for row in rows]


class ExecutorService:
    def __init__(
        self,
        *,
        storage: Storage,
        repository: OrxRepository,
        ownership: OwnershipService,
        transport: TmuxTransport | None = None,
        continuity: ContinuityService | None = None,
        session_namespace: str | None = None,
        runner_launcher: RunnerSessionLauncher | None = None,
    ) -> None:
        self.storage = storage
        self.repository = repository
        self.ownership = ownership
        self.transport = transport or TmuxClient()
        self.continuity = continuity or ContinuityService(storage)
        self.store = ExecutorStore(storage)
        self.session_namespace = session_namespace
        self.runner_launcher = runner_launcher or LocalRunnerSessionLauncher(transport=self.transport)

    def claim_session(
        self,
        issue_key: str,
        runner_id: str,
        *,
        startup_cwd: str | None = None,
    ) -> ExecutorSessionRecord:
        existing = self.store.get_session(runner_id)
        if existing and existing.issue_key == issue_key and self.transport.has_session(existing.session_name):
            self.ownership.claim_issue(issue_key, runner_id)
            return existing

        session_name, pane_target = self.runner_launcher.ensure_session(
            project_key=self.session_namespace or runner_id,
            repo_root=startup_cwd,
            runner_id=runner_id,
        )
        self.ownership.claim_issue(issue_key, runner_id)

        return self.store.upsert_session(
            runner_id=runner_id,
            issue_key=issue_key,
            session_name=session_name,
            pane_target=pane_target,
            transport="tmux-codex",
            state="claimed",
            metadata={
                "dispatch_mode": "runner_only",
                "project_key": self.session_namespace or runner_id,
                "repo_root": startup_cwd,
                "runner_session": session_name,
                "session_residency": "tmux_runner",
            },
        )

    def heartbeat(self, runner_id: str) -> ExecutorSessionRecord:
        session = self.store.get_session(runner_id)
        if session is None:
            raise ValueError(f"No executor session for runner {runner_id}")
        return self.store.upsert_session(
            runner_id=session.runner_id,
            issue_key=session.issue_key,
            session_name=session.session_name,
            pane_target=session.pane_target,
            transport=session.transport,
            heartbeat_at=_utc_now(),
            last_result_at=session.last_result_at,
            state="active",
            metadata=session.metadata,
        )

    def replay_slice_request(self, slice_id: str) -> ExecutorSessionRecord:
        request = self.store.get_slice_request(slice_id)
        if request is None:
            raise ValueError(f"Unknown slice request {slice_id}")
        session = self.claim_session(
            request.issue_key,
            request.runner_id,
            startup_cwd=_request_repo_root(request.request),
        )
        return self.store.upsert_session(
            runner_id=session.runner_id,
            issue_key=session.issue_key,
            session_name=session.session_name,
            pane_target=session.pane_target,
            transport=session.transport,
            heartbeat_at=_utc_now(),
            last_result_at=session.last_result_at,
            state="active",
            metadata=session.metadata,
        )

    def attach_target(self, runner_id: str) -> str:
        session = self.store.get_session(runner_id)
        if session is None:
            raise ValueError(f"No executor session for runner {runner_id}")
        return session.session_name

    def view_pane(self, runner_id: str, *, lines: int = 50) -> str:
        session = self.store.get_session(runner_id)
        if session is None:
            raise ValueError(f"No executor session for runner {runner_id}")
        return self.transport.capture_pane(session.session_name, lines=lines)

    def dispatch_slice(
        self,
        *,
        issue_key: str,
        runner_id: str,
        objective: str,
        acceptance: list[str],
        command_id: int | None = None,
        context: dict[str, Any] | None = None,
        slice_goal: str | None = None,
        validation_plan: list[str] | None = None,
        blockers: list[str] | None = None,
        discovered_gaps: list[str] | None = None,
        idempotency_key: str | None = None,
        resume_context: dict[str, Any] | None = None,
    ) -> SliceRequestRecord:
        startup_cwd = _request_repo_root(context or resume_context)
        existing_session = self.store.get_session(runner_id)
        session_name = (
            existing_session.session_name
            if existing_session is not None
            else _runner_session_name(self.session_namespace or runner_id)
        )
        pane_target = (
            existing_session.pane_target
            if existing_session is not None
            else f"{session_name}:0.0"
        )
        durable_context = {}
        if context:
            durable_context.update(context)
        if resume_context:
            durable_context.update(resume_context)
        request = {
            "issue_key": issue_key,
            "runner_id": runner_id,
            "objective": objective,
            "slice_goal": slice_goal or objective,
            "acceptance": acceptance,
            "validation_plan": validation_plan or acceptance,
            "context": durable_context,
            "session_residency": "tmux",
        }
        previous_continuity = self.continuity.get_state(issue_key, runner_id)
        record = self.store.create_slice_request(
            issue_key=issue_key,
            runner_id=runner_id,
            session_name=session_name,
            command_id=command_id,
            request=request,
        )
        self.continuity.begin_slice(
            issue_key=issue_key,
            runner_id=runner_id,
            objective=objective,
            slice_goal=slice_goal,
            acceptance=acceptance,
            validation_plan=validation_plan,
            blockers=blockers,
            discovered_gaps=discovered_gaps,
            idempotency_key=idempotency_key,
            resume_context=durable_context,
            active_slice_id=record.slice_id,
            active_command_id=command_id,
            session_name=session_name,
            pane_target=pane_target,
            transport="tmux-codex",
        )
        try:
            session = self.claim_session(issue_key, runner_id, startup_cwd=startup_cwd)
        except Exception:
            self.store.delete_slice_request(record.slice_id)
            if previous_continuity is None:
                self.continuity.clear_state(issue_key, runner_id)
            else:
                self.continuity.restore_state(previous_continuity)
            raise
        session = self.store.upsert_session(
            runner_id=session.runner_id,
            issue_key=session.issue_key,
            session_name=session.session_name,
            pane_target=session.pane_target,
            transport=session.transport,
            heartbeat_at=_utc_now(),
            last_result_at=session.last_result_at,
            state="active",
            metadata={
                **session.metadata,
                "active_slice_goal": request["slice_goal"],
                "active_slice_id": record.slice_id,
                "objective": objective,
            },
        )
        return record

    def submit_slice_result(
        self,
        slice_id: str,
        payload: dict[str, Any],
        *,
        gate: SliceApplyGate | None = None,
    ) -> SliceResultRecord:
        request = self.store.get_slice_request(slice_id)
        if request is None:
            raise ValueError(f"Unknown slice request {slice_id}")
        validated = validate_slice_result(payload)
        payload_hash = _payload_hash(payload)
        existing_results = self.store.list_slice_results(slice_id)
        for existing in existing_results:
            if existing.payload_hash and existing.payload_hash == payload_hash:
                return self.store.store_slice_result(
                    slice_id=slice_id,
                    runner_id=request.runner_id,
                    issue_key=request.issue_key,
                    result=validated,
                    apply_status="duplicate_ignored",
                    payload_hash=payload_hash,
                    stale_reason="duplicate_payload",
                )

        session = self.store.get_session(request.runner_id)
        continuity = self.continuity.get_state(request.issue_key, request.runner_id)
        apply_status = "applied"
        stale_reason = None
        if continuity is None:
            apply_status = "stale_audit_only"
            stale_reason = "missing_continuity_state"
        elif continuity.active_slice_id != slice_id:
            apply_status = "stale_audit_only"
            stale_reason = f"active_slice_mismatch:{continuity.active_slice_id or 'none'}"
        elif gate is not None:
            mismatch = _slice_gate_mismatch(
                payload=payload,
                request=request,
                continuity=continuity,
                gate=gate,
            )
            if mismatch is not None:
                apply_status = "stale_audit_only"
                stale_reason = mismatch
        result = self.store.store_slice_result(
            slice_id=slice_id,
            runner_id=request.runner_id,
            issue_key=request.issue_key,
            result=validated,
            apply_status=apply_status,
            payload_hash=payload_hash,
            stale_reason=stale_reason,
        )
        if apply_status == "applied" and session is not None:
            self.store.upsert_session(
                runner_id=session.runner_id,
                issue_key=session.issue_key,
                session_name=session.session_name,
                pane_target=session.pane_target,
                transport=session.transport,
                heartbeat_at=session.heartbeat_at,
                last_result_at=result.submitted_at,
                state=validated.status,
                metadata=session.metadata,
            )
            self.continuity.complete_slice(
                issue_key=request.issue_key,
                runner_id=request.runner_id,
                status=validated.status,
                summary=validated.summary,
                verified=validated.verified,
                next_slice=validated.next_slice,
                artifacts=validated.artifacts,
                submitted_at=result.submitted_at,
                session_name=session.session_name,
                pane_target=session.pane_target,
                transport=session.transport,
            )
        return result


def validate_slice_result(payload: dict[str, Any]) -> ValidatedSliceResult:
    if not isinstance(payload, dict):
        raise SliceResultValidationError("Slice result payload must be a JSON object.")

    status = _required_non_empty(payload.get("status"), field_name="status").lower()
    if status not in SLICE_RESULT_STATUS:
        raise SliceResultValidationError(f"Unsupported slice result status: {status}")

    summary = _required_non_empty(payload.get("summary"), field_name="summary")
    normalized_summary = " ".join(summary.lower().split())
    if normalized_summary in PLACEHOLDER_SLICE_SUMMARIES:
        raise SliceResultValidationError("Slice result summary must not be a placeholder.")
    verified = payload.get("verified")
    if not isinstance(verified, bool):
        raise SliceResultValidationError("Slice result verified field must be boolean.")

    next_slice = payload.get("next_slice")
    if next_slice is not None and not isinstance(next_slice, str):
        raise SliceResultValidationError("Slice result next_slice must be a string when present.")
    if isinstance(next_slice, str):
        next_slice = next_slice.strip() or None

    artifacts_value = payload.get("artifacts", [])
    if not isinstance(artifacts_value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in artifacts_value
    ):
        raise SliceResultValidationError("Slice result artifacts must be a list of non-empty strings.")

    metrics = payload.get("metrics", {})
    if not isinstance(metrics, dict):
        raise SliceResultValidationError("Slice result metrics must be an object.")

    # Require explicit structured fields so prose-only submissions are rejected.
    if "next_slice" not in payload or "artifacts" not in payload or "metrics" not in payload:
        raise SliceResultValidationError(
            "Slice result must include next_slice, artifacts, and metrics fields."
        )
    for field in (
        "issue_key",
        "packet_key",
        "packet_revision",
        "latest_handoff_revision",
        "continuity_revision",
        "decision_epoch",
    ):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise SliceResultValidationError(
                f"Slice result {field} must be a string when present."
            )

    return ValidatedSliceResult(
        status=status,
        summary=summary,
        verified=verified,
        next_slice=next_slice,
        artifacts=tuple(entry.strip() for entry in artifacts_value),
        metrics=metrics,
    )


def _required_non_empty(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SliceResultValidationError(f"Slice result {field_name} must be a non-empty string.")
    return value.strip()


def _runner_session_name(project_key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", project_key.lower()).strip("-")
    return f"runner-{normalized or 'main'}"[:64]


def _request_repo_root(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict):
        return None
    repo_root = context.get("repo_root")
    if isinstance(repo_root, str) and repo_root.strip():
        return repo_root.strip()
    nested = context.get("context")
    if isinstance(nested, dict):
        repo_root = nested.get("repo_root")
        if isinstance(repo_root, str) and repo_root.strip():
            return repo_root.strip()
    return None


def _tmux_codex_home() -> Path:
    configured = os.environ.get("TMUX_CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    dev_root = Path(os.environ.get("DEV", str(Path.home() / "Dev"))).expanduser().resolve()
    return (dev_root / "workspace" / "tmux-codex").resolve()


def _pane_target(transport: TmuxTransport, session_name: str) -> str:
    panes = transport.list_panes(session_name)
    return panes[0] if panes else f"{session_name}:0.0"


def _runner_launch_command(*, project_key: str, repo_root: str | None) -> str:
    target_root = repo_root.strip() if isinstance(repo_root, str) and repo_root.strip() else ""
    parts = ["tmux-codex loop-bg", project_key]
    if target_root:
        parts.extend(["--project-root", target_root])
    return " ".join(shlex.quote(part) for part in parts)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_session(row: Any) -> ExecutorSessionRecord:
    return ExecutorSessionRecord(
        runner_id=row["runner_id"],
        issue_key=row["issue_key"],
        session_name=row["session_name"],
        pane_target=row["pane_target"],
        transport=row["transport"],
        heartbeat_at=row["heartbeat_at"],
        last_result_at=row["last_result_at"],
        state=row["state"],
        metadata=json.loads(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_slice_request(row: Any) -> SliceRequestRecord:
    return SliceRequestRecord(
        slice_id=row["slice_id"],
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        command_id=int(row["command_id"]) if row["command_id"] is not None else None,
        session_name=row["session_name"],
        request=json.loads(row["request_json"]),
        dispatched_at=row["dispatched_at"],
        status=row["status"],
    )


def _row_to_slice_result(row: Any) -> SliceResultRecord:
    return SliceResultRecord(
        result_id=int(row["result_id"]),
        slice_id=row["slice_id"],
        runner_id=row["runner_id"],
        issue_key=row["issue_key"],
        status=row["status"],
        summary=row["summary"],
        verified=bool(row["verified"]),
        next_slice=row["next_slice"],
        artifacts=tuple(json.loads(row["artifacts_json"])),
        metrics=json.loads(row["metrics_json"]),
        submitted_at=row["submitted_at"],
        apply_status=row["apply_status"] if "apply_status" in row.keys() else "applied",
        payload_hash=row["payload_hash"] if "payload_hash" in row.keys() else None,
        stale_reason=row["stale_reason"] if "stale_reason" in row.keys() else None,
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _slice_gate_mismatch(
    *,
    payload: dict[str, Any],
    request: SliceRequestRecord,
    continuity: Any,
    gate: SliceApplyGate,
) -> str | None:
    if gate.expected_issue_key and request.issue_key != gate.expected_issue_key:
        return "issue_key_mismatch"
    if gate.expected_active_slice_id and continuity.active_slice_id != gate.expected_active_slice_id:
        return "active_slice_gate_mismatch"
    if gate.expected_packet_key:
        packet_key = str(payload.get("packet_key") or "").strip()
        if packet_key and packet_key != gate.expected_packet_key:
            return "packet_key_mismatch"
    if gate.expected_packet_revision:
        packet_revision = str(payload.get("packet_revision") or "").strip()
        if packet_revision and packet_revision != gate.expected_packet_revision:
            return "packet_revision_mismatch"
    if gate.expected_latest_handoff_revision:
        handoff_revision = str(payload.get("latest_handoff_revision") or "").strip()
        if handoff_revision and handoff_revision != gate.expected_latest_handoff_revision:
            return "handoff_revision_mismatch"
    if gate.expected_continuity_revision:
        continuity_revision = str(payload.get("continuity_revision") or "").strip()
        if continuity_revision and continuity_revision != gate.expected_continuity_revision:
            return "continuity_revision_mismatch"
    if gate.expected_decision_epoch:
        decision_epoch = str(payload.get("decision_epoch") or "").strip()
        if decision_epoch and decision_epoch != gate.expected_decision_epoch:
            return "decision_epoch_mismatch"
    return None
