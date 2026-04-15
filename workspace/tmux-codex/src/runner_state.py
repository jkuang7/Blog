"""Runner state schema and file helpers for deterministic Codex loops."""

from __future__ import annotations

import json
import os
import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_IMPLEMENTATION_PLAN = [
    "Confirm scope and constraints for the current objective.",
    "Execute one active seam with validation evidence.",
    "Run run_gates and only mark done when all checks pass.",
]
DONE_NEXT_TASK_TEXT = "No open seams remain in SEAMS.json."
RUNNER_PHASES = ("discover", "implement", "verify", "closeout")
DEFAULT_PHASE_BUDGET_MINUTES = 45
DEFAULT_TASK_SOURCE = "github_mcp_project_issues"
KANBAN_CONDITION_KEYS = (
    "ready_for_execution",
    "planning_satisfied",
    "dependencies_resolved",
    "workspace_healthy",
    "verification_passing",
    "human_approval_required",
)
RECONCILE_STAGE_NAMES = ("ingest", "classify", "select", "recover", "dispatch")


def workspace_home(dev: str) -> Path:
    override = os.environ.get("WORKSPACE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path(dev) / "workspace").resolve()


def codex_home(dev: str) -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def worktrees_home(dev: str) -> Path:
    return (Path(dev) / "worktrees").resolve()


@dataclass(frozen=True)
class RunnerStatePaths:
    """Runner-scoped file layout in <project_root>/.memory/."""

    memory_dir: Path
    runner_dir: Path
    runner_runtime_dir: Path
    runner_locks_dir: Path
    project_prd_file: Path
    legacy_refactor_status_file: Path
    runner_handoff_file: Path
    gates_file: Path
    state_file: Path
    ledger_file: Path
    done_lock: Path
    stop_lock: Path
    active_lock: Path
    enable_pending: Path
    clear_pending: Path
    hooks_log: Path
    objective_json: Path
    seams_json: Path
    gaps_json: Path
    prd_json: Path
    tasks_json: Path
    kanban_state_json: Path
    runner_parity_json: Path
    exec_context_json: Path
    active_backlog_json: Path
    runner_status_json: Path
    action_queue_json: Path
    reconcile_result_json: Path
    control_db: Path
    graph_dir: Path
    dep_graph_json: Path
    graph_active_slice_json: Path
    graph_boundaries_json: Path
    graph_hotspots_json: Path
    cycle_prepared_file: Path
    task_intake_file: Path
    runner_log: Path
    runners_log: Path


def utc_now() -> str:
    """Return UTC timestamp in ISO8601 Z form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_runner_state_paths(dev: str, project: str, runner_id: str) -> RunnerStatePaths:
    """Build all runner-scoped file paths."""
    project_root = Path(dev) / "Repos" / project
    return build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )


def build_runner_state_paths_for_root(
    project_root: Path,
    dev: str,
    project: str,
    runner_id: str,
) -> RunnerStatePaths:
    """Build runner paths anchored to an explicit project root."""
    memory_dir = Path(dev) / "Repos" / project / ".memory"
    if project_root:
        memory_dir = project_root / ".memory"
    runner_dir = memory_dir / "runner"
    runner_runtime_dir = runner_dir / "runtime"
    runner_locks_dir = runner_dir / "locks"
    graph_dir = runner_dir / "graph"
    logs_dir = codex_home(dev) / "logs" / "runners"

    return RunnerStatePaths(
        memory_dir=memory_dir,
        runner_dir=runner_dir,
        runner_runtime_dir=runner_runtime_dir,
        runner_locks_dir=runner_locks_dir,
        project_prd_file=memory_dir / "PRD.md",
        legacy_refactor_status_file=memory_dir / "REFRACTOR_STATUS.md",
        runner_handoff_file=runner_dir / "RUNNER_HANDOFF.md",
        gates_file=memory_dir / "gates.sh",
        state_file=runner_runtime_dir / "RUNNER_STATE.json",
        ledger_file=runner_runtime_dir / "RUNNER_LEDGER.ndjson",
        done_lock=runner_locks_dir / "RUNNER_DONE.lock",
        stop_lock=runner_locks_dir / "RUNNER_STOP.lock",
        active_lock=runner_locks_dir / "RUNNER_ACTIVE.lock",
        enable_pending=runner_locks_dir / "RUNNER_ENABLE.pending.json",
        clear_pending=runner_locks_dir / "RUNNER_CLEAR.pending.json",
        hooks_log=runner_runtime_dir / "RUNNER_HOOKS.ndjson",
        objective_json=runner_dir / "OBJECTIVE.json",
        seams_json=runner_dir / "SEAMS.json",
        gaps_json=runner_dir / "GAPS.json",
        prd_json=runner_dir / "PRD.json",
        tasks_json=runner_dir / "TASKS.json",
        kanban_state_json=runner_dir / "KANBAN_STATE.json",
        runner_parity_json=runner_dir / "RUNNER_PARITY.json",
        exec_context_json=runner_dir / "RUNNER_EXEC_CONTEXT.json",
        active_backlog_json=runner_dir / "RUNNER_ACTIVE_BACKLOG.json",
        runner_status_json=runner_dir / "RUNNER_STATUS.json",
        action_queue_json=runner_runtime_dir / "RUNNER_ACTION_QUEUE.json",
        reconcile_result_json=runner_runtime_dir / "RUNNER_RECONCILE_RESULT.json",
        control_db=runner_runtime_dir / "RUNNER_CONTROL.sqlite3",
        graph_dir=graph_dir,
        dep_graph_json=graph_dir / "RUNNER_DEP_GRAPH.json",
        graph_active_slice_json=graph_dir / "RUNNER_GRAPH_ACTIVE_SLICE.json",
        graph_boundaries_json=graph_dir / "RUNNER_GRAPH_BOUNDARIES.json",
        graph_hotspots_json=graph_dir / "RUNNER_GRAPH_HOTSPOTS.json",
        cycle_prepared_file=runner_runtime_dir / "RUNNER_CYCLE_PREPARED.json",
        task_intake_file=runner_dir / "RUNNER_TASK_INTAKE.json",
        runner_log=logs_dir / f"runner-{project}.log",
        runners_log=logs_dir / "runners.log",
    )


def default_runner_state(project: str, runner_id: str) -> dict[str, Any]:
    """Create initial canonical runner state."""
    now = utc_now()
    return {
        "runner_id": runner_id,
        "project": project,
        "status": "init",
        "enabled": False,
        "session_id": None,
        "iteration": 0,
        "current_step": "",
        "last_hil_decision": None,
        "dod_status": "in_progress",
        "current_goal": "Seed a concrete objective from the latest user request and repo evidence.",
        "last_iteration_summary": "",
        "completed_recent": [],
        "next_seam": "Seed the first concrete seam slice from the active objective.",
        "next_seam_reason": "No prior iteration update exists yet.",
        "next_task": "Seed the first concrete task slice from the active objective.",
        "next_task_reason": "No prior iteration update exists yet.",
        "objective_id": None,
        "active_seam_id": None,
        "next_seam_id": None,
        "next_task_id": None,
        "current_seam_id": None,
        "current_task_id": None,
        "seam_selection_reason": None,
        "task_selection_reason": None,
        "state_revision": 0,
        "project_root": None,
        "target_branch": None,
        "blockers": [],
        "done_candidate": False,
        "done_gate_status": "pending",
        "current_phase": "discover",
        "phase_status": "active",
        "phase_started_at": now,
        "phase_budget_minutes": DEFAULT_PHASE_BUDGET_MINUTES,
        "phase_context_digest": None,
        "git_branch": None,
        "git_head": None,
        "git_worktree": None,
        "implementation_plan": list(DEFAULT_IMPLEMENTATION_PLAN),
        "runtime_policy": {
            "runner_mode": "exec",
            "session_strategy": "fresh_session",
            "task_source": DEFAULT_TASK_SOURCE,
            "kanban_enabled": True,
            "completion_policy": "tasks_done_and_gates_green",
        },
        "updated_at": now,
    }


def default_kanban_state(project: str) -> dict[str, Any]:
    """Create initial ticket-native kanban continuity state."""
    now = utc_now()
    return {
        "version": 1,
        "project": project,
        "mode": "ticket_native",
        "phase": "selecting",
        "selection_scope": {
            "owner": "jkuang7",
            "project_number": 5,
            "workspace_mode": True,
        },
        "active_issue": None,
        "intake": {
            "issue_class": None,
            "complexity": None,
            "routing": None,
            "split_required": False,
            "clarification_required": False,
            "enhance_required": False,
            "last_result": None,
        },
        "active_checkout": {
            "repo_root": None,
            "worktree": None,
            "branch": None,
        },
        "board": {
            "last_known_status": None,
            "blocked_status_supported": None,
            "blocked_fallback_status": None,
            "schema_mismatch": False,
        },
        "dependencies": {
            "depends_on": [],
            "blocked_by": [],
            "children": [],
            "follow_ups": [],
            "merge_into": None,
        },
        "blocker": {
            "is_blocked": False,
            "category": None,
            "reason": "",
            "needs": "",
            "resume_from": "",
            "external": False,
        },
        "loop": {
            "iteration": 0,
            "last_transition": None,
            "last_comment_kind": None,
            "continue_until": "board_complete_or_all_blocked",
            "resume_source": "runner_state",
        },
        "telegram": {
            "bridge": None,
            "chat_id": None,
            "thread_id": None,
            "session_key": None,
        },
        "operator": {
            "pause_requested": False,
            "resume_requested": False,
            "split_override": False,
            "resume_run_id": None,
            "approval_required": False,
            "approval_reason": None,
        },
        "conditions": {
            "ready_for_execution": {
                "status": False,
                "reason": "no_active_issue",
                "message": "No active issue is selected yet.",
            },
            "planning_satisfied": {
                "status": False,
                "reason": "no_active_issue",
                "message": "Planning cannot start without an active issue.",
            },
            "dependencies_resolved": {
                "status": True,
                "reason": None,
                "message": "No dependency blockers are currently recorded.",
            },
            "workspace_healthy": {
                "status": False,
                "reason": "no_active_checkout",
                "message": "The active issue does not have a complete worktree and branch context yet.",
            },
            "verification_passing": {
                "status": True,
                "reason": None,
                "message": "No verification blocker is currently recorded.",
            },
            "human_approval_required": {
                "status": False,
                "reason": None,
                "message": "No operator approval gate is currently active.",
            },
        },
        "drift": {
            "github": {"detected": False, "reason": None},
            "workspace": {"detected": False, "reason": None},
            "operator_override": {"detected": False, "reason": None},
        },
        "reconcile": {
            "desired_state": "advance_active_issue",
            "actual_state": "selecting",
            "gap_reason": "no_active_issue",
            "stage_results": {
                "ingest": {"decision": "capture_current_state", "summary": "No active issue is selected yet."},
                "classify": {"decision": "non_executable", "summary": "Selection must happen before execution."},
                "select": {"decision": "select_next_issue", "summary": "The queue has not chosen an active issue."},
                "recover": {"decision": "no_recovery_needed", "summary": "No prior execution state exists yet."},
                "dispatch": {"decision": "wait_for_selection", "summary": "Dispatch is blocked until an issue is selected."},
            },
            "actions": [
                {
                    "action": "sync_issue",
                    "reason": "runner starts by syncing the remote issue queue",
                },
                {
                    "action": "select_next_issue",
                    "reason": "no active issue is selected yet",
                },
            ],
            "last_reconciled_at": now,
        },
        "updated_at": now,
    }


def write_runner_status_snapshot(
    paths: RunnerStatePaths,
    state: dict[str, Any],
    kanban_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the compact status contract consumed by remote control surfaces."""
    from .runner_control import reconcile_control_plane

    kanban_state = kanban_state if isinstance(kanban_state, dict) else {}
    kanban_state = reconcile_control_plane(
        paths=paths,
        state=state,
        kanban_state=kanban_state,
        enable_pending_exists=paths.enable_pending.exists(),
    )
    runtime_policy = state.get("runtime_policy") if isinstance(state.get("runtime_policy"), dict) else {}
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    active_checkout = (
        kanban_state.get("active_checkout") if isinstance(kanban_state.get("active_checkout"), dict) else {}
    )
    loop_state = kanban_state.get("loop") if isinstance(kanban_state.get("loop"), dict) else {}
    intake = kanban_state.get("intake") if isinstance(kanban_state.get("intake"), dict) else {}
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    drift = kanban_state.get("drift") if isinstance(kanban_state.get("drift"), dict) else {}
    conditions = kanban_state.get("conditions") if isinstance(kanban_state.get("conditions"), dict) else {}
    control = kanban_state.get("control") if isinstance(kanban_state.get("control"), dict) else {}
    write_json(paths.kanban_state_json, kanban_state)
    action_queue_payload = build_action_queue_payload(
        project=str(state.get("project") or "").strip() or None,
        runner_id=str(state.get("runner_id") or "").strip() or None,
        kanban_state=kanban_state,
    )
    reconcile_result_payload = build_reconcile_result_payload(
        project=str(state.get("project") or "").strip() or None,
        runner_id=str(state.get("runner_id") or "").strip() or None,
        kanban_state=kanban_state,
    )
    write_json(paths.action_queue_json, action_queue_payload)
    write_json(paths.reconcile_result_json, reconcile_result_payload)

    payload = {
        "project": str(state.get("project") or "").strip() or None,
        "runner_id": str(state.get("runner_id") or "").strip() or None,
        "status": str(state.get("status") or "").strip() or None,
        "current_phase": str(state.get("current_phase") or "").strip() or None,
        "phase_status": str(state.get("phase_status") or "").strip() or None,
        "done_gate_status": str(state.get("done_gate_status") or "").strip() or None,
        "current_goal": str(state.get("current_goal") or "").strip() or None,
        "next_task_id": str(state.get("next_task_id") or "").strip() or None,
        "next_task": str(state.get("next_task") or "").strip() or None,
        "next_task_reason": str(state.get("next_task_reason") or "").strip() or None,
        "project_root": str(state.get("project_root") or "").strip() or None,
        "git_branch": str(state.get("git_branch") or "").strip() or None,
        "runtime_policy": {
            "runner_mode": str(runtime_policy.get("runner_mode") or "").strip() or None,
            "task_source": str(runtime_policy.get("task_source") or "").strip() or None,
            "kanban_enabled": bool(runtime_policy.get("kanban_enabled", False)),
            "completion_policy": str(runtime_policy.get("completion_policy") or "").strip() or None,
        },
        "architecture": {
            "work_definition": "github_mcp_projects_issues",
            "execution_engine": "tmux-codex",
            "operator_shell": "telecodex",
            "durable_state": "sqlite_control_plane",
            "workflow_authority": "deterministic_reconcile_controller",
        },
        "blockers": [str(item).strip() for item in state.get("blockers", []) if str(item).strip()][:5],
        "kanban": {
            "mode": str(kanban_state.get("mode") or "").strip() or None,
            "phase": str(kanban_state.get("phase") or "").strip() or None,
            "continue_until": str(loop_state.get("continue_until") or "").strip() or None,
            "active_issue_url": str(active_issue.get("url") or "").strip() or None,
            "active_issue_repo": str(active_issue.get("repo") or "").strip() or None,
            "issue_class": str(intake.get("issue_class") or "").strip() or None,
            "complexity": str(intake.get("complexity") or "").strip() or None,
            "routing": str(intake.get("routing") or "").strip() or None,
            "checkout_root": str(active_checkout.get("repo_root") or "").strip() or None,
            "checkout_worktree": str(active_checkout.get("worktree") or "").strip() or None,
            "checkout_branch": str(active_checkout.get("branch") or "").strip() or None,
        },
        "conditions": {
            key: bool((conditions.get(key) or {}).get("status", False))
            for key in KANBAN_CONDITION_KEYS
        },
        "unmet_conditions": [
            key
            for key in KANBAN_CONDITION_KEYS
            if not bool((conditions.get(key) or {}).get("status", False))
        ],
        "reconcile": {
            "desired_state": str(reconcile.get("desired_state") or "").strip() or None,
            "actual_state": str(reconcile.get("actual_state") or "").strip() or None,
            "gap_reason": str(reconcile.get("gap_reason") or "").strip() or None,
            "next_actions": [
                str(item.get("action") or "").strip()
                for item in action_queue_payload.get("actions", [])
                if isinstance(item, dict) and str(item.get("action") or "").strip()
            ][:5],
        },
        "control": {
            "failure_classification": (
                ((control.get("diagnostics") or {}).get("failure_classification"))
                if isinstance(control.get("diagnostics"), dict)
                else None
            ),
            "metrics": (
                ((control.get("diagnostics") or {}).get("metrics"))
                if isinstance(control.get("diagnostics"), dict)
                else {}
            ),
            "invariant_violations": len(
                ((control.get("diagnostics") or {}).get("invariants"))
                if isinstance((control.get("diagnostics") or {}).get("invariants"), list)
                else []
            ),
        },
        "drift": {
            "github": bool((drift.get("github") or {}).get("detected", False)),
            "workspace": bool((drift.get("workspace") or {}).get("detected", False)),
            "operator_override": bool((drift.get("operator_override") or {}).get("detected", False)),
        },
        "updated_at": utc_now(),
    }
    write_json(paths.runner_status_json, payload)
    return payload


def coerce_runner_phase(value: Any, *, default: str = "implement") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in RUNNER_PHASES:
        return normalized
    return default


def ensure_memory_dir(paths: RunnerStatePaths) -> None:
    """Create memory and log directories."""
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.runner_dir.mkdir(parents=True, exist_ok=True)
    paths.runner_runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.runner_locks_dir.mkdir(parents=True, exist_ok=True)
    paths.graph_dir.mkdir(parents=True, exist_ok=True)
    paths.runner_log.parent.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_runner_layout(paths)


def _migrate_legacy_runner_layout(paths: RunnerStatePaths) -> None:
    """Move legacy runner files into canonical runtime/locks subdirs."""
    legacy_moves = {
        paths.memory_dir / "RUNNER_DONE.lock": paths.done_lock,
        paths.memory_dir / "RUNNER_STOP.lock": paths.stop_lock,
        paths.memory_dir / "RUNNER_ACTIVE.lock": paths.active_lock,
        paths.runner_dir / "RUNNER_STATE.json": paths.state_file,
        paths.runner_dir / "RUNNER_LEDGER.ndjson": paths.ledger_file,
        paths.runner_dir / "RUNNER_HOOKS.ndjson": paths.hooks_log,
        paths.runner_dir / "RUNNER_CYCLE_PREPARED.json": paths.cycle_prepared_file,
        paths.runner_dir / "RUNNER_ENABLE.pending.json": paths.enable_pending,
        paths.runner_dir / "RUNNER_CLEAR.pending.json": paths.clear_pending,
    }
    for legacy_path, canonical_path in legacy_moves.items():
        if not legacy_path.exists() or legacy_path == canonical_path:
            continue
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        if canonical_path.exists():
            legacy_path.unlink(missing_ok=True)
            continue
        os.replace(legacy_path, canonical_path)


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write text content to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically."""
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any] | None:
    """Read JSON file if present and valid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    coerced: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                coerced.append(stripped)
    return coerced


def _coerce_plan_list(value: Any) -> list[str]:
    items = _coerce_str_list(value)
    if items:
        return items
    return list(DEFAULT_IMPLEMENTATION_PLAN)


def _coerce_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_completion_fields(normalized: dict[str, Any]) -> None:
    status = str(normalized.get("status", "")).strip().lower()
    normalized["status"] = status or str(normalized.get("status", "")).strip() or "init"

    if status == "done":
        normalized["current_phase"] = "closeout"
        normalized["phase_status"] = "handoff_ready"
        normalized["done_gate_status"] = "passed"
        normalized["done_candidate"] = True
        normalized["dod_status"] = "done"
        normalized["blockers"] = []
        normalized["next_seam_id"] = None
        normalized["next_task_id"] = None
        normalized["next_seam"] = DONE_NEXT_TASK_TEXT
        normalized["next_task"] = DONE_NEXT_TASK_TEXT
        normalized["next_seam_reason"] = DONE_NEXT_TASK_TEXT
        normalized["next_task_reason"] = DONE_NEXT_TASK_TEXT
        return

    normalized["dod_status"] = "in_progress"


def normalize_runner_state(
    state: dict[str, Any],
    project: str,
    runner_id: str,
    runner_mode: str = "exec",
    session_strategy: str = "fresh_session",
) -> tuple[dict[str, Any], bool]:
    """Backfill/normalize runner state shape for forward-compatible loops."""
    original = json.dumps(state, sort_keys=True)
    defaults = default_runner_state(project=project, runner_id=runner_id)
    normalized = defaults.copy()
    for key in defaults:
        if key in state:
            normalized[key] = state[key]
    normalized["project"] = project
    normalized["runner_id"] = runner_id

    normalized["completed_recent"] = _coerce_str_list(normalized.get("completed_recent"))
    normalized["blockers"] = _coerce_str_list(normalized.get("blockers"))
    normalized["implementation_plan"] = _coerce_plan_list(normalized.get("implementation_plan"))
    normalized["current_goal"] = str(normalized.get("current_goal", defaults["current_goal"])).strip() or defaults[
        "current_goal"
    ]
    normalized["last_iteration_summary"] = str(
        normalized.get("last_iteration_summary", defaults["last_iteration_summary"])
    ).strip()
    raw_active_seam_id = str(state.get("active_seam_id", "")).strip() if "active_seam_id" in state else ""
    raw_next_seam_id = str(state.get("next_seam_id", "")).strip() if "next_seam_id" in state else ""
    raw_next_task_id = str(state.get("next_task_id", "")).strip() if "next_task_id" in state else ""
    raw_next_seam = str(state.get("next_seam", "")).strip() if "next_seam" in state else ""
    raw_next_task = str(state.get("next_task", "")).strip() if "next_task" in state else ""
    raw_next_seam_reason = str(state.get("next_seam_reason", "")).strip() if "next_seam_reason" in state else ""
    raw_next_task_reason = str(state.get("next_task_reason", "")).strip() if "next_task_reason" in state else ""
    prefer_legacy_next_task_id = bool(raw_next_task_id) and (
        not raw_next_seam_id
        or raw_next_seam_id == raw_active_seam_id
    )
    prefer_legacy_next_task = bool(raw_next_task) and (
        prefer_legacy_next_task_id
        or not raw_next_seam
        or raw_next_seam == defaults["next_seam"]
    )
    prefer_legacy_next_task_reason = bool(raw_next_task_reason) and (
        prefer_legacy_next_task_id
        or not raw_next_seam_reason
        or raw_next_seam_reason == defaults["next_seam_reason"]
    )
    next_seam = str(
        raw_next_task if prefer_legacy_next_task else (raw_next_seam or raw_next_task or defaults["next_seam"])
    ).strip() or defaults["next_seam"]
    next_seam_reason = str(
        raw_next_task_reason
        if prefer_legacy_next_task_reason
        else (raw_next_seam_reason or raw_next_task_reason or defaults["next_seam_reason"])
    ).strip() or defaults["next_seam_reason"]
    normalized["next_seam"] = next_seam
    normalized["next_task"] = next_seam
    normalized["next_seam_reason"] = next_seam_reason
    normalized["next_task_reason"] = next_seam_reason

    for field in (
        "objective_id",
        "active_seam_id",
        "next_seam_id",
        "next_task_id",
        "current_seam_id",
        "current_task_id",
        "seam_selection_reason",
        "task_selection_reason",
    ):
        value = normalized.get(field)
        normalized[field] = str(value).strip() if isinstance(value, str) and str(value).strip() else None

    normalized["next_seam_id"] = (
        raw_next_task_id
        if prefer_legacy_next_task_id
        else (normalized.get("next_seam_id") or normalized.get("next_task_id"))
    )
    normalized["next_task_id"] = normalized.get("next_seam_id")
    normalized["current_seam_id"] = (
        normalized.get("current_seam_id")
        or normalized.get("active_seam_id")
        or normalized.get("current_task_id")
    )
    normalized["current_task_id"] = normalized.get("current_seam_id")
    normalized["seam_selection_reason"] = normalized.get("seam_selection_reason") or normalized.get("task_selection_reason")
    normalized["task_selection_reason"] = normalized.get("seam_selection_reason")

    root = normalized.get("project_root")
    normalized["project_root"] = str(root).strip() if isinstance(root, str) and str(root).strip() else None

    target_branch = normalized.get("target_branch")
    normalized["target_branch"] = (
        str(target_branch).strip() if isinstance(target_branch, str) and str(target_branch).strip() else None
    )

    try:
        normalized["state_revision"] = int(normalized.get("state_revision", 0))
    except (TypeError, ValueError):
        normalized["state_revision"] = 0

    normalized["done_candidate"] = bool(normalized.get("done_candidate", False))
    normalized["current_phase"] = coerce_runner_phase(normalized.get("current_phase"), default="discover")
    phase_status = str(normalized.get("phase_status", "active")).strip().lower()
    if phase_status not in {"active", "handoff_ready", "blocked"}:
        phase_status = "active"
    normalized["phase_status"] = phase_status
    phase_started_at = normalized.get("phase_started_at")
    normalized["phase_started_at"] = (
        str(phase_started_at).strip() if isinstance(phase_started_at, str) and phase_started_at.strip() else defaults["phase_started_at"]
    )
    try:
        phase_budget_minutes = int(normalized.get("phase_budget_minutes", DEFAULT_PHASE_BUDGET_MINUTES))
    except (TypeError, ValueError):
        phase_budget_minutes = DEFAULT_PHASE_BUDGET_MINUTES
    normalized["phase_budget_minutes"] = max(1, phase_budget_minutes)
    phase_context_digest = normalized.get("phase_context_digest")
    normalized["phase_context_digest"] = (
        str(phase_context_digest).strip()
        if isinstance(phase_context_digest, str) and phase_context_digest.strip()
        else None
    )
    branch = normalized.get("git_branch")
    head = normalized.get("git_head")
    worktree = normalized.get("git_worktree")
    normalized["git_branch"] = str(branch).strip() if isinstance(branch, str) and branch.strip() else None
    normalized["git_head"] = str(head).strip() if isinstance(head, str) and head.strip() else None
    normalized["git_worktree"] = str(worktree).strip() if isinstance(worktree, str) and worktree.strip() else None

    done_gate_status = str(normalized.get("done_gate_status", "pending")).strip().lower()
    if done_gate_status not in {"pending", "passed", "failed"}:
        done_gate_status = "pending"
    normalized["done_gate_status"] = done_gate_status
    _normalize_completion_fields(normalized)

    runtime_policy = normalized.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        runtime_policy = {}
    runtime_policy = {
        "runner_mode": str(runtime_policy.get("runner_mode", runner_mode)).replace("-", "_"),
        "session_strategy": str(runtime_policy.get("session_strategy", session_strategy)).replace("-", "_"),
        "task_source": str(runtime_policy.get("task_source", DEFAULT_TASK_SOURCE)).replace("-", "_"),
        "kanban_enabled": bool(runtime_policy.get("kanban_enabled", True)),
        "completion_policy": str(
            runtime_policy.get("completion_policy", "tasks_done_and_gates_green")
        ).replace("-", "_"),
    }
    normalized["runtime_policy"] = runtime_policy

    changed = json.dumps(normalized, sort_keys=True) != original
    return normalized, changed


def append_ndjson(path: Path, event: dict[str, Any]) -> None:
    """Append one JSON event line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def update_state(path: Path, state: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Apply changes and persist state with updated timestamp."""
    state.update(changes)
    state["updated_at"] = utc_now()
    write_json(path, state)
    return state


def load_or_init_state(paths: RunnerStatePaths, project: str, runner_id: str) -> dict[str, Any]:
    """Load canonical state or create it when missing/invalid."""
    state = read_json(paths.state_file)
    if state is None:
        state = default_runner_state(project=project, runner_id=runner_id)
        write_json(paths.state_file, state)
        return state
    normalized, changed = normalize_runner_state(state, project=project, runner_id=runner_id)
    if changed:
        write_json(paths.state_file, normalized)
    return normalized


def load_state_snapshot(paths: RunnerStatePaths, project: str, runner_id: str) -> dict[str, Any]:
    """Load normalized state without persisting compatibility backfills."""
    state = read_json(paths.state_file)
    if state is None:
        return default_runner_state(project=project, runner_id=runner_id)
    normalized, _ = normalize_runner_state(state, project=project, runner_id=runner_id)
    return normalized


def managed_runner_files(paths: RunnerStatePaths) -> list[Path]:
    """Enumerate managed files for clear operations."""
    files = [
        paths.project_prd_file,
        paths.legacy_refactor_status_file,
        paths.runner_handoff_file,
        paths.state_file,
        paths.ledger_file,
        paths.done_lock,
        paths.stop_lock,
        paths.active_lock,
        paths.enable_pending,
        paths.clear_pending,
        paths.hooks_log,
        paths.objective_json,
        paths.seams_json,
        paths.gaps_json,
        paths.prd_json,
        paths.tasks_json,
        paths.kanban_state_json,
        paths.runner_parity_json,
        paths.exec_context_json,
        paths.active_backlog_json,
        paths.runner_status_json,
        paths.action_queue_json,
        paths.reconcile_result_json,
        paths.control_db,
        paths.dep_graph_json,
        paths.graph_active_slice_json,
        paths.graph_boundaries_json,
        paths.graph_hotspots_json,
        paths.cycle_prepared_file,
        paths.task_intake_file,
    ]
    # Keep deterministic order while avoiding duplicate paths in manifest output.
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def normalize_kanban_state(state: dict[str, Any], project: str) -> tuple[dict[str, Any], bool]:
    """Backfill/normalize ticket-native kanban continuity state."""
    original = json.dumps(state, sort_keys=True)
    defaults = default_kanban_state(project)
    normalized = defaults.copy()
    for key in defaults:
        if key in state:
            normalized[key] = state[key]

    normalized["project"] = project
    try:
        normalized["version"] = int(normalized.get("version", 1))
    except (TypeError, ValueError):
        normalized["version"] = 1

    phase = str(normalized.get("phase", "selecting")).strip().lower()
    if phase not in {"selecting", "executing", "blocked", "review", "done", "exhausted"}:
        phase = "selecting"
    normalized["phase"] = phase

    active_issue = normalized.get("active_issue")
    if not isinstance(active_issue, dict):
        active_issue = None
    elif not str(active_issue.get("url", "")).strip():
        active_issue = None
    else:
        active_issue = {
            "url": str(active_issue.get("url", "")).strip(),
            "repo": _coerce_optional_text(active_issue.get("repo")),
            "number": active_issue.get("number"),
            "title": _coerce_optional_text(active_issue.get("title")),
            "issue_class": _coerce_optional_text(active_issue.get("issue_class")),
            "complexity": _coerce_optional_text(active_issue.get("complexity")),
            "routing": _coerce_optional_text(active_issue.get("routing")),
            "parent": _coerce_optional_text(active_issue.get("parent")),
            "blocked_by": _coerce_optional_text(active_issue.get("blocked_by")),
            "unblocks": _coerce_optional_text(active_issue.get("unblocks")),
            "worktree": _coerce_optional_text(active_issue.get("worktree")),
            "branch": _coerce_optional_text(active_issue.get("branch")),
            "depends_on": _coerce_optional_text(active_issue.get("depends_on")),
            "merge_into": _coerce_optional_text(active_issue.get("merge_into")),
            "resume_from": _coerce_optional_text(active_issue.get("resume_from")),
        }
    normalized["active_issue"] = active_issue

    for key in ("active_checkout", "board", "dependencies", "blocker", "loop", "telegram", "selection_scope", "intake"):
        value = normalized.get(key)
        if not isinstance(value, dict):
            normalized[key] = defaults[key]
    for key in ("operator", "conditions", "drift", "reconcile"):
        value = normalized.get(key)
        if not isinstance(value, dict):
            normalized[key] = defaults[key]

    for list_key in ("depends_on", "blocked_by", "children", "follow_ups"):
        normalized["dependencies"][list_key] = _coerce_str_list(normalized["dependencies"].get(list_key))

    normalized["operator"] = {
        "pause_requested": bool(normalized["operator"].get("pause_requested", False)),
        "resume_requested": bool(normalized["operator"].get("resume_requested", False)),
        "split_override": bool(normalized["operator"].get("split_override", False)),
        "resume_run_id": _coerce_optional_text(normalized["operator"].get("resume_run_id")),
        "approval_required": bool(normalized["operator"].get("approval_required", False)),
        "approval_reason": _coerce_optional_text(normalized["operator"].get("approval_reason")),
    }
    normalized["intake"] = {
        "issue_class": _coerce_optional_text(normalized["intake"].get("issue_class")),
        "complexity": _coerce_optional_text(normalized["intake"].get("complexity")),
        "routing": _coerce_optional_text(normalized["intake"].get("routing")),
        "split_required": bool(normalized["intake"].get("split_required", False)),
        "clarification_required": bool(normalized["intake"].get("clarification_required", False)),
        "enhance_required": bool(normalized["intake"].get("enhance_required", False)),
        "last_result": _coerce_optional_text(normalized["intake"].get("last_result")),
    }

    normalized_conditions: dict[str, dict[str, Any]] = {}
    for key in KANBAN_CONDITION_KEYS:
        raw = normalized["conditions"].get(key) if isinstance(normalized["conditions"], dict) else None
        if not isinstance(raw, dict):
            raw = {}
        normalized_conditions[key] = {
            "status": bool(raw.get("status", defaults["conditions"][key]["status"])),
            "reason": str(raw.get("reason", "")).strip() or None,
            "message": str(raw.get("message", "")).strip() or defaults["conditions"][key]["message"],
        }
    normalized["conditions"] = normalized_conditions

    normalized_drift: dict[str, dict[str, Any]] = {}
    for key in ("github", "workspace", "operator_override"):
        raw = normalized["drift"].get(key) if isinstance(normalized["drift"], dict) else None
        if not isinstance(raw, dict):
            raw = {}
        normalized_drift[key] = {
            "detected": bool(raw.get("detected", False)),
            "reason": str(raw.get("reason", "")).strip() or None,
        }
    normalized["drift"] = normalized_drift

    actions = normalized["reconcile"].get("actions")
    raw_stage_results = normalized["reconcile"].get("stage_results")
    normalized_stage_results: dict[str, dict[str, Any]] = {}
    for key in RECONCILE_STAGE_NAMES:
        raw = raw_stage_results.get(key) if isinstance(raw_stage_results, dict) else None
        if not isinstance(raw, dict):
            raw = {}
        normalized_stage_results[key] = {
            "decision": str(raw.get("decision", "")).strip() or None,
            "summary": str(raw.get("summary", "")).strip() or None,
        }
    normalized["reconcile"] = {
        "desired_state": str(normalized["reconcile"].get("desired_state", "advance_active_issue")).strip()
        or "advance_active_issue",
        "actual_state": str(normalized["reconcile"].get("actual_state", normalized["phase"])).strip() or normalized["phase"],
        "gap_reason": str(normalized["reconcile"].get("gap_reason", "")).strip() or None,
        "stage_results": normalized_stage_results,
        "actions": [
            {
                "action": str(item.get("action") or "").strip(),
                "reason": str(item.get("reason") or "").strip() or None,
                "stage": str(item.get("stage") or "").strip() or None,
                "idempotency_key": str(item.get("idempotency_key") or "").strip() or None,
                "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
            }
            for item in actions
            if isinstance(actions, list)
            for item in [item]
            if isinstance(item, dict) and str(item.get("action") or "").strip()
        ],
        "last_reconciled_at": str(normalized["reconcile"].get("last_reconciled_at", defaults["updated_at"])).strip()
        or defaults["updated_at"],
    }

    normalized["blocker"] = {
        "is_blocked": bool(normalized["blocker"].get("is_blocked", False)),
        "category": str(normalized["blocker"].get("category", "")).strip() or None,
        "reason": str(normalized["blocker"].get("reason", "")).strip(),
        "needs": str(normalized["blocker"].get("needs", "")).strip(),
        "resume_from": str(normalized["blocker"].get("resume_from", "")).strip(),
        "external": bool(normalized["blocker"].get("external", False)),
    }

    normalized["updated_at"] = (
        str(normalized.get("updated_at", defaults["updated_at"])).strip() or defaults["updated_at"]
    )
    changed = json.dumps(normalized, sort_keys=True) != original
    return normalized, changed


def derive_kanban_runtime_view(
    *,
    state: dict[str, Any],
    kanban_state: dict[str, Any],
    enable_pending_exists: bool = False,
) -> dict[str, Any]:
    """Derive level-based conditions, drift, and next actions from current runtime state."""
    project = str(kanban_state.get("project") or state.get("project") or "").strip() or "unknown"
    normalized, _ = normalize_kanban_state(kanban_state, project=project)
    active_issue = normalized.get("active_issue") if isinstance(normalized.get("active_issue"), dict) else None
    active_checkout = normalized.get("active_checkout") if isinstance(normalized.get("active_checkout"), dict) else {}
    blocker = normalized.get("blocker") if isinstance(normalized.get("blocker"), dict) else {}
    dependencies = normalized.get("dependencies") if isinstance(normalized.get("dependencies"), dict) else {}
    board = normalized.get("board") if isinstance(normalized.get("board"), dict) else {}
    operator = normalized.get("operator") if isinstance(normalized.get("operator"), dict) else {}
    intake = normalized.get("intake") if isinstance(normalized.get("intake"), dict) else {}

    git_branch = str(state.get("git_branch") or "").strip()
    git_worktree = str(state.get("git_worktree") or "").strip()
    checkout_branch = str(active_checkout.get("branch") or "").strip()
    checkout_worktree = str(active_checkout.get("worktree") or "").strip()
    checkout_root = str(active_checkout.get("repo_root") or "").strip()
    phase = str(normalized.get("phase") or "selecting").strip() or "selecting"

    has_active_issue = active_issue is not None
    issue_parent = str((active_issue or {}).get("parent") or "").strip()
    issue_blocked_by = str((active_issue or {}).get("blocked_by") or "").strip()
    issue_unblocks = str((active_issue or {}).get("unblocks") or "").strip()
    issue_depends_on = str((active_issue or {}).get("depends_on") or "").strip()
    issue_class = str(intake.get("issue_class") or (active_issue or {}).get("issue_class") or "").strip().lower()
    issue_complexity = str(intake.get("complexity") or (active_issue or {}).get("complexity") or "").strip().upper()
    issue_routing = str(intake.get("routing") or (active_issue or {}).get("routing") or "").strip().lower()
    split_required = bool(intake.get("split_required"))
    clarification_required = bool(intake.get("clarification_required"))
    non_executable_issue_class = issue_class in {"coordination", "phase_parent", "umbrella"}
    unresolved_routing = has_active_issue and issue_routing in {"", "unknown", "unresolved"}
    oversized_ticket = issue_complexity in {"L", "XL"}
    split_override = bool(operator.get("split_override"))
    scope_bounded = has_active_issue and not clarification_required and not (oversized_ticket and not split_override)
    target_repo_known = has_active_issue and bool(str((active_issue or {}).get("repo") or "").strip())
    acceptance_clear = has_active_issue and not bool(intake.get("clarification_required"))
    validation_known = has_active_issue and str(blocker.get("needs") or "").strip().lower() != "validation_unknown"
    dependency_state_known = has_active_issue and (
        bool(dependencies.get("depends_on")) or bool(dependencies.get("blocked_by")) or True
    )
    executable_complexity = has_active_issue and not non_executable_issue_class and (
        issue_complexity not in {"XL"} or split_override
    )
    enhance_required = bool(
        has_active_issue
        and (
            bool(intake.get("enhance_required"))
            or non_executable_issue_class
            or (split_required and not split_override)
            or clarification_required
            or unresolved_routing
            or (oversized_ticket and not split_override)
        )
    )
    blocked_by_dependencies = bool(dependencies.get("blocked_by")) or bool(issue_blocked_by) or bool(issue_depends_on)
    has_child_dependencies = bool(dependencies.get("children"))
    executable_child_preferred = bool(has_active_issue and non_executable_issue_class and has_child_dependencies)
    followup_required = bool(
        has_active_issue
        and (
            ((split_required and not split_override) and not dependencies.get("follow_ups"))
            or (bool(blocker.get("is_blocked")) and not dependencies.get("follow_ups"))
        )
    )
    planning_satisfied = has_active_issue and phase not in {"selecting"} and not enhance_required
    workspace_healthy = (not has_active_issue) or bool(checkout_root and checkout_worktree and checkout_branch)
    verification_passing = not (
        bool(blocker.get("is_blocked")) and str(blocker.get("category") or "").strip().lower() == "verification"
    )
    human_approval_required = bool(enable_pending_exists or operator.get("approval_required"))

    github_drift_detected = bool(board.get("schema_mismatch"))
    github_drift_reason = "board_schema_mismatch" if github_drift_detected else None

    workspace_drift_detected = False
    workspace_drift_reason = None
    if has_active_issue and checkout_worktree and git_worktree and checkout_worktree != git_worktree:
        workspace_drift_detected = True
        workspace_drift_reason = "worktree_mismatch"
    elif has_active_issue and checkout_branch and git_branch and checkout_branch != git_branch:
        workspace_drift_detected = True
        workspace_drift_reason = "branch_mismatch"

    operator_drift_detected = bool(operator.get("pause_requested") or operator.get("resume_requested"))
    operator_drift_reason = None
    if operator_drift_detected:
        operator_drift_reason = "operator_override_requested"

    recovery_decision = derive_recovery_decision(
        state=state,
        has_active_issue=has_active_issue,
        workspace_drift_detected=workspace_drift_detected,
        workspace_drift_reason=workspace_drift_reason,
        workspace_healthy=workspace_healthy,
        human_approval_required=human_approval_required,
        operator_drift_detected=operator_drift_detected,
        operator_drift_reason=operator_drift_reason,
        blocker=blocker,
        phase=phase,
    )

    conditions = {
        "ready_for_execution": {
            "status": bool(
                has_active_issue
                and planning_satisfied
                and not blocked_by_dependencies
                and workspace_healthy
                and verification_passing
                and not human_approval_required
                and not bool(blocker.get("is_blocked"))
            ),
            "reason": None,
            "message": "Execution preconditions are satisfied.",
        },
        "planning_satisfied": {
            "status": planning_satisfied,
            "reason": None if planning_satisfied else ("no_active_issue" if not has_active_issue else "phase_not_advanced"),
            "message": "Planning state is ready."
            if planning_satisfied
            else (
                "Planning cannot start without an active issue."
                if not has_active_issue
                else "The issue must be refined before planning can proceed."
            ),
        },
        "dependencies_resolved": {
            "status": not blocked_by_dependencies,
            "reason": None if not blocked_by_dependencies else "blocked_by_dependency",
            "message": "No dependency blockers are active."
            if not blocked_by_dependencies
            else "A dependency blocker must clear before execution can proceed.",
        },
        "workspace_healthy": {
            "status": workspace_healthy and not workspace_drift_detected,
            "reason": None
            if workspace_healthy and not workspace_drift_detected
            else (workspace_drift_reason or ("no_active_checkout" if has_active_issue else None)),
            "message": "Workspace and checkout context match the active issue."
            if workspace_healthy and not workspace_drift_detected
            else (
                "Workspace reality does not match the active issue checkout."
                if workspace_drift_detected
                else "The active issue does not have a complete worktree and branch context yet."
            ),
        },
        "verification_passing": {
            "status": verification_passing,
            "reason": None if verification_passing else "verification_blocked",
            "message": "Verification is currently passing."
            if verification_passing
            else "Verification is currently blocked or failing.",
        },
        "human_approval_required": {
            "status": human_approval_required,
            "reason": "runner_enable_pending"
            if enable_pending_exists
            else (str(operator.get("approval_reason") or "").strip() or "operator_approval_required"),
            "message": "Human approval is currently required before continuing."
            if human_approval_required
            else "No operator approval gate is currently active.",
        },
    }
    if conditions["ready_for_execution"]["status"]:
        conditions["ready_for_execution"]["reason"] = None
    elif not has_active_issue:
        conditions["ready_for_execution"]["reason"] = "no_active_issue"
    elif human_approval_required:
        conditions["ready_for_execution"]["reason"] = "human_approval_required"
    elif enhance_required:
        conditions["ready_for_execution"]["reason"] = "enhance_required"
    elif blocked_by_dependencies:
        conditions["ready_for_execution"]["reason"] = "blocked_by_dependency"
    elif not workspace_healthy or workspace_drift_detected:
        conditions["ready_for_execution"]["reason"] = conditions["workspace_healthy"]["reason"]
    elif not planning_satisfied:
        conditions["ready_for_execution"]["reason"] = "planning_not_satisfied"
    elif not verification_passing:
        conditions["ready_for_execution"]["reason"] = "verification_blocked"
    elif bool(blocker.get("is_blocked")):
        conditions["ready_for_execution"]["reason"] = str(blocker.get("category") or "blocked").strip() or "blocked"
    conditions["ready_for_execution"]["message"] = _condition_message(
        "ready_for_execution",
        status=bool(conditions["ready_for_execution"]["status"]),
        reason=conditions["ready_for_execution"]["reason"],
    )

    drift = {
        "github": {"detected": github_drift_detected, "reason": github_drift_reason},
        "workspace": {"detected": workspace_drift_detected, "reason": workspace_drift_reason},
        "operator_override": {"detected": operator_drift_detected, "reason": operator_drift_reason},
    }

    actions: list[dict[str, Any]] = [{"action": "sync_issue", "reason": "reconcile starts from GitHub state"}]
    if human_approval_required:
        actions.append(
            {
                "action": "wait_for_human_approval",
                "reason": conditions["human_approval_required"]["reason"],
            }
        )
    elif github_drift_detected:
        actions.append({"action": "refresh_issue_snapshot", "reason": github_drift_reason})
    elif operator_drift_detected:
        actions.append({"action": "apply_operator_override", "reason": operator_drift_reason})
    elif not has_active_issue:
        actions.append({"action": "select_next_issue", "reason": "no active issue is selected"})
    elif workspace_drift_detected:
        actions.append({"action": "repair_workspace", "reason": workspace_drift_reason})
    elif not workspace_healthy:
        actions.append({"action": "acquire_worktree", "reason": "active checkout is incomplete"})
    elif executable_child_preferred:
        actions.append({"action": "select_next_issue", "reason": "prefer executable child ticket over parent tracker"})
    elif enhance_required:
        actions.append({"action": "spawn_refinement_agent", "reason": "complex ticket requires enhance before execution"})
    elif followup_required:
        actions.append({"action": "create_followup_ticket", "reason": "blocked or split-worthy work needs a bounded follow-up ticket"})
    elif blocked_by_dependencies:
        actions.append({"action": "write_blocker_comment", "reason": "dependency remains unresolved"})
    elif not planning_satisfied:
        actions.append({"action": "spawn_planning_agent", "reason": "planning artifact is not satisfied"})
    elif not verification_passing:
        actions.append({"action": "spawn_verification_agent", "reason": "verification is failing"})
    elif bool(blocker.get("is_blocked")):
        actions.append(
            {
                "action": "write_blocker_comment",
                "reason": str(blocker.get("category") or "blocked").strip() or "blocked",
            }
        )
    else:
        actions.append({"action": "spawn_execution_agent", "reason": "all execution preconditions are satisfied"})

    normalized["conditions"] = conditions
    normalized["drift"] = drift
    readiness = {
        "scope_is_bounded": {
            "status": scope_bounded,
            "reason": None if scope_bounded else ("split_required" if split_required else "clarification_required"),
        },
        "target_repo_known": {
            "status": target_repo_known,
            "reason": None if target_repo_known else "repo_unknown",
        },
        "acceptance_criteria_clear": {
            "status": acceptance_clear,
            "reason": None if acceptance_clear else "acceptance_unclear",
        },
        "validation_path_known": {
            "status": validation_known,
            "reason": None if validation_known else "validation_unknown",
        },
        "dependency_state_known": {
            "status": dependency_state_known,
            "reason": None if dependency_state_known else "dependencies_unknown",
        },
        "ticket_is_executable": {
            "status": executable_complexity and not enhance_required and not executable_child_preferred,
            "reason": (
                None
                if executable_complexity and not enhance_required and not executable_child_preferred
                else ("prefer_child_ticket" if executable_child_preferred else "enhance_required")
            ),
        },
        "conflicting_lease_absent": {
            "status": True,
            "reason": None,
        },
        "worktree_context_sane": {
            "status": bool(conditions["workspace_healthy"]["status"]),
            "reason": conditions["workspace_healthy"]["reason"],
        },
    }
    stage_results = {
        "ingest": {
            "decision": "state_loaded",
            "summary": "Loaded runner state, kanban state, and workspace metadata.",
        },
        "classify": {
            "decision": (
                "enhance_required"
                if enhance_required
                else ("executable" if has_active_issue else "selection_required")
            ),
            "summary": (
                "Active issue requires enhance/refinement before execution."
                if enhance_required
                else ("Active issue is executable." if has_active_issue else "No active issue is selected yet.")
            ),
        },
        "select": {
            "decision": "keep_active_issue" if has_active_issue else "select_next_issue",
            "summary": "Active issue remains the current frontier." if has_active_issue else "The controller must select the next issue.",
        },
        "recover": {
            "decision": recovery_decision["decision"],
            "summary": recovery_decision["summary"],
        },
        "dispatch": {
            "decision": str(actions[-1].get("action") or "none"),
            "summary": str(actions[-1].get("reason") or "").strip() or "No action selected.",
        },
    }
    normalized["reconcile"] = {
        "desired_state": "advance_active_issue",
        "actual_state": phase,
        "gap_reason": next(
            (
                str(item.get("reason") or "").strip()
                for item in actions
                if str(item.get("action") or "").strip() != "sync_issue" and str(item.get("reason") or "").strip()
            ),
            None,
        ),
        "controller": {
            "active_run_first": has_active_issue,
            "readiness": readiness,
            "selection_policy": {
                "prefer_executable_child": executable_child_preferred,
                "locality_preferred_repo": str((active_issue or {}).get("repo") or "").strip() or None,
            },
            "followup_policy": {
                "required": followup_required,
                "existing_followups": list(dependencies.get("follow_ups") or []),
            },
            "ticket_relations": {
                "parent": issue_parent or None,
                "blocked_by": issue_blocked_by or issue_depends_on or None,
                "unblocks": issue_unblocks or None,
                "merge_into": str((active_issue or {}).get("merge_into") or "").strip() or None,
                "resume_from": str((active_issue or {}).get("resume_from") or "").strip() or None,
            },
            "next_step": str(actions[-1].get("action") or "none"),
        },
        "stage_results": stage_results,
        "actions": normalize_action_packets(
            project=project,
            runner_id=str(state.get("runner_id") or "").strip() or "main",
            phase=phase,
            active_issue=active_issue,
            actions=actions,
        ),
        "last_reconciled_at": utc_now(),
    }
    normalized["updated_at"] = utc_now()
    return normalized


def derive_recovery_decision(
    *,
    state: dict[str, Any],
    has_active_issue: bool,
    workspace_drift_detected: bool,
    workspace_drift_reason: str | None,
    workspace_healthy: bool,
    human_approval_required: bool,
    operator_drift_detected: bool,
    operator_drift_reason: str | None,
    blocker: dict[str, Any],
    phase: str,
) -> dict[str, str | None]:
    """Apply strict recovery precedence: workspace, durable state, structured blocker, then board phase."""
    status = str(state.get("status") or "").strip().lower()
    if workspace_drift_detected:
        return {
            "decision": "repair_workspace",
            "summary": f"Workspace reality wins recovery precedence: {workspace_drift_reason or 'workspace drift detected'}.",
        }
    if has_active_issue and not workspace_healthy:
        return {
            "decision": "block_until_workspace_ready",
            "summary": "Execution remains blocked until the active checkout is complete.",
        }
    if status in {"error", "invalid_gates"}:
        return {
            "decision": "fail_run",
            "summary": f"Durable local run state is `{status}` and requires explicit recovery or teardown.",
        }
    if human_approval_required:
        return {
            "decision": "await_human_approval",
            "summary": "Recovery is paused behind an explicit approval gate.",
        }
    if operator_drift_detected:
        return {
            "decision": "apply_operator_override",
            "summary": f"Operator override must be applied before normal progression: {operator_drift_reason or 'override requested'}.",
        }
    if bool(blocker.get("is_blocked")):
        resume_from = str(blocker.get("resume_from") or "").strip()
        if resume_from:
            return {
                "decision": "resume_from_blocker_metadata",
                "summary": f"Structured blocker metadata requests resume from `{resume_from}`.",
            }
        return {
            "decision": "block_run",
            "summary": "Structured blocker metadata still marks the run as blocked.",
        }
    return {
        "decision": "continue" if phase != "selecting" else "select",
        "summary": "No higher-precedence recovery constraint is active.",
    }


def normalize_action_packets(
    *,
    project: str,
    runner_id: str,
    phase: str,
    active_issue: dict[str, Any] | None,
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn loose action recommendations into exact runtime packets."""
    normalized: list[dict[str, Any]] = []
    issue_url = str((active_issue or {}).get("url") or "").strip() or None
    issue_repo = str((active_issue or {}).get("repo") or "").strip() or None
    issue_number = (active_issue or {}).get("number")
    for raw in actions:
        action = str(raw.get("action") or "").strip()
        if not action:
            continue
        packet = {
            "action": action,
            "stage": infer_action_stage(action),
            "reason": str(raw.get("reason") or "").strip() or None,
            "status": "pending",
            "idempotency_key": build_action_idempotency_key(
                project=project,
                runner_id=runner_id,
                phase=phase,
                action=action,
                issue_url=issue_url,
            ),
            "payload": {
                "project": project,
                "runner_id": runner_id,
                "phase": phase,
                "issue_url": issue_url,
                "issue_repo": issue_repo,
                "issue_number": issue_number,
            },
        }
        mutation_payload = build_github_mutation_payload(
            action=action,
            project=project,
            runner_id=runner_id,
            phase=phase,
            active_issue=active_issue,
            reason=packet["reason"],
            action_idempotency_key=packet["idempotency_key"],
        )
        if mutation_payload is not None:
            packet["payload"]["mutation"] = mutation_payload
        normalized.append(packet)
    return normalized


def infer_action_stage(action: str) -> str:
    if action in {"sync_issue", "refresh_issue_snapshot"}:
        return "ingest"
    if action in {"select_next_issue"}:
        return "select"
    if action in {"repair_workspace", "acquire_worktree", "wait_for_human_approval", "apply_operator_override"}:
        return "recover"
    return "dispatch"


def build_action_idempotency_key(
    *,
    project: str,
    runner_id: str,
    phase: str,
    action: str,
    issue_url: str | None,
) -> str:
    seed = "|".join([project, runner_id, phase, action, issue_url or "-"])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def build_action_queue_payload(
    *,
    project: str | None,
    runner_id: str | None,
    kanban_state: dict[str, Any],
) -> dict[str, Any]:
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    return {
        "project": project,
        "runner_id": runner_id,
        "desired_state": str(reconcile.get("desired_state") or "").strip() or None,
        "actual_state": str(reconcile.get("actual_state") or "").strip() or None,
        "actions": reconcile.get("actions") if isinstance(reconcile.get("actions"), list) else [],
        "mutation_intents": _collect_mutation_intents(
            reconcile.get("actions") if isinstance(reconcile.get("actions"), list) else []
        ),
        "generated_at": utc_now(),
    }


def build_reconcile_result_payload(
    *,
    project: str | None,
    runner_id: str | None,
    kanban_state: dict[str, Any],
) -> dict[str, Any]:
    reconcile = kanban_state.get("reconcile") if isinstance(kanban_state.get("reconcile"), dict) else {}
    conditions = kanban_state.get("conditions") if isinstance(kanban_state.get("conditions"), dict) else {}
    drift = kanban_state.get("drift") if isinstance(kanban_state.get("drift"), dict) else {}
    return {
        "project": project,
        "runner_id": runner_id,
        "desired_state": str(reconcile.get("desired_state") or "").strip() or None,
        "actual_state": str(reconcile.get("actual_state") or "").strip() or None,
        "gap_reason": str(reconcile.get("gap_reason") or "").strip() or None,
        "stage_results": reconcile.get("stage_results") if isinstance(reconcile.get("stage_results"), dict) else {},
        "controller": reconcile.get("controller") if isinstance(reconcile.get("controller"), dict) else {},
        "mutation_intents": _collect_mutation_intents(
            reconcile.get("actions") if isinstance(reconcile.get("actions"), list) else []
        ),
        "unmet_conditions": [
            key
            for key in KANBAN_CONDITION_KEYS
            if not bool((conditions.get(key) or {}).get("status", False))
        ],
        "drift": drift,
        "generated_at": utc_now(),
    }


def _condition_message(condition_key: str, *, status: bool, reason: str | None) -> str:
    if status:
        if condition_key == "ready_for_execution":
            return "Execution preconditions are satisfied."
        return "Condition satisfied."
    if condition_key == "ready_for_execution":
        mapping = {
            "no_active_issue": "No active issue is selected yet.",
            "human_approval_required": "Human approval is blocking execution.",
            "enhance_required": "The active issue must be refined or split before execution.",
            "blocked_by_dependency": "A dependency blocker is preventing execution.",
            "planning_not_satisfied": "Planning is not complete enough to begin execution.",
            "verification_blocked": "Verification state is not healthy enough to proceed.",
            "worktree_mismatch": "Workspace reality does not match the active issue checkout.",
            "branch_mismatch": "The current branch does not match the active issue checkout.",
            "no_active_checkout": "The active issue does not have a complete worktree and branch context yet.",
            "blocked": "The run is currently blocked.",
        }
        return mapping.get(str(reason or "").strip(), "Execution preconditions are not satisfied.")
    return "Condition not satisfied."


def build_github_mutation_payload(
    *,
    action: str,
    project: str,
    runner_id: str,
    phase: str,
    active_issue: dict[str, Any] | None,
    reason: str | None,
    action_idempotency_key: str,
) -> dict[str, Any] | None:
    issue = active_issue if isinstance(active_issue, dict) else {}
    issue_url = str(issue.get("url") or "").strip()
    issue_repo = str(issue.get("repo") or "").strip()
    issue_title = str(issue.get("title") or "").strip()
    issue_number = issue.get("number")
    if not issue_url or not issue_repo:
        return None

    mutation_base = {
        "source": "tmux_codex_reconcile",
        "repo": issue_repo,
        "issue_url": issue_url,
        "issue_number": issue_number,
        "issue_title": issue_title or None,
        "project": project,
        "runner_id": runner_id,
        "phase": phase,
        "reason": reason,
        "idempotency_key": action_idempotency_key,
    }
    if action == "create_followup_ticket":
        return {
            **mutation_base,
            "operation": "create_issue",
            "title": _followup_issue_title(issue_title),
            "body": _followup_issue_body(issue=issue, reason=reason),
            "labels": ["follow-up", "codex"],
            "parent_issue_url": issue_url,
            "dedupe_key": f"github:create_issue:{issue_repo}:{action_idempotency_key}",
        }
    if action == "write_blocker_comment":
        return {
            **mutation_base,
            "operation": "create_issue_comment",
            "body": _blocker_comment_body(issue=issue, reason=reason),
            "comment_tag": "<!-- tmux-codex:blocker-comment -->",
            "dedupe_key": f"github:create_issue_comment:{issue_repo}:{action_idempotency_key}",
        }
    return None


def _collect_mutation_intents(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        mutation = payload.get("mutation") if isinstance(payload.get("mutation"), dict) else None
        if mutation:
            intents.append(mutation)
    return intents


def _followup_issue_title(issue_title: str) -> str:
    base = issue_title.strip() or "Active issue"
    return f"Follow-up: {base}"


def _followup_issue_body(*, issue: dict[str, Any], reason: str | None) -> str:
    issue_url = str(issue.get("url") or "").strip()
    merge_into = str(issue.get("merge_into") or "").strip() or "main"
    resume_from = str(issue.get("resume_from") or "").strip() or "execution"
    branch = str(issue.get("branch") or "").strip() or "not-set"
    worktree = str(issue.get("worktree") or "").strip() or "not-set"
    return "\n".join(
        [
            "Follow-up required from controller reconcile.",
            "",
            "## Ticket Relations",
            f"- Parent: {issue_url}",
            "- Children: none",
            "- Blocked by: none",
            "- Unblocks: none",
            "",
            "## Execution Routing",
            f"- Worktree: {worktree}",
            f"- Branch: {branch}",
            f"- Depends on: {issue_url}",
            f"- Merge into: {merge_into}",
            f"- Resume from: {resume_from}",
            "",
            "## Controller Context",
            f"- Reason: {reason or 'follow-up required'}",
            "- Source: tmux-codex durable control plane",
        ]
    )


def _blocker_comment_body(*, issue: dict[str, Any], reason: str | None) -> str:
    issue_url = str(issue.get("url") or "").strip()
    return "\n".join(
        [
            "Blocked on controller reconcile follow-up.",
            "",
            "<!-- tmux-codex:blocker-comment -->",
            f"- Issue: {issue_url}",
            f"- Reason: {reason or 'blocked'}",
            "- Source: tmux-codex durable control plane",
        ]
    )


def load_or_init_kanban_state(paths: RunnerStatePaths, project: str) -> dict[str, Any]:
    """Load ticket-native kanban continuity state or create it when missing."""
    state = read_json(paths.kanban_state_json)
    if state is None:
        state = default_kanban_state(project)
        write_json(paths.kanban_state_json, state)
        return state
    normalized, changed = normalize_kanban_state(state, project=project)
    if changed:
        write_json(paths.kanban_state_json, normalized)
    return normalized


def detect_git_context(project_root: Path) -> dict[str, str | None]:
    """Capture branch/head/worktree details for deterministic runner handoff."""
    if not project_root.exists():
        return {
            "git_branch": None,
            "git_head": None,
            "git_worktree": str(project_root),
        }

    def _read(cmd: list[str]) -> str | None:
        try:
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    branch = _read(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    head = _read(["git", "rev-parse", "HEAD"])
    worktree = str(project_root.resolve())
    return {
        "git_branch": branch,
        "git_head": head,
        "git_worktree": worktree,
    }


def compute_worktree_fingerprint(project_root: Path) -> str | None:
    """Hash git-visible worktree state, excluding runner memory churn."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None

    filtered_lines: list[str] = []
    for raw_line in result.stdout.splitlines():
        path_text = raw_line[3:].strip()
        candidate_paths = [item.strip() for item in path_text.split(" -> ")] if path_text else []
        if any(path == ".memory" or path.startswith(".memory/") for path in candidate_paths):
            continue
        line_parts = [raw_line]
        for relative_path in candidate_paths:
            if not relative_path:
                continue
            file_path = project_root / relative_path
            if not file_path.exists():
                line_parts.append(f"{relative_path}:missing")
                continue
            if file_path.is_file():
                try:
                    payload = file_path.read_bytes()
                except OSError:
                    line_parts.append(f"{relative_path}:unreadable")
                    continue
                digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
                line_parts.append(f"{relative_path}:{digest}")
                continue
            line_parts.append(f"{relative_path}:dir")
        filtered_lines.append("|".join(line_parts))

    if not filtered_lines:
        return "clean"
    digest = hashlib.sha1("\n".join(filtered_lines).encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()


def count_open_tasks(tasks_payload: dict[str, Any] | None) -> int:
    """Count tasks that are not done from TASKS.json payload."""
    if not isinstance(tasks_payload, dict):
        return 0
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return 0
    open_count = 0
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status in {"open", "in_progress", "blocked"}:
            open_count += 1
    return open_count


def count_open_seams(seams_payload: dict[str, Any] | None) -> int:
    """Count seams that are not done from SEAMS.json payload."""
    if not isinstance(seams_payload, dict):
        return 0
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        return 0
    open_count = 0
    for raw in seams:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status in {"open", "in_progress", "blocked"}:
            open_count += 1
    return open_count
