"""SQLite-backed durable control plane for ticket-native runner orchestration."""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .runner_state import RunnerStatePaths, derive_kanban_runtime_view, detect_git_context, utc_now

LEASE_TTL_SECONDS = 15 * 60
OPEN_RUN_STATUSES = {"queued", "preparing", "refining", "planning", "running", "verifying", "paused", "blocked"}
PARENT_TRACKER_CLASSES = {"coordination", "phase_parent", "umbrella"}
FINISHED_RUN_STATUSES = {"completed", "abandoned"}
ACTIONABLE_PROJECT_STATUSES = {"Inbox", "Ready", "In Progress"}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
RUN_STATUS_BY_ACTION = {
    "select_next_issue": "queued",
    "wait_for_human_approval": "paused",
    "apply_operator_override": "paused",
    "repair_workspace": "preparing",
    "acquire_worktree": "preparing",
    "spawn_refinement_agent": "refining",
    "spawn_planning_agent": "planning",
    "spawn_execution_agent": "running",
    "spawn_verification_agent": "verifying",
    "create_followup_ticket": "blocked",
    "write_blocker_comment": "blocked",
    "refresh_issue_snapshot": "queued",
}
EVENT_KIND_BY_ACTION = {
    "wait_for_human_approval": "paused",
    "apply_operator_override": "resumed",
    "spawn_refinement_agent": "refinement_requested",
    "spawn_planning_agent": "planning_requested",
    "spawn_execution_agent": "execution_started",
    "spawn_verification_agent": "verification_started",
    "create_followup_ticket": "followup_requested",
    "write_blocker_comment": "blocked",
    "select_next_issue": "issue_selected",
    "repair_workspace": "lease_recovered",
    "acquire_worktree": "lease_recovered",
}


def reconcile_control_plane(
    *,
    paths: RunnerStatePaths,
    state: dict[str, Any],
    kanban_state: dict[str, Any],
    enable_pending_exists: bool = False,
) -> dict[str, Any]:
    """Persist deterministic reconcile outputs into the durable control plane."""
    control = RunnerControlPlane(paths)
    return control.reconcile(
        state=state,
        kanban_state=kanban_state,
        enable_pending_exists=enable_pending_exists,
    )


