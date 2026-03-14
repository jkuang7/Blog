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
    "Execute one task from TASKS.json with validation evidence.",
    "Run run_gates and only mark done when all checks pass.",
]
RUNNER_PHASES = ("discover", "implement", "verify", "closeout")
DEFAULT_PHASE_BUDGET_MINUTES = 45


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
    prd_json: Path
    tasks_json: Path
    exec_context_json: Path
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
        prd_json=runner_dir / "PRD.json",
        tasks_json=runner_dir / "TASKS.json",
        exec_context_json=runner_dir / "RUNNER_EXEC_CONTEXT.json",
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
        "current_goal": "Establish the active objective and execute the next concrete validated slice.",
        "last_iteration_summary": "",
        "completed_recent": [],
        "next_task": "Execute the first concrete validated slice toward the active objective.",
        "next_task_reason": "No prior iteration update exists yet.",
        "objective_id": None,
        "next_task_id": None,
        "current_task_id": None,
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
        },
        "updated_at": now,
    }


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
    normalized["next_task"] = str(normalized.get("next_task", defaults["next_task"])).strip() or defaults["next_task"]
    normalized["next_task_reason"] = str(
        normalized.get("next_task_reason", defaults["next_task_reason"])
    ).strip() or defaults["next_task_reason"]

    for field in ("objective_id", "next_task_id", "current_task_id", "task_selection_reason"):
        value = normalized.get(field)
        normalized[field] = str(value).strip() if isinstance(value, str) and str(value).strip() else None

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

    runtime_policy = normalized.get("runtime_policy")
    if not isinstance(runtime_policy, dict):
        runtime_policy = {}
    runtime_policy = {
        "runner_mode": str(runtime_policy.get("runner_mode", runner_mode)).replace("-", "_"),
        "session_strategy": str(runtime_policy.get("session_strategy", session_strategy)).replace("-", "_"),
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
        paths.prd_json,
        paths.tasks_json,
        paths.exec_context_json,
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