class RunnerControlPlane:
    def __init__(self, paths: RunnerStatePaths) -> None:
        self.paths = paths
        self.project_root = paths.memory_dir.parent.resolve()
        self.paths.control_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def request_override(
        self,
        *,
        action: str,
        requested_by: str,
        reason: str | None = None,
        issue_url: str | None = None,
        target_issue: dict[str, Any] | None = None,
        target_run_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        target_issue = target_issue if isinstance(target_issue, dict) else {}
        target_issue_url = _as_text(target_issue.get("url"))
        target_issue_repo = _as_text(target_issue.get("repo"))
        target_issue_title = _as_text(target_issue.get("title"))
        target_issue_number = target_issue.get("number")

        if target_issue_url:
            self._upsert_issue_snapshot(
                {
                    "url": target_issue_url,
                    "repo": target_issue_repo,
                    "title": target_issue_title,
                    "number": target_issue_number,
                    "issue_class": _as_text(target_issue.get("issue_class")),
                    "complexity": _as_text(target_issue.get("complexity")),
                    "routing": _as_text(target_issue.get("routing")),
                },
                phase=None,
            )

        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_operator_overrides(
                    issue_url, target_issue_url, action, requested_by, reason,
                    target_repo, target_number, target_title, target_run_id, payload_json,
                    status, created_at, updated_at
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, 'pending', ?11, ?11)
                """,
                (
                    issue_url,
                    target_issue_url,
                    action,
                    requested_by,
                    reason,
                    target_issue_repo,
                    target_issue_number,
                    target_issue_title,
                    target_run_id,
                    _json({"target_run_id": target_run_id} if target_run_id else {}),
                    now,
                ),
            )
            override_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        return {
            "override_id": override_id,
            "action": action,
            "issue_url": issue_url,
            "target_issue_url": target_issue_url,
            "target_run_id": target_run_id,
            "requested_by": requested_by,
            "reason": reason,
            "created_at": now,
        }

    def import_github_item(
        self,
        item: dict[str, Any],
        *,
        issue: dict[str, Any] | None = None,
        issue_thread: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .github_sync import normalize_github_issue_import

        snapshot, phase = normalize_github_issue_import(
            item=item,
            issue=issue,
            issue_thread=issue_thread,
        )
        self._upsert_issue_snapshot(snapshot, phase=phase)
        return snapshot

    def import_issue_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        phase: str | None = None,
    ) -> dict[str, Any]:
        self._upsert_issue_snapshot(snapshot, phase=phase)
        return snapshot

    def describe(self, *, issue_url: str | None = None) -> dict[str, Any]:
        with self._connection() as conn:
            run_row = None
            lease_row = None
            if issue_url:
                run_row = conn.execute(
                    """
                    SELECT run_id, issue_url, issue_repo, issue_number, phase, status,
                           branch, worktree_path, session_name, started_at, updated_at, last_heartbeat_at
                    FROM orchestrator_runs
                    WHERE issue_url = ?1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (issue_url,),
                ).fetchone()
                lease_row = conn.execute(
                    """
                    SELECT issue_url, owner_id, acquired_at, lease_expires_at, heartbeat_at
                    FROM orchestrator_leases
                    WHERE issue_url = ?1
                    """,
                    (issue_url,),
                ).fetchone()

            override_rows = conn.execute(
                """
                SELECT id, issue_url, target_issue_url, action, requested_by, reason, status,
                       target_repo, target_number, target_title, target_run_id, payload_json,
                       created_at, updated_at, consumed_at
                FROM orchestrator_operator_overrides
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 5
                """
            ).fetchall()
            event_rows = conn.execute(
                """
                SELECT event_kind, summary, created_at
                FROM orchestrator_run_events
                ORDER BY id DESC
                LIMIT 5
                """
            ).fetchall()

        return {
            "run": _row_dict(run_row),
            "lease": _row_dict(lease_row),
            "pending_overrides": [_row_dict(row) for row in override_rows],
            "recent_events": [_row_dict(row) for row in event_rows],
            "mutation": self._mutation_snapshot(issue_url=issue_url),
            "diagnostics": self.diagnostics(issue_url=issue_url),
            "db_path": str(self.paths.control_db),
        }

    def diagnostics(self, *, issue_url: str | None = None) -> dict[str, Any]:
        return {
            "failure_classification": self._classify_issue_failure(issue_url=issue_url),
            "metrics": self._metrics_snapshot(),
            "invariants": self._check_invariants(),
            "replay": self._replay_snapshot(issue_url=issue_url),
        }

    def reconcile(
        self,
        *,
        state: dict[str, Any],
        kanban_state: dict[str, Any],
        enable_pending_exists: bool,
    ) -> dict[str, Any]:
        base_state = copy.deepcopy(kanban_state if isinstance(kanban_state, dict) else {})
        previous_active_issue_url = _as_text((base_state.get("active_issue") or {}).get("url"))
        workspace = detect_git_context(self.project_root)
        applied_override = self._apply_pending_override(base_state)
        runtime_phase = _as_text(state.get("current_phase"))
        done_gate_status = _as_text(state.get("done_gate_status"))
        selected_issue = self._yield_active_issue_to_board(
            base_state,
            workspace=workspace,
            runtime_phase=runtime_phase,
            done_gate_status=done_gate_status,
        )
        recovered_run = None if selected_issue else self._recover_active_run(
            base_state,
            workspace=workspace,
            runtime_phase=runtime_phase,
            done_gate_status=done_gate_status,
        )
        selected_issue = selected_issue or (None if recovered_run else self._select_next_issue(base_state, workspace=workspace))

        active_issue_before_reconcile = (
            base_state.get("active_issue") if isinstance(base_state.get("active_issue"), dict) else None
        )
        active_issue_url = _as_text((active_issue_before_reconcile or {}).get("url"))
        owner_id = _owner_id(
            project=_as_text(state.get("project")) or self.project_root.name,
            runner_id=_as_text(state.get("runner_id")) or "main",
        )

        live_lease = self._get_live_lease(active_issue_url, now=utc_now()) if active_issue_url else None
        if (
            active_issue_before_reconcile
            and live_lease
            and _as_text(live_lease.get("owner_id")) != owner_id
        ):
            self._increment_metric("duplicate_work_prevented")
            self._increment_metric("lease_contention")
            blocker = base_state.get("blocker")
            if not isinstance(blocker, dict):
                blocker = {}
            blocker.update(
                {
                    "is_blocked": True,
                    "category": "lease",
                    "reason": f"issue leased by {_as_text(live_lease.get('owner_id'))}",
                    "needs": "wait_for_lease_recovery",
                    "resume_from": "workspace",
                    "external": True,
                }
            )
            base_state["blocker"] = blocker

        derived = derive_kanban_runtime_view(
            state=state,
            kanban_state=base_state,
            enable_pending_exists=enable_pending_exists,
        )
        active_issue = derived.get("active_issue") if isinstance(derived.get("active_issue"), dict) else None
        active_issue_url = _as_text((active_issue or {}).get("url"))
        issue_scope = active_issue_url or _project_scope(_as_text(state.get("project")) or self.project_root.name)
        primary_action = _primary_action(derived)

        if active_issue:
            self._upsert_issue_snapshot(active_issue, phase=_as_text(derived.get("phase")))
        self._sync_conditions(issue_scope, derived.get("conditions"))

        run_row = None
        run_id = None
        if active_issue:
            if primary_action == "wait_for_human_approval":
                self._release_lease(active_issue_url, owner_id)
            else:
                lease_result = self._acquire_or_renew_lease(active_issue_url, owner_id, LEASE_TTL_SECONDS)
                if lease_result["state"] in {"acquired", "recovered", "renewed"}:
                    if lease_result["state"] == "recovered":
                        self._increment_metric("stale_lease_recovered")
                    self._record_event(
                        issue_url=active_issue_url,
                        run_id=None,
                        event_kind="lease_acquired" if lease_result["state"] != "recovered" else "lease_recovered",
                        summary=lease_result["summary"],
                        payload=lease_result,
                        dedupe_key=f"lease:{active_issue_url}:{lease_result['state']}:{lease_result['heartbeat_at']}",
                    )

            run_status = RUN_STATUS_BY_ACTION.get(primary_action, "queued")
            run_row = self._upsert_run(
                issue=active_issue,
                phase=_as_text(derived.get("phase")) or _as_text(state.get("current_phase")) or "selecting",
                status=run_status,
                branch=_as_text(workspace.get("git_branch")),
                worktree_path=_as_text(workspace.get("git_worktree")) or str(self.project_root),
                session_name=_as_text(state.get("session_id")),
            )
            run_id = _as_text(run_row.get("run_id"))
            self._close_stale_runs(current_issue_url=active_issue_url)
            self._write_checkpoint(
                checkpoint_key=f"reconcile:{issue_scope}",
                run_id=run_id,
                issue_url=issue_scope,
                checkpoint_type="reconcile",
                payload={
                    "phase": derived.get("phase"),
                    "reconcile": derived.get("reconcile"),
                    "conditions": derived.get("conditions"),
                    "drift": derived.get("drift"),
                    "controller": (derived.get("reconcile") or {}).get("controller"),
                    "workspace": workspace,
                },
            )
            self._write_handoff(
                run_id=run_id,
                issue_url=issue_scope,
                phase=_as_text(derived.get("phase")) or "selecting",
                summary=_handoff_summary(derived),
                payload=self._build_handoff_payload(
                    derived=derived,
                    workspace=workspace,
                ),
                dedupe_key=f"handoff:{issue_scope}:{_action_dedupe_suffix(derived)}",
            )
        else:
            self._pause_open_runs()
            self._release_leases_for_owner(owner_id)

        if active_issue_url and previous_active_issue_url != active_issue_url:
            self._record_event(
                issue_url=active_issue_url,
                run_id=run_id,
                event_kind="resumed" if recovered_run else "issue_selected",
                summary=(
                    f"Recovered active run for {active_issue_url} from durable state."
                    if recovered_run
                    else f"Selected active issue {active_issue_url}."
                ),
                payload={
                    "previous_issue_url": previous_active_issue_url,
                    "active_issue": active_issue,
                    "recovered_run": recovered_run,
                    "selected_issue": selected_issue,
                },
                dedupe_key=f"{'resumed' if recovered_run else 'issue_selected'}:{active_issue_url}",
            )

        if applied_override:
            self._record_event(
                issue_url=active_issue_url or issue_scope,
                run_id=run_id,
                event_kind="resumed" if applied_override["action"] == "resume" else "paused",
                summary=f"Applied operator override `{applied_override['action']}`.",
                payload=applied_override,
                dedupe_key=f"override:{applied_override['id']}",
            )

        action_event_kind = EVENT_KIND_BY_ACTION.get(primary_action)
        action_packet = _primary_action_packet(derived)
        if action_event_kind and action_packet:
            self._record_event(
                issue_url=active_issue_url or issue_scope,
                run_id=run_id,
                event_kind=action_event_kind,
                summary=_as_text(action_packet.get("reason")) or primary_action,
                payload=action_packet,
                dedupe_key=f"action:{_action_dedupe_suffix(derived)}",
            )

        derived["control"] = self.describe(issue_url=active_issue_url)
        return derived

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.control_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self) -> Any:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orchestrator_issue_snapshots(
                    issue_url TEXT PRIMARY KEY,
                    issue_repo TEXT,
                    issue_number INTEGER,
                    title TEXT,
                    issue_class TEXT,
                    complexity TEXT,
                    routing TEXT,
                    phase TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_leases(
                    issue_url TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_runs(
                    run_id TEXT PRIMARY KEY,
                    issue_url TEXT NOT NULL,
                    issue_repo TEXT,
                    issue_number INTEGER,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    branch TEXT,
                    worktree_path TEXT,
                    session_name TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_run_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT UNIQUE,
                    run_id TEXT,
                    issue_url TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_conditions(
                    issue_url TEXT NOT NULL,
                    condition_key TEXT NOT NULL,
                    status INTEGER NOT NULL,
                    reason TEXT,
                    message TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(issue_url, condition_key)
                );

                CREATE TABLE IF NOT EXISTS orchestrator_handoffs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT UNIQUE,
                    run_id TEXT,
                    issue_url TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_checkpoints(
                    checkpoint_key TEXT PRIMARY KEY,
                    run_id TEXT,
                    issue_url TEXT NOT NULL,
                    checkpoint_type TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_operator_overrides(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_url TEXT,
                    target_issue_url TEXT,
                    action TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    reason TEXT,
                    target_repo TEXT,
                    target_number INTEGER,
                    target_title TEXT,
                    target_run_id TEXT,
                    payload_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_issue_url
                    ON orchestrator_runs(issue_url, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_orchestrator_events_issue_url
                    ON orchestrator_run_events(issue_url, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_orchestrator_overrides_status
                    ON orchestrator_operator_overrides(status, created_at ASC);

                CREATE TABLE IF NOT EXISTS orchestrator_metrics(
                    metric_key TEXT PRIMARY KEY,
                    metric_value INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "orchestrator_conditions", "message", "TEXT")
            self._ensure_column(conn, "orchestrator_operator_overrides", "target_run_id", "TEXT")
            self._ensure_column(conn, "orchestrator_operator_overrides", "payload_json", "TEXT")

    def _upsert_issue_snapshot(self, issue: dict[str, Any], *, phase: str | None) -> None:
        issue_url = _as_text(issue.get("url"))
        if not issue_url:
            return
        now = utc_now()
        with self._connection() as conn:
            existing_row = conn.execute(
                """
                SELECT issue_repo, issue_number, title, issue_class, complexity, routing, phase, payload_json
                FROM orchestrator_issue_snapshots
                WHERE issue_url = ?1
                """,
                (issue_url,),
            ).fetchone()
            existing = _row_dict(existing_row) or {}
            payload = _parse_json(existing.get("payload_json"))
            for key, value in issue.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                payload[key] = value
            payload.update(
                {
                    "url": issue_url,
                    "repo": _coalesce_text(issue.get("repo"), payload.get("repo"), existing.get("issue_repo")),
                    "number": issue.get("number")
                    if issue.get("number") is not None
                    else payload.get("number", existing.get("issue_number")),
                    "title": _coalesce_text(issue.get("title"), payload.get("title"), existing.get("title")),
                    "issue_class": _coalesce_text(
                        issue.get("issue_class"), payload.get("issue_class"), existing.get("issue_class")
                    ),
                    "complexity": _coalesce_text(
                        issue.get("complexity"), payload.get("complexity"), existing.get("complexity")
                    ),
                    "routing": _coalesce_text(issue.get("routing"), payload.get("routing"), existing.get("routing")),
                }
            )
            snapshot_phase = _coalesce_text(phase, existing.get("phase"))
            conn.execute(
                """
                INSERT INTO orchestrator_issue_snapshots(
                    issue_url, issue_repo, issue_number, title, issue_class, complexity,
                    routing, phase, payload_json, updated_at
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                ON CONFLICT(issue_url) DO UPDATE SET
                    issue_repo = excluded.issue_repo,
                    issue_number = excluded.issue_number,
                    title = excluded.title,
                    issue_class = excluded.issue_class,
                    complexity = excluded.complexity,
                    routing = excluded.routing,
                    phase = excluded.phase,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    issue_url,
                    payload["repo"],
                    payload["number"],
                    payload["title"],
                    payload["issue_class"],
                    payload["complexity"],
                    payload["routing"],
                    snapshot_phase,
                    _json(payload),
                    now,
                ),
            )

    def _apply_pending_override(self, kanban_state: dict[str, Any]) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, issue_url, target_issue_url, action, requested_by, reason,
                       target_repo, target_number, target_title, target_run_id, payload_json, created_at
                FROM orchestrator_operator_overrides
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            override = _row_dict(row)
            operator = kanban_state.get("operator")
            if not isinstance(operator, dict):
                operator = {}
                kanban_state["operator"] = operator

            active_issue = kanban_state.get("active_issue")
            if not isinstance(active_issue, dict):
                active_issue = None

            action = _as_text(override.get("action")) or ""
            if action == "pause":
                operator["pause_requested"] = True
                operator["approval_required"] = True
                operator["approval_reason"] = _as_text(override.get("reason")) or "paused_by_operator"
            elif action == "resume":
                operator["pause_requested"] = False
                operator["resume_requested"] = True
                operator["approval_required"] = False
                operator["approval_reason"] = None
                operator["resume_run_id"] = None
            elif action == "force":
                issue_url = _as_text(override.get("target_issue_url"))
                if issue_url:
                    snapshot = self._issue_snapshot(issue_url)
                    kanban_state["active_issue"] = {
                        "url": issue_url,
                        "repo": _as_text(override.get("target_repo")) or _as_text((snapshot or {}).get("issue_repo")),
                        "number": override.get("target_number") or (snapshot or {}).get("issue_number"),
                        "title": _as_text(override.get("target_title")) or _as_text((snapshot or {}).get("title")),
                        "issue_class": _as_text((snapshot or {}).get("issue_class")),
                        "complexity": _as_text((snapshot or {}).get("complexity")),
                        "routing": _as_text((snapshot or {}).get("routing")),
                    }
                    kanban_state["phase"] = "executing"
            elif action == "skip":
                target_issue_url = _as_text(override.get("target_issue_url")) or _as_text((active_issue or {}).get("url"))
                if active_issue and target_issue_url and _as_text(active_issue.get("url")) == target_issue_url:
                    kanban_state["active_issue"] = None
                    kanban_state["phase"] = "selecting"
            elif action == "mark_blocked":
                blocker = kanban_state.get("blocker")
                if not isinstance(blocker, dict):
                    blocker = {}
                blocker.update(
                    {
                        "is_blocked": True,
                        "category": "operator",
                        "reason": _as_text(override.get("reason")) or "marked_blocked_by_operator",
                        "needs": "operator_unblock",
                        "resume_from": "operator_override",
                        "external": True,
                    }
                )
                kanban_state["blocker"] = blocker
                kanban_state["phase"] = "blocked"
            elif action == "resume_run":
                target_run_id = _as_text(override.get("target_run_id"))
                run_row = self._run_snapshot(target_run_id) if target_run_id else None
                if run_row:
                    self._hydrate_issue_from_run(kanban_state, run_row)
                    operator["pause_requested"] = False
                    operator["resume_requested"] = True
                    operator["approval_required"] = False
                    operator["approval_reason"] = None
                    operator["resume_run_id"] = target_run_id
            elif action == "override_split":
                operator["split_override"] = True

            now = utc_now()
            conn.execute(
                """
                UPDATE orchestrator_operator_overrides
                SET status = 'consumed', updated_at = ?2, consumed_at = ?2
                WHERE id = ?1
                """,
                (override["id"], now),
            )
            override["consumed_at"] = now
            return override

    def _issue_snapshot(self, issue_url: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT issue_url, issue_repo, issue_number, title, issue_class, complexity, routing, phase, payload_json
                FROM orchestrator_issue_snapshots
                WHERE issue_url = ?1
                """,
                (issue_url,),
            ).fetchone()
        data = _row_dict(row)
        if not data:
            return None
        payload = _parse_json(data.get("payload_json"))
        if isinstance(payload, dict):
            data["payload"] = payload
        return data

    def _sync_conditions(self, issue_url: str, conditions: Any) -> None:
        if not isinstance(conditions, dict):
            return
        now = utc_now()
        with self._connection() as conn:
            for condition_key, raw in conditions.items():
                if not isinstance(raw, dict):
                    continue
                conn.execute(
                    """
                    INSERT INTO orchestrator_conditions(issue_url, condition_key, status, reason, message, updated_at)
                    VALUES(?1, ?2, ?3, ?4, ?5, ?6)
                    ON CONFLICT(issue_url, condition_key) DO UPDATE SET
                        status = excluded.status,
                        reason = excluded.reason,
                        message = excluded.message,
                        updated_at = excluded.updated_at
                    """,
                    (
                        issue_url,
                        str(condition_key),
                        1 if bool(raw.get("status")) else 0,
                        _as_text(raw.get("reason")),
                        _as_text(raw.get("message")),
                        now,
                    ),
                )

    def _get_live_lease(self, issue_url: str | None, *, now: str) -> dict[str, Any] | None:
        if not issue_url:
            return None
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT issue_url, owner_id, acquired_at, lease_expires_at, heartbeat_at
                FROM orchestrator_leases
                WHERE issue_url = ?1 AND lease_expires_at > ?2
                """,
                (issue_url, now),
            ).fetchone()
        return _row_dict(row)

    def _acquire_or_renew_lease(self, issue_url: str, owner_id: str, ttl_seconds: int) -> dict[str, Any]:
        now = utc_now()
        expires_at = _iso_offset(now, ttl_seconds)
        with self._connection() as conn:
            existing = conn.execute(
                """
                SELECT issue_url, owner_id, acquired_at, lease_expires_at, heartbeat_at
                FROM orchestrator_leases
                WHERE issue_url = ?1
                """,
                (issue_url,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO orchestrator_leases(issue_url, owner_id, acquired_at, lease_expires_at, heartbeat_at)
                    VALUES(?1, ?2, ?3, ?4, ?3)
                    """,
                    (issue_url, owner_id, now, expires_at),
                )
                return {
                    "state": "acquired",
                    "issue_url": issue_url,
                    "owner_id": owner_id,
                    "heartbeat_at": now,
                    "expires_at": expires_at,
                    "summary": f"Acquired lease for {issue_url}.",
                }

            existing_dict = _row_dict(existing)
            existing_owner = _as_text(existing_dict.get("owner_id"))
            existing_expiry = _as_text(existing_dict.get("lease_expires_at"))
            state = "renewed"
            if existing_owner != owner_id and existing_expiry and existing_expiry <= now:
                state = "recovered"
            elif existing_owner != owner_id and existing_expiry and existing_expiry > now:
                return {
                    "state": "contended",
                    "issue_url": issue_url,
                    "owner_id": existing_owner,
                    "heartbeat_at": _as_text(existing_dict.get("heartbeat_at")),
                    "expires_at": existing_expiry,
                    "summary": f"Lease for {issue_url} is still held by {existing_owner}.",
                }

            conn.execute(
                """
                UPDATE orchestrator_leases
                SET owner_id = ?2, lease_expires_at = ?3, heartbeat_at = ?4
                WHERE issue_url = ?1
                """,
                (issue_url, owner_id, expires_at, now),
            )
            return {
                "state": state,
                "issue_url": issue_url,
                "owner_id": owner_id,
                "heartbeat_at": now,
                "expires_at": expires_at,
                "summary": (
                    f"Recovered stale lease for {issue_url}."
                    if state == "recovered"
                    else f"Renewed lease for {issue_url}."
                ),
            }

    def _release_lease(self, issue_url: str | None, owner_id: str) -> None:
        if not issue_url:
            return
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM orchestrator_leases WHERE issue_url = ?1 AND owner_id = ?2",
                (issue_url, owner_id),
            )

    def _release_leases_for_owner(self, owner_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM orchestrator_leases WHERE owner_id = ?1", (owner_id,))

    def _upsert_run(
        self,
        *,
        issue: dict[str, Any],
        phase: str,
        status: str,
        branch: str | None,
        worktree_path: str | None,
        session_name: str | None,
    ) -> dict[str, Any]:
        issue_url = _as_text(issue.get("url")) or ""
        issue_repo = _as_text(issue.get("repo"))
        issue_number = issue.get("number")
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT run_id, issue_url, issue_repo, issue_number, phase, status, branch,
                       worktree_path, session_name, started_at, updated_at, last_heartbeat_at
                FROM orchestrator_runs
                WHERE issue_url = ?1 AND status NOT IN ('completed', 'abandoned')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (issue_url,),
            ).fetchone()
            if row is None:
                run_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO orchestrator_runs(
                        run_id, issue_url, issue_repo, issue_number, phase, status,
                        branch, worktree_path, session_name, started_at, updated_at, last_heartbeat_at
                    ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10, ?10)
                    """,
                    (
                        run_id,
                        issue_url,
                        issue_repo,
                        issue_number,
                        phase,
                        status,
                        branch,
                        worktree_path,
                        session_name,
                        now,
                    ),
                )
            else:
                run_id = _as_text(row["run_id"]) or uuid.uuid4().hex
                conn.execute(
                    """
                    UPDATE orchestrator_runs
                    SET issue_repo = ?2,
                        issue_number = ?3,
                        phase = ?4,
                        status = ?5,
                        branch = ?6,
                        worktree_path = ?7,
                        session_name = ?8,
                        updated_at = ?9,
                        last_heartbeat_at = ?9
                    WHERE run_id = ?1
                    """,
                    (
                        run_id,
                        issue_repo,
                        issue_number,
                        phase,
                        status,
                        branch,
                        worktree_path,
                        session_name,
                        now,
                    ),
                )

            fresh = conn.execute(
                """
                SELECT run_id, issue_url, issue_repo, issue_number, phase, status, branch,
                       worktree_path, session_name, started_at, updated_at, last_heartbeat_at
                FROM orchestrator_runs
                WHERE run_id = ?1
                """,
                (run_id,),
            ).fetchone()
        return _row_dict(fresh) or {"run_id": run_id}

    def _pause_open_runs(self) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE orchestrator_runs
                SET status = 'paused', updated_at = ?1, last_heartbeat_at = ?1
                WHERE status NOT IN ('completed', 'abandoned', 'paused')
                """,
                (now,),
            )

    def _close_stale_runs(self, *, current_issue_url: str) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE orchestrator_runs
                SET status = CASE
                        WHEN status IN ('blocked', 'paused') THEN status
                        ELSE 'paused'
                    END,
                    updated_at = ?2,
                    last_heartbeat_at = ?2
                WHERE issue_url != ?1 AND status NOT IN ('completed', 'abandoned')
                """,
                (current_issue_url, now),
            )
            conn.execute(
                """
                DELETE FROM orchestrator_leases
                WHERE issue_url != ?1
                """,
                (current_issue_url,),
            )

    def _record_event(
        self,
        *,
        issue_url: str,
        run_id: str | None,
        event_kind: str,
        summary: str,
        payload: dict[str, Any] | None,
        dedupe_key: str | None,
    ) -> None:
        if not issue_url:
            return
        now = utc_now()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO orchestrator_run_events(
                    dedupe_key, run_id, issue_url, event_kind, summary, payload_json, created_at
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7)
                """,
                (
                    dedupe_key,
                    run_id,
                    issue_url,
                    event_kind,
                    summary,
                    _json(payload) if payload else None,
                    now,
                ),
            )
        if dedupe_key and cursor.rowcount == 0:
            self._increment_metric("duplicate_work_prevented")

    def _write_handoff(
        self,
        *,
        run_id: str | None,
        issue_url: str,
        phase: str,
        summary: str,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO orchestrator_handoffs(
                    dedupe_key, run_id, issue_url, phase, summary, payload_json, created_at
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7)
                """,
                (dedupe_key, run_id, issue_url, phase, summary, _json(payload), now),
            )

    def _write_checkpoint(
        self,
        *,
        checkpoint_key: str,
        run_id: str | None,
        issue_url: str,
        checkpoint_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_checkpoints(
                    checkpoint_key, run_id, issue_url, checkpoint_type, state_json, created_at, updated_at
                ) VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?6)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    run_id = excluded.run_id,
                    issue_url = excluded.issue_url,
                    checkpoint_type = excluded.checkpoint_type,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (checkpoint_key, run_id, issue_url, checkpoint_type, _json(payload), now),
            )

    def _build_handoff_payload(
        self,
        *,
        derived: dict[str, Any],
        workspace: dict[str, Any],
    ) -> dict[str, Any]:
        reconcile = derived.get("reconcile") if isinstance(derived.get("reconcile"), dict) else {}
        dispatch = (reconcile.get("stage_results") or {}).get("dispatch") if isinstance((reconcile.get("stage_results") or {}).get("dispatch"), dict) else {}
        blocker = derived.get("blocker") if isinstance(derived.get("blocker"), dict) else {}
        packet = _primary_action_packet(derived) or {}
        return {
            "decision": _as_text(dispatch.get("decision")) or _as_text(packet.get("action")) or "idle",
            "why": _as_text(dispatch.get("summary")) or _as_text(packet.get("reason")),
            "next_step": _as_text(packet.get("action")) or "none",
            "blocker": blocker if blocker else None,
            "changed_files": [],
            "tests_run": [],
            "branch": _as_text(workspace.get("git_branch")),
            "worktree": _as_text(workspace.get("git_worktree")) or str(self.project_root),
            "ticket_relations": ((reconcile.get("controller") or {}).get("ticket_relations") if isinstance(reconcile.get("controller"), dict) else {}),
            "resume_point": {
                "phase": _as_text(derived.get("phase")) or "selecting",
                "issue_url": _as_text((derived.get("active_issue") or {}).get("url")),
                "action_idempotency_key": _as_text(packet.get("idempotency_key")),
            },
        }

    def _recover_active_run(
        self,
        kanban_state: dict[str, Any],
        *,
        workspace: dict[str, Any],
        runtime_phase: str | None = None,
        done_gate_status: str | None = None,
    ) -> dict[str, Any] | None:
        active_issue = kanban_state.get("active_issue")
        if isinstance(active_issue, dict) and _as_text(active_issue.get("url")):
            return None
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT run_id, issue_url, issue_repo, issue_number, phase, status, branch, worktree_path, session_name
                FROM orchestrator_runs
                WHERE status NOT IN ('completed', 'abandoned')
                ORDER BY updated_at DESC
                LIMIT 10
                """
            ).fetchall()
        for row in rows:
            run_row = _row_dict(row)
            if not run_row:
                continue
            issue_url = _as_text(run_row.get("issue_url"))
            recovery_gate = self._recovery_gate(
                issue_url,
                active_frontier=False,
                runtime_phase=runtime_phase,
                done_gate_status=done_gate_status,
            )
            if recovery_gate["yield_to_board"]:
                candidate = self._candidate_issue(exclude_issue_url=issue_url)
                if candidate:
                    self._increment_metric("stale_run_yielded")
                    continue
            self._hydrate_issue_from_run(kanban_state, run_row, workspace=workspace)
            return run_row
        return None

    def _yield_active_issue_to_board(
        self,
        kanban_state: dict[str, Any],
        *,
        workspace: dict[str, Any],
        runtime_phase: str | None = None,
        done_gate_status: str | None = None,
    ) -> dict[str, Any] | None:
        active_issue = kanban_state.get("active_issue")
        issue_url = _as_text((active_issue or {}).get("url")) if isinstance(active_issue, dict) else None
        if not issue_url:
            return None
        recovery_gate = self._recovery_gate(
            issue_url,
            active_frontier=True,
            runtime_phase=runtime_phase,
            done_gate_status=done_gate_status,
        )
        if not recovery_gate["yield_to_board"]:
            return None
        candidate = self._candidate_issue(exclude_issue_url=issue_url)
        if not candidate:
            return None
        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        self._increment_metric("stale_run_yielded")
        return self._select_next_issue(kanban_state, workspace=workspace)

    def _select_next_issue(self, kanban_state: dict[str, Any], *, workspace: dict[str, Any]) -> dict[str, Any] | None:
        active_issue = kanban_state.get("active_issue")
        if isinstance(active_issue, dict) and _as_text(active_issue.get("url")):
            return None
        candidate = self._candidate_issue()
        if not candidate:
            return None
        kanban_state["active_issue"] = candidate
        kanban_state["phase"] = "executing"
        active_checkout = kanban_state.get("active_checkout")
        if not isinstance(active_checkout, dict):
            active_checkout = {}
        active_checkout.update(
            {
                "repo_root": str(self.project_root),
                "worktree": _as_text(workspace.get("git_worktree")) or str(self.project_root),
                "branch": _as_text(workspace.get("git_branch")),
            }
        )
        kanban_state["active_checkout"] = active_checkout
        self._increment_metric("selection_count")
        return candidate

    def _candidate_issue(self, *, exclude_issue_url: str | None = None) -> dict[str, Any] | None:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT issue_url, issue_repo, issue_number, title, issue_class, complexity, routing, payload_json, updated_at
                FROM orchestrator_issue_snapshots
                ORDER BY updated_at DESC
                """
            ).fetchall()
        project_repo_hint = self.project_root.name.lower()
        best: tuple[tuple[int, int, int, int], dict[str, Any]] | None = None
        now = utc_now()
        for row in rows:
            data = _row_dict(row) or {}
            issue_url = _as_text(data.get("issue_url"))
            if not issue_url:
                continue
            if exclude_issue_url and issue_url == exclude_issue_url:
                continue
            payload = _parse_json(data.get("payload_json"))
            gate = self._issue_selection_gate(data, payload)
            status = gate["status"]
            if status not in ACTIONABLE_PROJECT_STATUSES:
                continue
            if self._get_live_lease(issue_url, now=now):
                self._increment_metric("duplicate_work_prevented")
                continue
            if gate["skip"]:
                continue
            issue_class = gate["issue_class"]
            blocked_by_values = gate["blocked_by_values"]
            repo = _as_text(data.get("issue_repo")) or ""
            repo_locality = 0 if project_repo_hint and project_repo_hint in repo.lower() else 1
            status_rank = {"Ready": 0, "Inbox": 1, "In Progress": 2}.get(status, 9)
            priority_rank = PRIORITY_ORDER.get(_metadata_text(payload, "priority") or "P2", 9)
            child_bonus = 0 if _metadata_text(payload, "parent") else 1
            executable_penalty = 1 if issue_class in PARENT_TRACKER_CLASSES else 0
            complexity_penalty = {"S": 0, "M": 1, "L": 2, "XL": 3}.get((_as_text(data.get("complexity")) or "M").upper(), 1)
            score = (repo_locality, status_rank, priority_rank, child_bonus, executable_penalty, complexity_penalty)
            candidate = {
                "url": issue_url,
                "repo": repo or None,
                "number": data.get("issue_number"),
                "title": _as_text(data.get("title")),
                "issue_class": _as_text(data.get("issue_class")),
                "complexity": _as_text(data.get("complexity")),
                "routing": _as_text(data.get("routing")),
                "parent": _metadata_text(payload, "parent"),
                "blocked_by": _one_or_many(blocked_by_values),
                "unblocks": _metadata_text(payload, "unblocks"),
                "worktree": _metadata_text(payload, "worktree"),
                "branch": _metadata_text(payload, "branch"),
                "depends_on": _one_or_many(_metadata_values(payload, "depends_on")),
                "merge_into": _metadata_text(payload, "merge_into"),
                "resume_from": _metadata_text(payload, "resume_from"),
                "status": status or None,
                "priority": _metadata_text(payload, "priority"),
            }
            if best is None or score < best[0]:
                best = (score, candidate)
        return best[1] if best else None

    def _issue_selection_gate(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        issue_class = (_as_text(snapshot.get("issue_class")) or _as_text(payload.get("issue_class")) or "").lower()
        complexity = (_as_text(snapshot.get("complexity")) or _as_text(payload.get("complexity")) or "").upper()
        status = _metadata_text(payload, "status")
        children = _metadata_values(payload, "children")
        blocked_by_values = _metadata_values(payload, "blocked_by", "depends_on")
        enhance_required = bool(payload.get("enhance_required") or payload.get("split_required"))
        tracker_like = issue_class in PARENT_TRACKER_CLASSES or bool(children)
        oversized = complexity in {"L", "XL"}
        missing_board_status = not status
        skip = bool(missing_board_status or tracker_like or blocked_by_values or enhance_required or oversized)
        return {
            "status": status,
            "issue_class": issue_class,
            "blocked_by_values": blocked_by_values,
            "skip": skip,
        }

    def _recovery_gate(
        self,
        issue_url: str | None,
        *,
        active_frontier: bool,
        runtime_phase: str | None = None,
        done_gate_status: str | None = None,
    ) -> dict[str, Any]:
        if not issue_url:
            return {"yield_to_board": False}
        snapshot = self._issue_snapshot(issue_url) or {}
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
        selection_gate = self._issue_selection_gate(snapshot, payload)
        failure = self._classify_issue_failure(issue_url=issue_url)
        conditions = self._issue_conditions(issue_url)
        reason = _as_text(failure.get("reason"))
        ready_for_execution = conditions.get("ready_for_execution") or {}
        ready_reason = _as_text(ready_for_execution.get("reason"))
        normalized_runtime_phase = (runtime_phase or "").strip().lower()
        normalized_done_gate_status = (done_gate_status or "").strip().lower()
        closeout_phase_mismatch = bool(
            active_frontier
            and normalized_runtime_phase in {"closeout", "done"}
            and (ready_reason == "enhance_required" or reason == "enhance_required")
        )
        failed_done_gate_mismatch = bool(
            active_frontier
            and normalized_done_gate_status == "failed"
            and (ready_reason == "enhance_required" or reason == "enhance_required")
        )
        yield_to_board = bool(
            selection_gate["skip"]
            or ready_reason == "blocked_by_dependency"
            or reason == "blocked_by_dependency"
            or (not active_frontier and ready_reason == "enhance_required")
            or (not active_frontier and reason == "enhance_required")
            or closeout_phase_mismatch
            or failed_done_gate_mismatch
            or failure.get("category") == "dependencies_resolved"
        )
        return {
            "yield_to_board": yield_to_board,
            "failure": failure,
            "conditions": conditions,
            "selection_gate": selection_gate,
            "runtime_phase": normalized_runtime_phase or None,
            "done_gate_status": normalized_done_gate_status or None,
        }

    def _hydrate_issue_from_run(
        self,
        kanban_state: dict[str, Any],
        run_row: dict[str, Any],
        *,
        workspace: dict[str, Any] | None = None,
    ) -> None:
        issue_url = _as_text(run_row.get("issue_url"))
        if not issue_url:
            return
        snapshot = self._issue_snapshot(issue_url) or {}
        kanban_state["active_issue"] = {
            "url": issue_url,
            "repo": _as_text(run_row.get("issue_repo")) or _as_text(snapshot.get("issue_repo")),
            "number": run_row.get("issue_number") or snapshot.get("issue_number"),
            "title": _as_text(snapshot.get("title")),
            "issue_class": _as_text(snapshot.get("issue_class")),
            "complexity": _as_text(snapshot.get("complexity")),
            "routing": _as_text(snapshot.get("routing")),
            "parent": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "parent"),
            "blocked_by": _one_or_many(
                _metadata_values(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "blocked_by")
            ),
            "unblocks": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "unblocks"),
            "worktree": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "worktree"),
            "branch": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "branch"),
            "depends_on": _one_or_many(
                _metadata_values(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "depends_on")
            ),
            "merge_into": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "merge_into"),
            "resume_from": _metadata_text(snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}, "resume_from"),
        }
        kanban_state["phase"] = _run_phase_to_kanban_phase(_as_text(run_row.get("phase")), _as_text(run_row.get("status")))
        active_checkout = kanban_state.get("active_checkout")
        if not isinstance(active_checkout, dict):
            active_checkout = {}
        active_checkout.update(
            {
                "repo_root": str(self.project_root),
                "worktree": _as_text((workspace or {}).get("git_worktree")) or _as_text(run_row.get("worktree_path")) or str(self.project_root),
                "branch": _as_text((workspace or {}).get("git_branch")) or _as_text(run_row.get("branch")),
            }
        )
        kanban_state["active_checkout"] = active_checkout

    def _run_snapshot(self, run_id: str | None) -> dict[str, Any] | None:
        if not run_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT run_id, issue_url, issue_repo, issue_number, phase, status, branch, worktree_path, session_name
                FROM orchestrator_runs
                WHERE run_id = ?1
                """,
                (run_id,),
            ).fetchone()
        return _row_dict(row)

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _increment_metric(self, metric_key: str, amount: int = 1) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_metrics(metric_key, metric_value, updated_at)
                VALUES(?1, ?2, ?3)
                ON CONFLICT(metric_key) DO UPDATE SET
                    metric_value = orchestrator_metrics.metric_value + excluded.metric_value,
                    updated_at = excluded.updated_at
                """,
                (metric_key, amount, now),
            )

    def _metrics_snapshot(self) -> dict[str, Any]:
        now = utc_now()
        stale_before = _iso_offset(now, -LEASE_TTL_SECONDS)
        with self._connection() as conn:
            metric_rows = conn.execute(
                "SELECT metric_key, metric_value, updated_at FROM orchestrator_metrics ORDER BY metric_key ASC"
            ).fetchall()
            churn_last_hour = conn.execute(
                "SELECT COUNT(*) FROM orchestrator_run_events WHERE created_at >= ?1",
                (_iso_offset(now, -3600),),
            ).fetchone()[0]
            stuck_runs = conn.execute(
                f"""
                SELECT COUNT(*) FROM orchestrator_runs
                WHERE status IN ({",".join(repr(status) for status in sorted(OPEN_RUN_STATUSES))})
                  AND last_heartbeat_at < ?1
                """,
                (stale_before,),
            ).fetchone()[0]
        metrics = {row["metric_key"]: row["metric_value"] for row in metric_rows}
        metrics["event_churn_last_hour"] = churn_last_hour
        metrics["stuck_runs"] = stuck_runs
        return metrics

    def _check_invariants(self) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        with self._connection() as conn:
            duplicate_runs = conn.execute(
                f"""
                SELECT issue_url, COUNT(*) AS count
                FROM orchestrator_runs
                WHERE status IN ({",".join(repr(status) for status in sorted(OPEN_RUN_STATUSES))})
                GROUP BY issue_url
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            orphan_leases = conn.execute(
                """
                SELECT l.issue_url, l.owner_id
                FROM orchestrator_leases l
                LEFT JOIN orchestrator_issue_snapshots s ON s.issue_url = l.issue_url
                WHERE s.issue_url IS NULL
                """
            ).fetchall()
        for row in duplicate_runs:
            violations.append(
                {
                    "invariant": "one_open_run_per_issue",
                    "status": "failed",
                    "issue_url": row["issue_url"],
                    "count": row["count"],
                }
            )
        for row in orphan_leases:
            violations.append(
                {
                    "invariant": "lease_requires_issue_snapshot",
                    "status": "failed",
                    "issue_url": row["issue_url"],
                    "owner_id": row["owner_id"],
                }
            )
        return violations

    def _replay_snapshot(self, *, issue_url: str | None = None) -> dict[str, Any]:
        with self._connection() as conn:
            if issue_url:
                event_rows = conn.execute(
                    """
                    SELECT event_kind, summary, created_at
                    FROM orchestrator_run_events
                    WHERE issue_url = ?1
                    ORDER BY id DESC
                    LIMIT 10
                    """,
                    (issue_url,),
                ).fetchall()
                checkpoint_rows = conn.execute(
                    """
                    SELECT checkpoint_key, checkpoint_type, updated_at
                    FROM orchestrator_checkpoints
                    WHERE issue_url = ?1
                    ORDER BY updated_at DESC
                    LIMIT 5
                    """,
                    (issue_url,),
                ).fetchall()
            else:
                event_rows = conn.execute(
                    """
                    SELECT event_kind, summary, created_at
                    FROM orchestrator_run_events
                    ORDER BY id DESC
                    LIMIT 10
                    """
                ).fetchall()
                checkpoint_rows = conn.execute(
                    """
                    SELECT checkpoint_key, checkpoint_type, updated_at
                    FROM orchestrator_checkpoints
                    ORDER BY updated_at DESC
                    LIMIT 5
                    """
                ).fetchall()
        return {
            "events": [_row_dict(row) for row in event_rows],
            "checkpoints": [_row_dict(row) for row in checkpoint_rows],
        }

    def _classify_issue_failure(self, *, issue_url: str | None = None) -> dict[str, Any]:
        if not issue_url:
            return {"category": "idle", "reason": "no_active_issue"}
        with self._connection() as conn:
            run_row = conn.execute(
                """
                SELECT run_id, status, phase
                FROM orchestrator_runs
                WHERE issue_url = ?1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (issue_url,),
            ).fetchone()
        conditions = self._issue_conditions(issue_url)
        for key in (
            "human_approval_required",
            "workspace_healthy",
            "dependencies_resolved",
            "verification_passing",
            "planning_satisfied",
            "ready_for_execution",
        ):
            data = conditions.get(key)
            if not data:
                continue
            if key == "human_approval_required":
                if bool(data.get("status")):
                    return {
                        "category": key,
                        "reason": _as_text(data.get("reason")),
                        "message": _as_text(data.get("message")),
                        "run": _row_dict(run_row),
                    }
                continue
            if not bool(data.get("status")):
                return {
                    "category": key,
                    "reason": _as_text(data.get("reason")),
                    "message": _as_text(data.get("message")),
                    "run": _row_dict(run_row),
                }
        return {"category": "healthy", "reason": None, "run": _row_dict(run_row)}

    def _issue_conditions(self, issue_url: str | None) -> dict[str, dict[str, Any]]:
        if not issue_url:
            return {}
        with self._connection() as conn:
            condition_rows = conn.execute(
                """
                SELECT condition_key, status, reason, message
                FROM orchestrator_conditions
                WHERE issue_url = ?1
                """,
                (issue_url,),
            ).fetchall()
        conditions: dict[str, dict[str, Any]] = {}
        for row in condition_rows:
            data = _row_dict(row) or {}
            key = _as_text(data.get("condition_key")) or ""
            if key:
                conditions[key] = data
        return conditions

    def _mutation_snapshot(self, *, issue_url: str | None = None) -> dict[str, Any]:
        if not issue_url:
            return {"pending": [], "last_event": None}
        with self._connection() as conn:
            event_row = conn.execute(
                """
                SELECT event_kind, summary, payload_json, created_at
                FROM orchestrator_run_events
                WHERE issue_url = ?1
                  AND event_kind IN ('followup_requested', 'blocked')
                ORDER BY id DESC
                LIMIT 1
                """,
                (issue_url,),
            ).fetchone()
        event = _row_dict(event_row) or {}
        payload = _parse_json(event.get("payload_json"))
        mutation = (
            ((payload.get("payload") or {}).get("mutation"))
            if isinstance(payload.get("payload"), dict)
            else None
        )
        pending = [mutation] if isinstance(mutation, dict) else []
        return {
            "pending": pending,
            "last_event": {
                "event_kind": _as_text(event.get("event_kind")),
                "summary": _as_text(event.get("summary")),
                "created_at": _as_text(event.get("created_at")),
            }
            if event
            else None,
        }


def _primary_action(kanban_state: dict[str, Any]) -> str:
    action = _primary_action_packet(kanban_state)
    return _as_text((action or {}).get("action")) or "sync_issue"


def _primary_action_packet(kanban_state: dict[str, Any]) -> dict[str, Any] | None:
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    actions = reconcile.get("actions")
    if not isinstance(actions, list):
        return None
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = _as_text(action.get("action"))
        if action_name and action_name != "sync_issue":
            return action
    return next((item for item in actions if isinstance(item, dict)), None)


def _handoff_summary(kanban_state: dict[str, Any]) -> str:
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    stage_results = reconcile.get("stage_results") if isinstance(reconcile.get("stage_results"), dict) else {}
    dispatch = stage_results.get("dispatch") if isinstance(stage_results.get("dispatch"), dict) else {}
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    title = _as_text((active_issue or {}).get("title")) or _as_text((active_issue or {}).get("url")) or "no active issue"
    decision = _as_text(dispatch.get("decision")) or "idle"
    summary = _as_text(dispatch.get("summary"))
    return f"{title}: {decision}. {summary}".strip()


def _action_dedupe_suffix(kanban_state: dict[str, Any]) -> str:
    packet = _primary_action_packet(kanban_state)
    if packet:
        return _as_text(packet.get("idempotency_key")) or _as_text(packet.get("action")) or "action"
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    return _as_text(reconcile.get("actual_state")) or "state"


def _owner_id(*, project: str, runner_id: str) -> str:
    return f"runner:{project}:{runner_id}"


def _project_scope(project: str) -> str:
    return f"project://{project}"


def _run_phase_to_kanban_phase(phase: str | None, status: str | None) -> str:
    normalized_status = _as_text(status) or ""
    if normalized_status in {"blocked"}:
        return "blocked"
    if normalized_status in {"completed"}:
        return "done"
    normalized_phase = _as_text(phase) or ""
    if normalized_phase in {"verify", "verification", "review"}:
        return "review"
    if normalized_phase in {"closeout", "done"}:
        return "done"
    if normalized_phase in {"implement", "planning", "executing", "execution"}:
        return "executing"
    return "selecting"


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)


def _parse_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _metadata_text(payload: dict[str, Any], key: str) -> str | None:
    values = _metadata_values(payload, key)
    if values:
        return values[0]
    return None


def _metadata_values(payload: dict[str, Any], *keys: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value is None:
            normalized_target = key.replace("_", "").replace(" ", "").lower()
            for candidate_key, candidate_value in payload.items():
                normalized_key = str(candidate_key).replace("_", "").replace(" ", "").lower()
                if normalized_key == normalized_target:
                    value = candidate_value
                    break
        if isinstance(value, list):
            values.extend(item for item in (_as_text(entry) for entry in value) if item and item.lower() != "none")
            continue
        text = _as_text(value)
        if text and text.lower() != "none":
            values.append(text)
    return list(dict.fromkeys(values))


def _one_or_many(values: list[str]) -> str | list[str] | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _coalesce_text(*values: Any) -> str | None:
    for value in values:
        text = _as_text(value)
        if text is not None:
            return text
    return None


def _iso_offset(timestamp: str, seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
