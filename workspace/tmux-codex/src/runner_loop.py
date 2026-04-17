"""Codex infinite loop runner helpers and orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from .codex_threads import archive_runner_threads_for_cwd
from .codex_engine import CodexRunResult, run_codex_iteration
from .hooks import HookAdapter, LocalHooks, load_agents_bridge
from .orx_control import OrxControlError, fetch_execution_packet, submit_slice_result
from .runctl import create_runner_state, resolve_target_project_root
from .runner_status import detect_runner_state, has_explicit_codex_prompt
from .runner_state import (
    RunnerStatePaths,
    append_ndjson,
    coerce_runner_phase,
    build_runner_state_paths_for_root,
    count_open_tasks,
    detect_git_context,
    ensure_memory_dir,
    load_or_init_state,
    read_json,
    update_state,
    utc_now,
    write_runner_status_snapshot,
    write_json,
)
from .tmux import LINES_FULL, LINES_STATUS, TmuxClient

COMPLEXITY_PROFILE_MAP = {
    "low": ("gpt-5.3-codex", "low"),
    "med": ("gpt-5.3-codex", "medium"),
    "high": ("gpt-5.3-codex", "high"),
    "xhigh": ("gpt-5.3-codex", "xhigh"),
}
TASK_MODEL_PROFILE_MAP = {
    "mini": ("gpt-5.4-mini", "medium"),
    "high": ("gpt-5.4", "high"),
}

ProjectStack = Literal["pnpm", "npm", "python_pyproject", "python_requirements", "go", "cargo", "unknown"]
RUNNER_UPDATE_START_MARKER = "RUNNER_UPDATE_START"
RUNNER_UPDATE_END_MARKER = "RUNNER_UPDATE_END"
RUNNER_RESULT_START_MARKER = "RUNNER_RESULT_START"
RUNNER_RESULT_END_MARKER = "RUNNER_RESULT_END"
REQUIRED_UPDATE_KEYS = (
    "summary",
    "completed",
    "completed_seam_ids",
    "next_seam",
    "next_seam_reason",
    "blockers",
    "remaining_gaps",
    "done_candidate",
)
DONE_SIGNAL_TRUE_PATTERNS = (
    re.compile(r"\ball requested (changes|work) (are )?(complete|completed)\b", re.IGNORECASE),
    re.compile(r"\b(all )?implementation (is )?(complete|completed)\b", re.IGNORECASE),
    re.compile(r"\bno (remaining|further) (tasks|changes|work)\b", re.IGNORECASE),
    re.compile(r"\bnothing left to (do|implement|change)\b", re.IGNORECASE),
    re.compile(r"\bready for (handoff|merge|review)\b", re.IGNORECASE),
)
DONE_SIGNAL_FALSE_PATTERNS = (
    re.compile(r"\b(not|isn't|is not) (done|complete)\b", re.IGNORECASE),
    re.compile(r"\b(incomplete|partial)\b", re.IGNORECASE),
    re.compile(r"\b(next task|next step|todo|to do|follow[- ]up)\b", re.IGNORECASE),
)
RUNNER_UPDATE_VISIBLE_HOLD_SECONDS = 1.2
RUNNER_DISPATCH_IDLE_GRACE_SECONDS = 1.0
RUNNER_COMPLETION_IDLE_GRACE_SECONDS = 1.0
RUNNER_UPDATE_PENDING_PREFIX = "update_pending"
RUNNER_RECOVERY_TASK_TITLE_PREFIX = "Diagnose stalled runner recovery for"
RUNNER_FALLBACK_COMPLETION_STABLE_SECONDS = 4.0


@dataclass(frozen=True)
class RunnerPaths:
    """Compatibility wrapper around codex runner state paths."""

    state: RunnerStatePaths

    @property
    def memory_dir(self) -> Path:
        return self.state.memory_dir

    @property
    def complete_lock(self) -> Path:
        return self.state.done_lock

    @property
    def stop_file(self) -> Path:
        return self.state.stop_lock

    @property
    def active_lock(self) -> Path:
        return self.state.active_lock

    @property
    def state_file(self) -> Path:
        return self.state.state_file

    @property
    def audit_file(self) -> Path:
        return self.state.ledger_file

    @property
    def gates_file(self) -> Path:
        return self.state.gates_file

    @property
    def runner_log(self) -> Path:
        return self.state.runner_log

    @property
    def runners_log(self) -> Path:
        return self.state.runners_log


def resolve_runner_profile(complexity: str, model_override: str | None) -> tuple[str, str]:
    """Resolve model + reasoning effort from complexity preset."""
    if complexity not in COMPLEXITY_PROFILE_MAP:
        allowed = ", ".join(sorted(COMPLEXITY_PROFILE_MAP))
        raise ValueError(f"Invalid complexity '{complexity}'. Use one of: {allowed}")

    mapped_model, effort = COMPLEXITY_PROFILE_MAP[complexity]
    if model_override:
        return model_override, effort
    return mapped_model, effort


def _normalize_seam_model_profile(raw: object) -> str | None:
    candidate = str(raw or "").strip().lower()
    if candidate in TASK_MODEL_PROFILE_MAP:
        return candidate
    return None


def _normalize_scope_status(raw: object) -> str:
    candidate = str(raw or "").strip().lower()
    if candidate in {"ok", "narrow", "split", "reseed"}:
        return candidate
    return "ok"


def _pending_update_profile_from_state(state: dict[str, object] | None) -> str | None:
    if not isinstance(state, dict):
        return None
    current_step = str(state.get("current_step") or "").strip().lower()
    if not current_step.startswith(RUNNER_UPDATE_PENDING_PREFIX):
        return None
    _, _, raw_profile = current_step.partition(":")
    profile = _normalize_seam_model_profile(raw_profile)
    return profile or "mini"


def _parse_status_directive(text: str, key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}=(.+)$", re.MULTILINE)
    matches = pattern.findall(text)
    if not matches:
        return None
    return matches[-1].strip().lower()


def _parse_execute_update_request(text: str) -> dict[str, str | bool]:
    needs_update = _parse_status_directive(text, "needs_update")
    update_profile = _normalize_seam_model_profile(_parse_status_directive(text, "update_profile"))
    update_reason = _parse_status_directive(text, "update_reason")
    scope_status = _normalize_scope_status(_parse_status_directive(text, "scope_status"))
    phase_done = _parse_status_directive(text, "phase_done")
    requested = needs_update == "yes"
    return {
        "needs_update": requested,
        "update_profile": update_profile or "mini",
        "update_reason": update_reason or "",
        "scope_status": scope_status,
        "phase_done": phase_done == "yes",
    }


def _has_completion_directive_for_step(text: str, step: Literal["execute", "update"]) -> bool:
    if _parse_status_directive(text, "exiting") != "yes":
        return False
    if step == "execute":
        return any(
            _parse_status_directive(text, key) is not None
            for key in ("phase_done", "validation", "needs_update", "scope_status")
        )
    return _parse_status_directive(text, "state_repaired") is not None


def _session_profile_for_dispatch(
    *,
    model_profile: str | None,
    model: str,
    reasoning_effort: str,
) -> str | None:
    normalized = _normalize_seam_model_profile(model_profile)
    if normalized:
        return normalized
    for candidate, (candidate_model, candidate_effort) in TASK_MODEL_PROFILE_MAP.items():
        if model == candidate_model and reasoning_effort == candidate_effort:
            return candidate
    return None


def _can_continue_update_in_same_session(
    *,
    requested_profile: str,
    session_profile: str | None,
) -> bool:
    if requested_profile != "high":
        return True
    return session_profile == "high"


def _prepare_same_session_update_handoff(
    *,
    state_paths: RunnerStatePaths,
    state: dict[str, object],
    phase: str,
    update_profile: str,
    scope_status: str,
    reason: str | None,
    active_step: Literal["execute", "update"],
) -> dict[str, object]:
    next_state = update_state(
        state_paths.state_file,
        state,
        status="running",
        current_step=f"{RUNNER_UPDATE_PENDING_PREFIX}:{update_profile}",
    )
    _append_ledger(
        state_paths,
        "runner.update_requested_same_session",
        phase=phase,
        update_profile=update_profile,
        scope_status=scope_status,
        reason=reason or None,
        source_step=active_step,
    )
    _log_line(
        state_paths,
        (
            f"{active_step.capitalize()} requested semantic run_govern for phase={phase}; "
            "continuing in the same Codex session to preserve context"
        ),
    )
    return next_state


def _active_seam_from_runtime_payload(paths: RunnerStatePaths, seam_id: str | None) -> dict[str, object] | None:
    payload = read_json(paths.seams_json)
    tasks = payload.get("seams") if isinstance(payload, dict) else None
    id_key = "seam_id"
    if not isinstance(tasks, list):
        payload = read_json(paths.tasks_json)
        if not isinstance(payload, dict):
            return None
        tasks = payload.get("tasks")
        id_key = "task_id"
    if not isinstance(tasks, list):
        return None
    normalized_seam_id = str(seam_id or "").strip()
    if normalized_seam_id:
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            if str(raw.get(id_key, "")).strip() == normalized_seam_id:
                if id_key == "seam_id":
                    return {
                        "seam_id": str(raw.get("seam_id", "")).strip(),
                        "title": raw.get("title"),
                        "status": raw.get("status"),
                        "model_profile": raw.get("model_profile"),
                        "profile_reason": raw.get("why_now"),
                    }
                return raw
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
            if str(raw.get("status", "")).strip().lower() in {"open", "in_progress", "blocked"}:
                if id_key == "seam_id":
                    return {
                        "seam_id": str(raw.get("seam_id", "")).strip(),
                        "title": raw.get("title"),
                        "status": raw.get("status"),
                        "model_profile": raw.get("model_profile"),
                        "profile_reason": raw.get("why_now"),
                    }
                return raw
    return None


def _sync_runner_status_snapshot(paths: RunnerStatePaths, state: dict[str, object]) -> None:
    kanban_state = read_json(paths.kanban_state_json) or {}
    write_runner_status_snapshot(paths, state, kanban_state)


def resolve_active_seam_execution_profile(
    *,
    paths: RunnerStatePaths,
    fallback_model: str,
    fallback_reasoning_effort: str,
) -> dict[str, str | None]:
    """Resolve the current seam routing profile from exec-context/state with safe fallback."""
    active_issue = _active_issue_snapshot(paths.memory_dir.parent)
    project_key = (
        str(active_issue.get("project_key") or "").strip()
        if isinstance(active_issue, dict)
        else ""
    ) or paths.memory_dir.parent.name
    execution_packet = _orx_execution_packet(paths.memory_dir.parent, project=project_key)
    if _is_orx_execution_packet(execution_packet):
        model = str(execution_packet.get("execution_model") or fallback_model).strip() or fallback_model
        reasoning_effort = (
            str(execution_packet.get("execution_reasoning_effort") or fallback_reasoning_effort).strip()
            or fallback_reasoning_effort
        )
        issue_key = str(execution_packet.get("issue_key") or "").strip() or None
        issue_title = str(execution_packet.get("issue_title") or "").strip() or None
        return {
            "model_profile": "orx",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "seam_id": issue_key,
            "seam_title": issue_title,
            "task_id": issue_key,
            "task_title": issue_title,
            "profile_reason": "ORX execution packet is authoritative for the active slice.",
            "source": "orx_execution_packet",
        }

    exec_context = read_json(paths.exec_context_json)
    state = read_json(paths.state_file)
    if not isinstance(exec_context, dict):
        exec_context = {}
    if not isinstance(state, dict):
        state = {}

    pending_update_profile = _pending_update_profile_from_state(state)

    seam_id = str(exec_context.get("seam_id") or exec_context.get("task_id") or exec_context.get("next_task_id") or state.get("active_seam_id") or state.get("next_task_id") or "").strip()
    seam_title = str(exec_context.get("seam_title") or exec_context.get("task_title") or exec_context.get("next_task") or state.get("next_task") or "").strip()
    profile_reason = str(exec_context.get("profile_reason") or "").strip()
    model_profile = _normalize_seam_model_profile(exec_context.get("model_profile"))
    profile_source = "exec_context"

    if pending_update_profile:
        model, reasoning_effort = TASK_MODEL_PROFILE_MAP[pending_update_profile]
        return {
            "model_profile": pending_update_profile,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "seam_id": seam_id or None,
            "seam_title": seam_title or None,
            "task_id": seam_id or None,
            "task_title": seam_title or None,
            "profile_reason": "Pending semantic run_govern session after execute slice.",
            "source": "pending_update",
        }

    seam = None
    if not model_profile or not seam_title or not profile_reason:
        seam = _active_seam_from_runtime_payload(paths, seam_id or None)
        if isinstance(seam, dict):
            seam_id = seam_id or str(seam.get("seam_id") or seam.get("task_id", "")).strip()
            seam_title = seam_title or str(seam.get("title", "")).strip()
            profile_reason = profile_reason or str(seam.get("profile_reason", "")).strip()
            model_profile = model_profile or _normalize_seam_model_profile(seam.get("model_profile"))
            profile_source = "tasks_json"

    if model_profile:
        model, reasoning_effort = TASK_MODEL_PROFILE_MAP[model_profile]
        return {
            "model_profile": model_profile,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "seam_id": seam_id or None,
            "seam_title": seam_title or None,
            "task_id": seam_id or None,
            "task_title": seam_title or None,
            "profile_reason": profile_reason or None,
            "source": profile_source,
        }

    return {
        "model_profile": None,
        "model": fallback_model,
        "reasoning_effort": fallback_reasoning_effort,
        "seam_id": seam_id or None,
        "seam_title": seam_title or None,
        "task_id": seam_id or None,
        "task_title": seam_title or None,
        "profile_reason": profile_reason or None,
        "source": "fallback",
    }


def resolve_active_task_execution_profile(
    *,
    paths: RunnerStatePaths,
    fallback_model: str,
    fallback_reasoning_effort: str,
) -> dict[str, str | None]:
    """Compatibility alias for seam-based routing profile resolution."""
    return resolve_active_seam_execution_profile(
        paths=paths,
        fallback_model=fallback_model,
        fallback_reasoning_effort=fallback_reasoning_effort,
    )


def _run_scripted_cycle_refresh(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> tuple[bool, str | None]:
    runctl_path = _repo_home() / "bin" / "runctl"
    setup_cmd = [
        "python3",
        str(runctl_path),
        "--setup",
        "--quiet",
        "--project-root",
        str(project_root),
        "--runner-id",
        runner_id,
    ]
    prepare_cmd = [
        "python3",
        str(runctl_path),
        "--prepare-cycle",
        "--quiet",
        "--project-root",
        str(project_root),
        "--runner-id",
        runner_id,
    ]
    try:
        setup_result = subprocess.run(setup_cmd, capture_output=True, text=True, check=False)
        if setup_result.returncode != 0:
            message = (setup_result.stderr or setup_result.stdout or "runctl --setup failed").strip()
            return False, f"setup_failed:{message}"
        prepare_result = subprocess.run(prepare_cmd, capture_output=True, text=True, check=False)
        if prepare_result.returncode != 0:
            message = (prepare_result.stderr or prepare_result.stdout or "runctl --prepare-cycle failed").strip()
            return False, f"prepare_failed:{message}"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, f"refresh_exception:{type(exc).__name__}: {exc}"
    return True, None


def _reload_refreshed_runner_state(state_file: Path, fallback_state: dict[str, object]) -> dict[str, object]:
    refreshed_state = read_json(state_file)
    if isinstance(refreshed_state, dict):
        return refreshed_state
    return fallback_state


def _write_runner_stop_lock(
    *,
    state_paths: RunnerStatePaths,
    source: str,
    reason: str,
    phase: str,
    session_name: str,
) -> None:
    state_paths.stop_lock.parent.mkdir(parents=True, exist_ok=True)
    state_paths.stop_lock.write_text(
        (
            f"requested_at={utc_now()}\n"
            f"source={source}\n"
            f"reason={reason}\n"
            f"phase={phase}\n"
            f"session={session_name}\n"
        ),
        encoding="utf-8",
    )


def _is_no_durable_progress_refresh_failure(reason: str | None) -> bool:
    normalized = str(reason or "").strip().lower()
    return "no durable progress" in normalized


def _next_recovery_task_id(state_paths: RunnerStatePaths) -> str:
    highest = 0
    for path in (state_paths.tasks_json, state_paths.task_intake_file):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            continue
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("task_id", "")).strip()
            match = re.match(r"^TT-(\d+)$", task_id)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"TT-{highest + 1:03d}"


def _build_no_progress_recovery_title(*, stalled_task_id: str | None, stalled_task_title: str | None) -> str:
    task_id = str(stalled_task_id or "").strip()
    task_title = str(stalled_task_title or "").strip()
    if task_id and task_title:
        return f"{RUNNER_RECOVERY_TASK_TITLE_PREFIX} {task_id}: {task_title}"
    if task_id:
        return f"{RUNNER_RECOVERY_TASK_TITLE_PREFIX} {task_id}"
    return f"{RUNNER_RECOVERY_TASK_TITLE_PREFIX} the current slice"


def _queue_no_progress_recovery_task(
    *,
    state_paths: RunnerStatePaths,
    state: dict[str, object],
    project_root: Path,
    failure_reason: str,
    stalled_task_id: str | None,
    stalled_task_title: str | None,
) -> tuple[bool, str]:
    recovery_title = _build_no_progress_recovery_title(
        stalled_task_id=stalled_task_id,
        stalled_task_title=stalled_task_title,
    )
    tasks_payload = read_json(state_paths.tasks_json) or {}
    intake_payload = read_json(state_paths.task_intake_file) or {}
    objective_id = str(tasks_payload.get("objective_id") or state.get("objective_id") or "").strip()
    target_branch = str(state.get("target_branch") or "main").strip() or "main"

    for payload, include_status in ((tasks_payload, True), (intake_payload, False)):
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(tasks, list):
            continue
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("title", "")).strip() != recovery_title:
                continue
            status = str(raw.get("status", "")).strip().lower()
            if not include_status or status in {"open", "in_progress", "blocked"}:
                task_id = str(raw.get("task_id", "")).strip() or "(pending)"
                return True, task_id

    queued_tasks = intake_payload.get("tasks")
    if not isinstance(queued_tasks, list):
        queued_tasks = []
        intake_payload["tasks"] = queued_tasks

    queued_task_id = _next_recovery_task_id(state_paths)
    task_label = str(stalled_task_id or "current slice").strip()
    queued_tasks.append(
        {
            "task_id": queued_task_id,
            "title": recovery_title,
            "priority": "p0",
            "depends_on": [],
            "acceptance": [
                f"Identify why {task_label} exited run_govern without durable progress.",
                "Rewrite or split the active backlog so the next cycle has one explicit actionable recovery slice.",
                "Preserve the current objective and do not relax acceptance or validation contracts while repairing the backlog.",
            ],
            "validation": [
                "TASKS.json or queued runner intake shows a concrete next slice that explains the stall instead of repeating the same broad title.",
                "The recovery keeps one actionable frontier and records any blocked sibling slices explicitly.",
                "The next runner refresh selects a durable backlog change instead of repeating the prior no-progress state.",
            ],
            "project_root": str(project_root.resolve()),
            "target_branch": target_branch,
            "objective_id": objective_id or None,
            "queued_at": utc_now(),
            "reason": failure_reason[:220],
        }
    )
    write_json(state_paths.task_intake_file, intake_payload)
    return True, queued_task_id


def render_runner_profile_shell_exports(profile: dict[str, str | None]) -> str:
    """Render shell-safe assignments for per-cycle runner model selection."""
    shell_vars = {
        "RUNNER_MODEL": profile.get("model") or "",
        "RUNNER_REASONING_EFFORT": profile.get("reasoning_effort") or "",
        "RUNNER_MODEL_PROFILE": profile.get("model_profile") or "",
        "RUNNER_PROFILE_REASON": profile.get("profile_reason") or "",
        "RUNNER_SEAM_ID": profile.get("seam_id") or profile.get("task_id") or "",
        "RUNNER_SEAM_TITLE": profile.get("seam_title") or profile.get("task_title") or "",
        "RUNNER_TASK_ID": profile.get("seam_id") or profile.get("task_id") or "",
        "RUNNER_TASK_TITLE": profile.get("seam_title") or profile.get("task_title") or "",
        "RUNNER_PROFILE_SOURCE": profile.get("source") or "",
    }
    return "\n".join(f"{name}={shlex.quote(value)}" for name, value in shell_vars.items())


def _runner_idle_grace_seconds(default_seconds: float, poll_seconds: float) -> float:
    """Use real idle grace in live runs but keep zero-poll tests deterministic."""
    if poll_seconds <= 0:
        return 0.0
    return default_seconds


def build_runner_paths(
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path | None = None,
) -> RunnerPaths:
    """Create runner-specific path layout."""
    if project_root is None:
        project_root = resolve_target_project_root(
            dev=dev,
            project=project,
            runner_id=runner_id,
        )
    return RunnerPaths(
        state=build_runner_state_paths_for_root(
            project_root=project_root,
            dev=dev,
            project=project,
            runner_id=runner_id,
        )
    )


def detect_project_stack(project_root: Path) -> ProjectStack:
    """Detect project stack from marker files."""
    if (project_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_root / "package-lock.json").exists() or (project_root / "package.json").exists():
        return "npm"
    if (project_root / "pyproject.toml").exists():
        return "python_pyproject"
    if (project_root / "requirements.txt").exists():
        return "python_requirements"
    if (project_root / "go.mod").exists():
        return "go"
    if (project_root / "Cargo.toml").exists():
        return "cargo"
    return "unknown"


def render_gates_template(stack: ProjectStack) -> str:
    """Render gates.sh template for the detected stack."""
    project_cd = 'cd "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"'

    if stack == "pnpm":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v node >/dev/null 2>&1 || {{ echo \"run_gates: node is required\"; return 1; }}
    command -v pnpm >/dev/null 2>&1 || {{ echo \"run_gates: pnpm is required\"; return 1; }}
    [[ -f package.json ]] || {{ echo \"run_gates: package.json not found\"; return 1; }}

    for script_name in lint test build; do
        node -e \"const fs=require('fs'); const p=JSON.parse(fs.readFileSync('package.json','utf8')); process.exit(p.scripts && p.scripts['$script_name'] ? 0 : 1);\" >/dev/null 2>&1 || {{
            echo \"run_gates: missing required package.json script: $script_name\"
            return 1
        }}
    done

    pnpm run lint
    pnpm run test
    pnpm run build
}}
"""

    if stack == "npm":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v node >/dev/null 2>&1 || {{ echo \"run_gates: node is required\"; return 1; }}
    command -v npm >/dev/null 2>&1 || {{ echo \"run_gates: npm is required\"; return 1; }}
    [[ -f package.json ]] || {{ echo \"run_gates: package.json not found\"; return 1; }}

    for script_name in lint test build; do
        node -e \"const fs=require('fs'); const p=JSON.parse(fs.readFileSync('package.json','utf8')); process.exit(p.scripts && p.scripts['$script_name'] ? 0 : 1);\" >/dev/null 2>&1 || {{
            echo \"run_gates: missing required package.json script: $script_name\"
            return 1
        }}
    done

    npm run lint
    npm run test
    npm run build
}}
"""

    if stack in ("python_pyproject", "python_requirements"):
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v pytest >/dev/null 2>&1 || {{ echo \"run_gates: pytest is required\"; return 1; }}

    if command -v ruff >/dev/null 2>&1; then
        ruff check .
    elif command -v flake8 >/dev/null 2>&1; then
        flake8 .
    else
        echo \"run_gates: install ruff or flake8 before marking complete\"
        return 1
    fi

    pytest
}}
"""

    if stack == "go":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v go >/dev/null 2>&1 || {{ echo \"run_gates: go is required\"; return 1; }}

    go test ./...
    go vet ./...
}}
"""

    if stack == "cargo":
        return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}

    command -v cargo >/dev/null 2>&1 || {{ echo \"run_gates: cargo is required\"; return 1; }}

    cargo test
    cargo clippy -- -D warnings
}}
"""

    return f"""#!/usr/bin/env bash

run_gates() {{
    set -euo pipefail
    {project_cd}
    echo "run_gates: unknown project stack"
    echo "TODO: edit .memory/gates.sh and implement build/lint/test/smoke checks"
    return 1
}}
"""


def ensure_gates_file(
    dev: str,
    project: str,
    runner_id: str = "main",
    project_root: Path | None = None,
) -> tuple[Path, bool]:
    """Create stack-aware gates.sh only when missing."""
    resolved_root = project_root or resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    memory_dir = resolved_root / ".memory"
    gates_file = memory_dir / "gates.sh"
    lessons_file = memory_dir / "lessons.md"

    memory_dir.mkdir(parents=True, exist_ok=True)
    if not lessons_file.exists():
        lessons_file.write_text("# Lessons\n\n")

    if gates_file.exists():
        return gates_file, False

    stack = detect_project_stack(resolved_root)
    gates_file.write_text(render_gates_template(stack))
    os.chmod(gates_file, 0o755)
    return gates_file, True


def _repo_home() -> Path:
    override = os.environ.get("TMUX_CLI_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _build_execute_only_command(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
    phase: str,
) -> str:
    phase_name = coerce_runner_phase(phase, default="discover")
    return _build_runner_dispatch_prompt(
        prompt_name="run_execute",
        project=project,
        runner_id=runner_id,
        project_root=project_root,
        phase=phase_name,
    )


def _build_update_command(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> str:
    return _build_runner_dispatch_prompt(
        prompt_name="run_govern",
        project=project,
        runner_id=runner_id,
        project_root=project_root,
        phase=None,
    )


def _prompt_template_path(prompt_name: str) -> Path:
    return _repo_home() / "prompts" / f"{prompt_name}.md"


def _load_prompt_template(prompt_name: str) -> str:
    return _prompt_template_path(prompt_name).read_text(encoding="utf-8").strip()


def _issue_context_from_execution_packet(packet: dict[str, object] | None) -> dict[str, str]:
    if not _is_orx_execution_packet(packet):
        return {}
    fields = {
        "identifier": str((packet or {}).get("issue_key") or "").strip(),
        "title": str((packet or {}).get("issue_title") or "").strip(),
        "external_url": str((packet or {}).get("issue_url") or "").strip(),
        "project_key": str((packet or {}).get("project_key") or "").strip(),
        "project_name": str((packet or {}).get("project_display_name") or "").strip(),
        "worktree": str((packet or {}).get("worktree_path") or "").strip(),
        "branch": str((packet or {}).get("branch") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _active_issue_dispatch_context(project_root: Path, *, project: str) -> dict[str, str]:
    execution_packet = _orx_execution_packet(project_root, project=project)
    packet_issue = _issue_context_from_execution_packet(execution_packet)
    if packet_issue:
        return packet_issue

    kanban_state = read_json(project_root / ".memory" / "runner" / "KANBAN_STATE.json")
    if not isinstance(kanban_state, dict):
        return {}
    active_issue = kanban_state.get("active_issue")
    if not isinstance(active_issue, dict):
        return {}
    fields = {
        "identifier": str(active_issue.get("identifier") or "").strip(),
        "title": str(active_issue.get("title") or "").strip(),
        "external_url": str(active_issue.get("external_url") or "").strip(),
        "project_key": str(active_issue.get("project_key") or "").strip(),
        "project_name": str(active_issue.get("project_name") or "").strip(),
        "worktree": str(active_issue.get("worktree") or "").strip(),
        "branch": str(active_issue.get("branch") or "").strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _active_issue_snapshot(project_root: Path) -> dict[str, object] | None:
    kanban_state = read_json(project_root / ".memory" / "runner" / "KANBAN_STATE.json")
    if not isinstance(kanban_state, dict):
        return None
    active_issue = kanban_state.get("active_issue")
    if not isinstance(active_issue, dict):
        return None
    return active_issue


def _orx_execution_packet(project_root: Path, *, project: str) -> dict[str, object] | None:
    active_issue = _active_issue_snapshot(project_root)
    existing = active_issue.get("execution_packet") if isinstance(active_issue, dict) else None
    if isinstance(existing, dict) and existing.get("active_slice_id"):
        return existing
    try:
        return fetch_execution_packet(project_key=project)
    except OrxControlError:
        return existing if isinstance(existing, dict) else None


def _is_orx_execution_packet(packet: dict[str, object] | None) -> bool:
    if not isinstance(packet, dict):
        return False
    active_slice_id = str(packet.get("active_slice_id") or "").strip()
    issue_key = str(packet.get("issue_key") or "").strip()
    return bool(active_slice_id and issue_key)


def _parse_orx_runner_result(text: str) -> tuple[dict[str, object] | None, str | None]:
    raw_lines = text.splitlines()
    start_idx = -1
    end_idx = -1
    for idx, raw in enumerate(raw_lines):
        line = raw.strip()
        if line == RUNNER_RESULT_START_MARKER:
            start_idx = idx
            end_idx = -1
            continue
        if line == RUNNER_RESULT_END_MARKER and start_idx != -1:
            end_idx = idx

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx + 1:
        return None, "missing RUNNER_RESULT_START/END block"

    payload_text = "\n".join(raw_lines[start_idx + 1 : end_idx]).strip()
    if not payload_text:
        return None, "empty runner result payload"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return None, f"invalid runner result json: {exc}"
    if not isinstance(payload, dict):
        return None, "runner result payload must be a JSON object"

    status = str(payload.get("status") or "").strip().lower()
    if status not in {"success", "blocked", "failed"}:
        return None, "runner result status must be success, blocked, or failed"
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        return None, "runner result summary must be a non-empty string"
    if not isinstance(payload.get("verified"), bool):
        return None, "runner result verified must be boolean"

    next_slice = payload.get("next_slice")
    if next_slice is not None and not isinstance(next_slice, str):
        return None, "runner result next_slice must be a string when present"

    if "artifacts" not in payload:
        payload["artifacts"] = []
    if not isinstance(payload.get("artifacts"), list):
        return None, "runner result artifacts must be a list"
    payload["artifacts"] = [str(item).strip() for item in payload["artifacts"] if str(item).strip()]

    for field in (
        "blockers",
        "risks",
        "lessons",
        "verification_ran",
        "verification_failed",
        "touched_paths",
    ):
        if field not in payload:
            continue
        if not isinstance(payload[field], list):
            return None, f"runner result {field} must be a list"
        payload[field] = [str(item).strip() for item in payload[field] if str(item).strip()]

    metrics = payload.get("metrics")
    if metrics is None:
        payload["metrics"] = {}
    elif not isinstance(metrics, dict):
        return None, "runner result metrics must be an object"

    for field in ("owner_mismatch", "scope_mismatch", "needs_human_help"):
        if field in payload and payload[field] is not None and not isinstance(payload[field], bool):
            return None, f"runner result {field} must be boolean when present"
    for field in ("next_step_hint",):
        if field in payload and payload[field] is not None and not isinstance(payload[field], str):
            return None, f"runner result {field} must be a string when present"

    if "next_slice" not in payload:
        payload["next_slice"] = None
    return payload, None


def _build_runner_dispatch_prompt(
    *,
    prompt_name: str,
    project: str,
    runner_id: str,
    project_root: Path,
    phase: str | None,
) -> str:
    issue_context = _active_issue_dispatch_context(project_root, project=project)
    execution_packet = _orx_execution_packet(project_root, project=project)
    header_lines = [
        f"RUNNER_DISPATCH mode={prompt_name} project={project} runner_id={runner_id}",
        f"PROJECT_ROOT={project_root}",
    ]
    if phase:
        header_lines.append(f"PHASE={phase}")
    if issue_context.get("identifier"):
        header_lines.append(f"LINEAR_ISSUE={issue_context['identifier']}")
    if issue_context.get("title"):
        header_lines.append(f"LINEAR_TITLE={issue_context['title']}")
    if issue_context.get("external_url"):
        header_lines.append(f"LINEAR_URL={issue_context['external_url']}")
    if issue_context.get("project_key"):
        header_lines.append(f"LINEAR_PROJECT={issue_context['project_key']}")
    if issue_context.get("project_name"):
        header_lines.append(f"LINEAR_PROJECT_NAME={issue_context['project_name']}")
    if issue_context.get("worktree"):
        header_lines.append(f"LINEAR_WORKTREE={issue_context['worktree']}")
    if issue_context.get("branch"):
        header_lines.append(f"LINEAR_BRANCH={issue_context['branch']}")

    template = _load_prompt_template(prompt_name)
    packet_block = ""
    if isinstance(execution_packet, dict):
        packet_block = "\n".join(
            [
                "## ORX Execution Packet",
                "Use this packet as the live source of truth for the current slice.",
                "```json",
                json.dumps(execution_packet, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    return "\n".join(
        [
            *header_lines,
            "",
            *(packet_block.splitlines() if packet_block else []),
            "Follow the instruction block below exactly.",
            "",
            template,
        ]
    )


def _normalize_prompt_capture(text: str) -> str:
    """Collapse pane output to make prompt matching resilient to wrapping."""
    return " ".join(text.split())


def _pane_contains_exact_prompt(pane: str, command: str) -> bool:
    normalized_command = _normalize_prompt_capture(command)
    normalized_pane = _normalize_prompt_capture(pane)
    if normalized_command and normalized_command in normalized_pane:
        return True
    first_line = next((line.strip() for line in command.splitlines() if line.strip()), "")
    if first_line:
        return first_line in normalized_pane
    return False


def _pane_contains_empty_prompt_args(pane: str, command_anchor: str) -> bool:
    normalized = _normalize_prompt_capture(pane)
    if command_anchor not in normalized:
        return False
    if "missing required args" in normalized.lower():
        return True
    return 'DEV=""' in normalized and 'PROJECT=""' in normalized and 'PROJECT_ROOT=""' in normalized


def _pane_contains_direct_runner_prompt_body(pane: str, command: str) -> bool:
    normalized = _normalize_prompt_capture(pane).lower()
    if not normalized or _pane_contains_exact_prompt(pane, command):
        return False
    direct_markers = (
        "runner_dispatch mode=",
        "runner context from `/prompts:",
        "runner context from `/run_",
        "use this command to execute exactly one medium bounded infinite-runner work slice.",
        "use this command to refresh infinite-runner state after one execute slice finishes.",
        "## scope first",
        "## execution contract",
        "## command",
        "## handoff",
        "write prepared marker:",
    )
    working_markers = (
        "working (",
        "to interrupt",
        "thinking",
        "model interrupted to submit steer instructions",
    )
    return any(marker in normalized for marker in direct_markers + working_markers)


def _dismiss_runner_prompt(tmux: TmuxClient, session_name: str) -> None:
    """Best-effort prompt dismissal so the controller can retry cleanly."""
    tmux.send_escape(session_name)
    time.sleep(0.03)
    tmux.send_escape(session_name)
    time.sleep(0.03)
    tmux.clear_prompt_line(session_name)


def _ensure_runner_prompt_active(
    *,
    tmux: TmuxClient,
    session_name: str,
    activate_attempts: int = 3,
    settle_delay_seconds: float = 0.08,
) -> bool:
    """Return True only when the Codex chat input prompt is visibly active."""
    for attempt in range(activate_attempts):
        pane = tmux.capture_pane(session_name, lines=LINES_STATUS) or ""
        if has_explicit_codex_prompt(pane):
            return True
        tmux.send_escape(session_name)
        time.sleep(settle_delay_seconds)
        pane = tmux.capture_pane(session_name, lines=LINES_STATUS) or ""
        if has_explicit_codex_prompt(pane):
            return True
        if attempt + 1 < activate_attempts:
            time.sleep(settle_delay_seconds)
    return False


def _submit_runner_prompt(
    *,
    tmux: TmuxClient,
    session_name: str,
    command: str,
    settle_attempts: int = 12,
    settle_delay_seconds: float = 0.08,
    submit_attempts: int = 2,
    visible_hold_seconds: float = 0.0,
) -> tuple[bool, str | None]:
    """Stage and submit one deterministic runner prompt body only after it is visible.

    Older Codex builds exposed custom slash commands here. Current builds may not,
    so tmux-codex now treats the prompt files as deterministic templates and
    injects the rendered body directly into the live Codex session.
    """
    command_anchor = next((line.strip() for line in command.splitlines() if line.strip()), "")

    if not _ensure_runner_prompt_active(
        tmux=tmux,
        session_name=session_name,
        settle_delay_seconds=settle_delay_seconds,
    ):
        return False, "prompt_not_active"

    for attempt in range(submit_attempts):
        tmux.clear_prompt_line(session_name)
        sent = tmux.send_keys(
            session_name,
            command,
            enter=False,
            delay_ms=0,
            force_buffer=True,
        )
        if not sent:
            return False, "tmux_send_keys_failed"

        command_visible = False
        for _ in range(settle_attempts):
            pane = tmux.capture_pane(session_name, lines=LINES_STATUS) or ""
            if _pane_contains_exact_prompt(pane, command):
                command_visible = True
                break
            time.sleep(settle_delay_seconds)

        if not command_visible:
            if attempt + 1 < submit_attempts:
                _dismiss_runner_prompt(tmux, session_name)
                continue
            time.sleep(settle_delay_seconds)

        if visible_hold_seconds > 0:
            time.sleep(visible_hold_seconds)

        if not tmux.press_enter(session_name):
            return False, "prompt_submit_enter_failed"

        expansion_ready = False
        bad_expansion = False
        direct_submission_ready = False
        for _ in range(settle_attempts):
            pane = tmux.capture_pane(session_name, lines=LINES_STATUS) or ""
            lower = pane.lower()
            if _pane_contains_empty_prompt_args(pane, command_anchor):
                bad_expansion = True
                break
            if "send saved prompt" in lower:
                expansion_ready = True
                break
            if _pane_contains_direct_runner_prompt_body(pane, command):
                direct_submission_ready = True
                break
            time.sleep(settle_delay_seconds)

        if bad_expansion:
            _dismiss_runner_prompt(tmux, session_name)
            if attempt + 1 < submit_attempts:
                continue
            return False, "empty_prompt_args_expansion"

        if direct_submission_ready or "\n" in command:
            return True, None

        if not expansion_ready:
            if attempt + 1 < submit_attempts:
                _dismiss_runner_prompt(tmux, session_name)
                continue
            return False, "saved_prompt_expansion_not_ready"

        if not tmux.press_enter(session_name):
            return False, "prompt_submit_enter_failed"
        return True, None

    return False, "exact_prompt_not_visible"


def make_codex_interactive_runner_script(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    paths: RunnerPaths,
) -> str:
    """Generate interactive Codex launcher plus internal background controller."""
    repo_home = _repo_home()
    q_project = shlex.quote(project)
    q_dev = shlex.quote(dev)
    q_model = shlex.quote(model)
    q_reasoning_effort = shlex.quote(reasoning_effort)
    q_runner_log = shlex.quote(str(paths.runner_log))
    q_stop_lock = shlex.quote(str(paths.stop_file))
    q_done_lock = shlex.quote(str(paths.complete_lock))
    project_root = paths.memory_dir.parent
    q_project_root = shlex.quote(str(project_root))
    q_runner_id = shlex.quote(runner_id)
    q_session_name = shlex.quote(f"runner-{project}")
    q_repo_home = shlex.quote(str(repo_home))
    return f'''\
cd {q_project_root} || exit 1
export TMUX_CLI_HOME="${{TMUX_CLI_HOME:-{repo_home}}}"
RUNNER_LOG={q_runner_log}
STOP_LOCK={q_stop_lock}
DONE_LOCK={q_done_lock}
PROJECT_ROOT={q_project_root}
mkdir -p "$(dirname "$RUNNER_LOG")"
log_supervisor() {{
  echo "[$(date +%H:%M:%S)] $1" | tee -a "$RUNNER_LOG"
}}
log_supervisor "starting infinite runner for {q_project}"
while true; do
  if [[ -f "$STOP_LOCK" || -f "$DONE_LOCK" ]]; then
    log_supervisor "lock detected before launch; stopping infinite runner"
    break
  fi

  RUNNER_MODEL={q_model}
  RUNNER_REASONING_EFFORT={q_reasoning_effort}
  RUNNER_MODEL_PROFILE=""
  RUNNER_PROFILE_REASON=""
  RUNNER_SEAM_ID=""
  RUNNER_SEAM_TITLE=""
  RUNNER_TASK_ID=""
  RUNNER_TASK_TITLE=""
  RUNNER_PROFILE_SOURCE="fallback"
  PROFILE_EXPORTS=$(cd {q_repo_home} && PYTHONPATH={q_repo_home}${{PYTHONPATH:+:$PYTHONPATH}} python3 -m src.main __runner-profile \
    --project {q_project} \
    --runner-id {q_runner_id} \
    --dev {q_dev} \
    --default-model {q_model} \
    --default-reasoning-effort {q_reasoning_effort} \
    --format shell 2>>"$RUNNER_LOG")
  PROFILE_RC=$?
  if [[ $PROFILE_RC -eq 0 && -n "$PROFILE_EXPORTS" ]]; then
    eval "$PROFILE_EXPORTS"
  else
    log_supervisor "profile resolution failed rc=$PROFILE_RC; using launcher defaults"
  fi
  log_supervisor "launching cycle model_profile=${{RUNNER_MODEL_PROFILE:-fallback}} model=$RUNNER_MODEL effort=$RUNNER_REASONING_EFFORT seam_id=${{RUNNER_SEAM_ID:-}} source=${{RUNNER_PROFILE_SOURCE:-fallback}}"

  (cd {q_repo_home} && PYTHONPATH={q_repo_home}${{PYTHONPATH:+:$PYTHONPATH}} python3 -m src.main __runner-controller \
    --project {q_project} \
    --runner-id {q_runner_id} \
    --session-name {q_session_name} \
    --dev {q_dev}) >>"$RUNNER_LOG" 2>&1 &
  CONTROLLER_PID=$!
  log_supervisor "cycle controller pid=$CONTROLLER_PID"

  codex --search --dangerously-bypass-approvals-and-sandbox \
    -m "$RUNNER_MODEL" \
    -c model_reasoning_effort="$RUNNER_REASONING_EFFORT"
  CODEX_RC=$?
  wait "$CONTROLLER_PID"
  CONTROLLER_RC=$?
  log_supervisor "cycle ended codex_rc=$CODEX_RC controller_rc=$CONTROLLER_RC"

  ARCHIVE_SUMMARY=$(cd {q_repo_home} && PYTHONPATH={q_repo_home}${{PYTHONPATH:+:$PYTHONPATH}} python3 -m src.main __runner-archive \
    --project-root "$PROJECT_ROOT" \
    --keep 0 \
    --format text 2>>"$RUNNER_LOG")
  ARCHIVE_RC=$?
  if [[ -n "$ARCHIVE_SUMMARY" ]]; then
    log_supervisor "$ARCHIVE_SUMMARY"
  fi
  if [[ $ARCHIVE_RC -ne 0 ]]; then
    log_supervisor "runner thread archive failed rc=$ARCHIVE_RC"
  fi

  if [[ -f "$STOP_LOCK" || -f "$DONE_LOCK" ]]; then
    log_supervisor "lock detected after cycle; stopping infinite runner"
    break
  fi

  sleep 0.3
done

exec zsh -l
'''


def make_codex_exec_loop_script(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    paths: RunnerPaths,
) -> str:
    """Compatibility wrapper for tests/callers; public runner uses interactive mode."""
    return make_codex_interactive_runner_script(
        dev=dev,
        project=project,
        runner_id=runner_id,
        model=model,
        reasoning_effort=reasoning_effort,
        paths=paths,
    )


def _validate_gates_contract(gates_file: Path) -> tuple[bool, str]:
    if not gates_file.exists():
        return False, f"Missing gates file: {gates_file}"

    cmd = f'set -euo pipefail; source "{gates_file}"; declare -F run_gates >/dev/null'
    result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"gates.sh must define run_gates: {gates_file}"
    return True, ""


def _run_gates(gates_file: Path, project_root: Path, runner_id: str) -> tuple[bool, str]:
    cmd = f'set -euo pipefail; cd "{project_root}"; source "{gates_file}"; run_gates'
    env = os.environ.copy()
    env["RUNNER_ID"] = runner_id
    env["MEMORY_DIR"] = str(project_root / ".memory")
    result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, env=env)
    if result.returncode == 0:
        return True, result.stdout.strip()
    output = (result.stdout or "") + (result.stderr or "")
    return False, output.strip()


def _extract_open_tasks(tasks_file: Path, max_items: int = 40) -> list[str]:
    payload = read_json(tasks_file)
    if not isinstance(payload, dict):
        return []
    tasks = payload.get("seams")
    id_key = "seam_id"
    if not isinstance(tasks, list):
        tasks = payload.get("tasks")
        id_key = "task_id"
    if not isinstance(tasks, list):
        return []

    items: list[str] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status not in {"open", "in_progress", "blocked"}:
            continue
        task_id = str(raw.get(id_key, "")).strip() or "(id)"
        title = str(raw.get("title", "")).strip() or "(untitled)"
        items.append(f"{task_id}: {title}"[:220])
        if len(items) >= max_items:
            break
    return items


def _extract_open_runner_backlog(project_root: Path, *, project: str | None = None) -> tuple[list[str], str]:
    project_key = (project or project_root.name).strip() or project_root.name
    execution_packet = _orx_execution_packet(project_root, project=project_key)
    packet_issue = _issue_context_from_execution_packet(execution_packet)
    if packet_issue:
        identifier = str(packet_issue.get("identifier") or "").strip()
        title = str(packet_issue.get("title") or "").strip()
        if identifier and title:
            return [f"{identifier}: {title}"[:220]], "ORX execution packet"

    active_backlog_file = project_root / ".memory" / "runner" / "RUNNER_ACTIVE_BACKLOG.json"
    active_backlog = read_json(active_backlog_file)
    if isinstance(active_backlog, dict):
        active_issue = active_backlog.get("kanban")
        items: list[str] = []
        if isinstance(active_issue, dict):
            identifier = str(active_issue.get("active_issue_identifier") or "").strip()
            title = str(active_issue.get("active_issue_title") or "").strip()
            if identifier and title:
                items.append(f"{identifier}: {title}"[:220])
        raw_entries = active_backlog.get("active_backlog")
        if isinstance(raw_entries, list):
            for raw in raw_entries:
                if not isinstance(raw, dict):
                    continue
                task_id = str(raw.get("task_id") or raw.get("identifier") or "").strip()
                title = str(raw.get("title") or "").strip()
                if not task_id or not title:
                    continue
                entry = f"{task_id}: {title}"[:220]
                if entry not in items:
                    items.append(entry)
                if len(items) >= 4:
                    break
        if items:
            return items, "RUNNER_ACTIVE_BACKLOG.json"

    seams_file = project_root / ".memory" / "runner" / "SEAMS.json"
    open_items = _extract_open_tasks(seams_file)
    if open_items:
        return open_items, "SEAMS.json"

    tasks_file = project_root / ".memory" / "runner" / "TASKS.json"
    fallback_items = _extract_open_tasks(tasks_file)
    if fallback_items:
        return fallback_items, "TASKS.json"
    return [], "SEAMS.json"


def _append_ledger(paths: RunnerStatePaths, event: str, **payload: object) -> None:
    append_ndjson(paths.ledger_file, {"ts": utc_now(), "event": event, **payload})


def _mark_runner_end_time(paths: RunnerStatePaths, session_name: str) -> None:
    """Backfill end timestamp for the active session in runners.log."""
    if not paths.runners_log.exists():
        return

    end_ts = int(time.time())
    lines: list[str] = []
    for line in paths.runners_log.read_text().splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0] == session_name and (len(parts) < 3 or not parts[2]):
            lines.append(f"{parts[0]},{parts[1]},{end_ts}")
        else:
            lines.append(line)
    paths.runners_log.write_text("\n".join(lines) + ("\n" if lines else ""))


def _log_line(paths: RunnerStatePaths, message: str) -> None:
    stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    paths.runner_log.parent.mkdir(parents=True, exist_ok=True)
    with paths.runner_log.open("a", encoding="utf-8") as handle:
        handle.write(stamped + "\n")
    # Keep file logs timestamped but preserve a CLI-like pane view.
    print(message, flush=True)


def _build_prompt(project: str, runner_id: str, paths: RunnerStatePaths) -> str:
    state = read_json(paths.state_file) or {}
    project_root = paths.memory_dir.parent
    execution_packet = _orx_execution_packet(project_root, project=project)
    packet_issue = _issue_context_from_execution_packet(execution_packet)
    kanban_state = {} if packet_issue else (read_json(paths.kanban_state_json) or {})
    active_backlog = {} if packet_issue else (read_json(paths.active_backlog_json) or {})

    def _compact_list(raw: object, *, limit: int = 3, item_chars: int = 140) -> list[str]:
        if not isinstance(raw, list):
            return []
        compact: list[str] = []
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            compact.append(text[:item_chars])
            if len(compact) >= limit:
                break
        return compact

    plan_items = _compact_list(state.get("implementation_plan"), limit=2, item_chars=160)
    completed_items = _compact_list(state.get("completed_recent"), limit=3, item_chars=120)
    blocker_items = _compact_list(state.get("blockers"), limit=3, item_chars=120)
    snapshot = {
        "objective_id": str(state.get("objective_id", "")).strip(),
        "active_seam_id": str(state.get("active_seam_id", "")).strip(),
        "next_seam_id": str(state.get("next_seam_id", "") or state.get("next_task_id", "")).strip(),
        "next_task_id": str(state.get("next_task_id", "")).strip(),
        "current_goal": str(state.get("current_goal", "")).strip(),
        "next_seam": str(state.get("next_seam", "") or state.get("next_task", "")).strip(),
        "next_seam_reason": str(state.get("next_seam_reason", "") or state.get("next_task_reason", "")).strip(),
        "next_task": str(state.get("next_task", "")).strip(),
        "next_task_reason": str(state.get("next_task_reason", "")).strip(),
        "implementation_plan_top2": plan_items,
        "completed_recent_top3": completed_items,
        "blockers_top3": blocker_items,
        "last_iteration_summary": str(state.get("last_iteration_summary", "")).strip(),
        "done_candidate": bool(state.get("done_candidate", False)),
        "done_gate_status": str(state.get("done_gate_status", "pending")).strip(),
        "status": str(state.get("status", "ready")).strip(),
        "linear_issue": {
            "identifier": str(
                packet_issue.get("identifier")
                or (((kanban_state.get("active_issue") or {}) if isinstance(kanban_state, dict) else {}).get("identifier"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("active_issue_identifier"))
                or ""
            ).strip(),
            "title": str(
                packet_issue.get("title")
                or (((kanban_state.get("active_issue") or {}) if isinstance(kanban_state, dict) else {}).get("title"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("active_issue_title"))
                or ""
            ).strip(),
            "url": str(
                packet_issue.get("external_url")
                or (((kanban_state.get("active_issue") or {}) if isinstance(kanban_state, dict) else {}).get("url"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("active_issue_url"))
                or ""
            ).strip(),
            "project_key": str(
                packet_issue.get("project_key")
                or (((kanban_state.get("active_issue") or {}) if isinstance(kanban_state, dict) else {}).get("project_key"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("active_issue_project_key"))
                or project
            ).strip(),
            "worktree": str(
                packet_issue.get("worktree")
                or (((kanban_state.get("active_checkout") or {}) if isinstance(kanban_state, dict) else {}).get("worktree"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("kanban_checkout_worktree"))
                or ""
            ).strip(),
            "branch": str(
                packet_issue.get("branch")
                or (((kanban_state.get("active_checkout") or {}) if isinstance(kanban_state, dict) else {}).get("branch"))
                or (((active_backlog.get("kanban") or {}) if isinstance(active_backlog, dict) else {}).get("kanban_checkout_branch"))
                or ""
            ).strip(),
        },
        "active_backlog_top4": []
        if packet_issue
        else (active_backlog.get("active_backlog", [])[:4] if isinstance(active_backlog, dict) else []),
        "execution_packet": execution_packet if isinstance(execution_packet, dict) else None,
    }
    state_context = json.dumps(snapshot, indent=2)

    return f"""Continue work for project {project}.

Completion protocol:
- Runner ID: {runner_id}
- Execution lane: ORX-selected Linear issue for `{project}`
- Runner state file: {paths.state_file}
- Kanban state file: {paths.kanban_state_json}
- Active backlog file: {paths.active_backlog_json}
- Done lock path: {paths.done_lock}
- Stop lock path: {paths.stop_lock}
- Gates file: {paths.gates_file}

Rules:
1. Execute exactly one meaningful unit of progress this iteration.
2. Update code and tests as needed.
3. Do not create the done lock early.
4. Before creating done lock, source .memory/gates.sh and run run_gates.
5. Only create done lock if run_gates succeeds and the active ORX/Linear backlog is actually empty for this runner cycle.
6. Treat ORX + Linear as the source of truth for what this runner should do. If an ORX execution packet is present, treat it as the only live objective source for this cycle. KANBAN_STATE.json, RUNNER_ACTIVE_BACKLOG.json, RUNNER_EXEC_CONTEXT.json, optional .memory/runner/graph/RUNNER_GRAPH_ACTIVE_SLICE.json, and runner state are cache/recovery context only. Legacy TASKS.json / SEAMS.json files are compatibility inputs, not the planner of record. Treat RUNNER_HANDOFF.md as human/manual recovery context, not default per-cycle input.
7. End your response with this exact machine-parsable block:
RUNNER_UPDATE_START
{{"summary":"...","completed":["..."],"completed_seam_ids":["TT-..."],"next_seam":"...","next_seam_reason":"...","blockers":["..."],"remaining_gaps":["..."],"done_candidate":false}}
RUNNER_UPDATE_END
8. The JSON must be valid and include all required keys exactly once.
9. Before finalizing, review your work against the active acceptance and ask: "Any problems with the current implementation?" Put every real remaining issue in `remaining_gaps`.
10. If you fully completed an active seam this slice, list its canonical seam id in `completed_seam_ids`. Do not rely on prose alone.

Runner state snapshot:
{state_context}
"""


def _extract_update_payload(raw_lines: list[str]) -> tuple[dict[str, object] | None, str | None]:
    """Parse the final deterministic RUNNER_UPDATE payload block."""
    start_idx = -1
    end_idx = -1
    for idx, raw in enumerate(raw_lines):
        line = raw.strip()
        if line == RUNNER_UPDATE_START_MARKER:
            start_idx = idx
            end_idx = -1
            continue
        if line == RUNNER_UPDATE_END_MARKER and start_idx != -1:
            end_idx = idx

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx + 1:
        return None, "missing RUNNER_UPDATE_START/END block"

    payload_text = "\n".join(raw_lines[start_idx + 1 : end_idx]).strip()
    if not payload_text:
        return None, "empty runner update payload"

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return None, f"invalid runner update json: {exc}"

    if not isinstance(payload, dict):
        return None, "runner update payload must be a JSON object"

    seam_keys_missing = [key for key in REQUIRED_UPDATE_KEYS if key not in payload]
    legacy_keys = ("completed_task_ids", "next_task", "next_task_reason")
    if seam_keys_missing:
        if not all(key in payload for key in legacy_keys):
            return None, f"runner update missing required key: {seam_keys_missing[0]}"
        payload = {
            **payload,
            "completed_seam_ids": payload.get("completed_task_ids"),
            "next_seam": payload.get("next_task"),
            "next_seam_reason": payload.get("next_task_reason"),
        }

    summary = payload.get("summary")
    next_seam = payload.get("next_seam")
    next_seam_reason = payload.get("next_seam_reason")
    completed = payload.get("completed")
    completed_seam_ids = payload.get("completed_seam_ids")
    blockers = payload.get("blockers")
    remaining_gaps = payload.get("remaining_gaps")
    done_candidate = payload.get("done_candidate")

    if not isinstance(summary, str) or not summary.strip():
        return None, "runner update summary must be a non-empty string"
    if not isinstance(next_seam, str) or not next_seam.strip():
        return None, "runner update next_seam must be a non-empty string"
    if not isinstance(next_seam_reason, str) or not next_seam_reason.strip():
        return None, "runner update next_seam_reason must be a non-empty string"
    if not isinstance(done_candidate, bool):
        return None, "runner update done_candidate must be a boolean"
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        return None, "runner update completed must be a string array"
    if not isinstance(completed_seam_ids, list) or not all(isinstance(item, str) for item in completed_seam_ids):
        return None, "runner update completed_seam_ids must be a string array"
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        return None, "runner update blockers must be a string array"
    if not isinstance(remaining_gaps, list) or not all(isinstance(item, str) for item in remaining_gaps):
        return None, "runner update remaining_gaps must be a string array"

    normalized_remaining_gaps = [item.strip() for item in remaining_gaps if item.strip()]
    if normalized_remaining_gaps:
        done_candidate = False

    normalized: dict[str, object] = {
        "summary": summary.strip(),
        "completed": [item.strip() for item in completed if item.strip()],
        "completed_seam_ids": [item.strip() for item in completed_seam_ids if item.strip()],
        "completed_task_ids": [item.strip() for item in completed_seam_ids if item.strip()],
        "next_seam": next_seam.strip(),
        "next_task": next_seam.strip(),
        "next_seam_reason": next_seam_reason.strip(),
        "next_task_reason": next_seam_reason.strip(),
        "blockers": [item.strip() for item in blockers if item.strip()],
        "remaining_gaps": normalized_remaining_gaps,
        "done_candidate": done_candidate,
    }
    return normalized, None


def _infer_done_candidate_from_text(final_message: str, raw_lines: list[str]) -> bool:
    text = "\n".join([final_message, *raw_lines[-40:]])
    if any(pattern.search(text) for pattern in DONE_SIGNAL_FALSE_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in DONE_SIGNAL_TRUE_PATTERNS)


def _build_finalize_hook_prompt(
    final_message: str,
    parse_error: str,
    state: dict[str, object],
) -> str:
    safe_message = final_message.strip() or "(no final message)"
    current_next = str(state.get("next_seam", "") or state.get("next_task", "")).strip() or "Execute the next concrete validated slice."
    current_reason = str(state.get("next_seam_reason", "") or state.get("next_task_reason", "")).strip() or "Carry forward prior plan context."
    return f"""The previous runner response failed strict parsing.

Parse failure:
{parse_error}

Last assistant message:
{safe_message}

Return ONLY this block format:
RUNNER_UPDATE_START
{{"summary":"...","completed":["..."],"completed_seam_ids":["TT-..."],"next_seam":"...","next_seam_reason":"...","blockers":["..."],"remaining_gaps":["..."],"done_candidate":false}}
RUNNER_UPDATE_END

Rules:
- Keep JSON valid with all required keys.
- If the work is clearly complete, set "done_candidate": true.
- If uncertain, set "done_candidate": false.
- If any real issue, rough edge, regression, or acceptance gap remains, list it in "remaining_gaps" and do not mark done.
- If you fully completed the active seam, include its canonical seam id in "completed_seam_ids".
- Preserve continuity with current next task when no better signal exists:
  next_seam="{current_next}"
  next_seam_reason="{current_reason}"
"""


def _fallback_update_payload(
    state: dict[str, object],
    final_message: str,
    parse_error: str,
    probe_error: str | None,
    raw_lines: list[str],
) -> dict[str, object]:
    summary = final_message.strip() or "Iteration completed without structured runner update."
    inferred_done_candidate = _infer_done_candidate_from_text(final_message=final_message, raw_lines=raw_lines)
    prior_completed = state.get("completed_recent", [])
    completed: list[str] = []
    if isinstance(prior_completed, list):
        completed = [str(item).strip() for item in prior_completed if str(item).strip()]

    blockers: list[str] = []
    prior_blockers = state.get("blockers", [])
    if isinstance(prior_blockers, list):
        blockers.extend([str(item).strip() for item in prior_blockers if str(item).strip()])
    blockers.append(f"runner update parse fallback used: {parse_error}")
    if probe_error:
        blockers.append(f"finalize hook parse failure: {probe_error}")

    remaining_gaps: list[str] = [
        "Structured self-review was unavailable because the runner update payload could not be parsed.",
    ]
    remaining_gaps.extend(blockers[:2])
    done_candidate = False

    if inferred_done_candidate:
        next_seam = str(state.get("next_seam", "") or state.get("next_task", "")).strip() or "Review the prior slice for any remaining gaps."
        next_seam_reason = (
            "Completion was inferred from free-form output, but structured self-review failed; "
            "keep the current seam open until remaining gaps are explicitly cleared."
        )
    else:
        next_seam = str(state.get("next_seam", "") or state.get("next_task", "")).strip() or "Execute the next concrete validated slice."
        next_seam_reason = str(state.get("next_seam_reason", "") or state.get("next_task_reason", "")).strip() or (
            "Structured runner update missing; carrying forward previous next step."
        )

    return {
        "summary": summary,
        "completed": completed,
        "completed_seam_ids": [],
        "completed_task_ids": [],
        "next_seam": next_seam,
        "next_task": next_seam,
        "next_seam_reason": next_seam_reason,
        "next_task_reason": next_seam_reason,
        "blockers": blockers,
        "remaining_gaps": remaining_gaps,
        "done_candidate": done_candidate,
    }


def _resolve_iteration_update(
    paths: RunnerStatePaths,
    project_root: Path,
    model: str,
    reasoning_effort: str,
    state: dict[str, object],
    result: CodexRunResult,
    iteration: int,
) -> tuple[dict[str, object], str, str | None, str | None]:
    update_payload, parse_error = _extract_update_payload(result.raw_lines)
    finalize_mode = "direct"
    finalize_probe_error: str | None = None
    if parse_error is None and update_payload is not None:
        return update_payload, finalize_mode, None, None

    _append_ledger(paths, "iteration.update_parse_failed", iteration=iteration, error=parse_error)
    _log_line(paths, f"Runner update parse failed: {parse_error}")
    finalize_mode = "probe"
    _append_ledger(paths, "iteration.finalize_probe.start", iteration=iteration)
    probe_result = run_codex_iteration(
        cwd=project_root,
        model=model,
        prompt=_build_finalize_hook_prompt(
            final_message=result.final_message,
            parse_error=str(parse_error),
            state=state,
        ),
        session_id=None,
        reasoning_effort=reasoning_effort,
        json_stream=False,
        logger=lambda line: _log_line(paths, f"[finalize-hook] {line}"),
    )
    _append_ledger(
        paths,
        "iteration.finalize_probe.finish",
        iteration=iteration,
        exit_code=probe_result.exit_code,
        final_message=probe_result.final_message,
    )
    probe_payload, probe_error = _extract_update_payload(probe_result.raw_lines)
    if probe_payload is not None:
        return probe_payload, finalize_mode, None, None

    finalize_mode = "fallback"
    finalize_probe_error = probe_error
    fallback_payload = _fallback_update_payload(
        state=state,
        final_message=result.final_message,
        parse_error=str(parse_error),
        probe_error=probe_error,
        raw_lines=result.raw_lines,
    )
    _append_ledger(
        paths,
        "iteration.update_fallback_used",
        iteration=iteration,
        parse_error=parse_error,
        probe_error=probe_error,
        inferred_done=bool(fallback_payload.get("done_candidate", False)),
    )
    return fallback_payload, finalize_mode, parse_error, finalize_probe_error


def _apply_iteration_update(
    paths: RunnerStatePaths,
    state: dict[str, object],
    update_payload: dict[str, object],
    session_id: str | None,
) -> dict[str, object]:
    completed_recent = list(update_payload.get("completed", []))
    completed_seam_ids = list(update_payload.get("completed_seam_ids", update_payload.get("completed_task_ids", [])))
    blockers = list(update_payload.get("blockers", []))
    remaining_gaps = list(update_payload.get("remaining_gaps", []))
    next_seam = str(update_payload.get("next_seam", update_payload.get("next_task", ""))).strip()
    next_seam_reason = str(update_payload.get("next_seam_reason", update_payload.get("next_task_reason", ""))).strip()
    summary = str(update_payload.get("summary", "")).strip()
    done_candidate = bool(update_payload.get("done_candidate", False))
    for gap in remaining_gaps:
        gap_text = str(gap).strip()
        if not gap_text:
            continue
        blocker_text = f"Self-review gap: {gap_text}"[:220]
        if blocker_text not in blockers:
            blockers.append(blocker_text)
    state = update_state(
        paths.state_file,
        state,
        session_id=session_id,
        current_goal=next_seam,
        last_iteration_summary=summary,
        completed_recent=completed_recent,
        completed_seam_ids=completed_seam_ids,
        completed_task_ids=completed_seam_ids,
        next_seam=next_seam,
        next_task=next_seam,
        next_seam_reason=next_seam_reason,
        next_task_reason=next_seam_reason,
        blockers=blockers,
        done_candidate=done_candidate,
        done_gate_status="pending",
        runtime_policy={
            "runner_mode": "exec",
            "session_strategy": "fresh_session",
        },
    )
    return state


def run_loop_runner(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    session_name: str,
    reasoning_effort: str = "high",
    backoff_seconds: int = 2,
) -> int:
    """Run codex loop until stop/done criteria are met."""
    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    if not project_root.exists():
        print(f"ERROR: project not found: {project_root}")
        return 2

    ensure_gates_file(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )

    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    ensure_memory_dir(paths)

    create_result = create_runner_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        approve_enable=None,
        project_root=project_root,
    )
    if not create_result.get("ok"):
        print(f"ERROR: {create_result.get('error')}")
        return 2

    state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
    state = update_state(
        paths.state_file,
        state,
        **detect_git_context(project_root),
        runtime_policy={
            "runner_mode": "exec",
            "session_strategy": "fresh_session",
        },
    )

    ok, err = _validate_gates_contract(paths.gates_file)
    if not ok:
        _append_ledger(paths, "runner.invalid_gates", message=err)
        _log_line(paths, f"ERROR {err}")
        update_state(paths.state_file, state, status="invalid_gates")
        return 2

    if not bool(state.get("enabled")):
        token = create_result.get("enable_token")
        pending = create_result.get("enable_pending_file")
        _append_ledger(paths, "runner.blocked_enable", token=token, pending_file=pending)
        _log_line(paths, "Runner disabled until HIL enable approval is provided.")
        if pending and token:
            runctl_path = _repo_home() / "bin" / "runctl"
            _log_line(
                paths,
                (
                    f"Run: python3 {runctl_path} --setup --project-root {project_root} "
                    f"--runner-id {runner_id} --approve-enable {token}"
                ),
            )
        update_state(paths.state_file, state, status="blocked_enable")
        return 3

    handlers = [LocalHooks(memory_dir=paths.runner_dir, hooks_log=paths.hooks_log)]
    agents = load_agents_bridge()
    if agents is not None:
        handlers.append(agents)
    hooks = HookAdapter(handlers)

    _append_ledger(
        paths,
        "runner.start",
        default_model=model,
        default_reasoning_effort=reasoning_effort,
        routing_mode="per_task",
        session_name=session_name,
    )
    hooks.emit(
        "on_start",
        utc_now(),
        project,
        runner_id,
        int(state.get("iteration", 0)),
        {"model": model, "reasoning_effort": reasoning_effort, "routing_mode": "per_task"},
    )
    write_json(
        paths.active_lock,
        {
            "project": project,
            "runner_id": runner_id,
            "session_name": session_name,
            "pid": os.getpid(),
            "started_at": utc_now(),
        },
    )

    status = "running"
    session_id = state.get("session_id") if isinstance(state.get("session_id"), str) else None
    iteration = int(state.get("iteration", 0))
    return_code = 0
    runner_error: str | None = None

    try:
        with paths.runners_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{session_name},{int(time.time())},\n")

        while True:
            if paths.stop_lock.exists():
                status = "manual_stop"
                _append_ledger(paths, "runner.manual_stop", stop_lock=str(paths.stop_lock))
                _log_line(paths, f"Manual stop detected: {paths.stop_lock}")
                break

            if paths.done_lock.exists():
                _log_line(paths, "Done lock detected; validating gates + active ORX/Linear backlog")
                gates_ok, gates_output = _run_gates(paths.gates_file, project_root, runner_id)
                open_tasks, backlog_label = _extract_open_runner_backlog(project_root, project=project)
                tasks_ok = len(open_tasks) == 0
                _append_ledger(
                    paths,
                    "runner.done_candidate",
                    gates_ok=gates_ok,
                    tasks_ok=tasks_ok,
                    open_tasks_count=len(open_tasks),
                )
                if not gates_ok or not tasks_ok:
                    _log_line(paths, "Validation failed for done candidate; deleting done lock")
                    if gates_output:
                        _log_line(paths, gates_output)
                    if not tasks_ok:
                        _log_line(
                            paths,
                            f"Done candidate rejected: {backlog_label} has open/in_progress/blocked seams "
                            f"({len(open_tasks)} remaining)",
                        )
                    paths.done_lock.unlink(missing_ok=True)
                    reasons: list[str] = []
                    if not gates_ok:
                        reasons.append("gates_failed")
                    if not tasks_ok:
                        reasons.append("tasks_open")
                    _append_ledger(
                        paths,
                        "runner.done_rejected",
                        reason=",".join(reasons) or "unknown",
                        open_tasks=open_tasks[:8],
                    )
                    state = update_state(
                        paths.state_file,
                        state,
                        done_gate_status="failed",
                        done_candidate=False,
                    )
                    time.sleep(backoff_seconds)
                    continue
                state = update_state(paths.state_file, state, done_gate_status="passed")
                status = "done"
                _log_line(paths, "Done lock accepted; exiting loop")
                break

            iteration += 1
            state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
            state = update_state(
                paths.state_file,
                state,
                status="running",
                iteration=iteration,
                current_step=f"iteration_{iteration}",
                session_id=session_id,
                **detect_git_context(project_root),
                runtime_policy={
                    "runner_mode": "exec",
                    "session_strategy": "fresh_session",
                },
            )

            iteration_profile = resolve_active_seam_execution_profile(
                paths=paths,
                fallback_model=model,
                fallback_reasoning_effort=reasoning_effort,
            )
            iteration_model = str(iteration_profile.get("model") or model)
            iteration_reasoning_effort = str(iteration_profile.get("reasoning_effort") or reasoning_effort)
            iteration_model_profile = str(iteration_profile.get("model_profile") or "").strip() or "fallback"
            prompt = _build_prompt(project=project, runner_id=runner_id, paths=paths)
            _log_line(
                paths,
                (
                    f"Iteration {iteration} running {iteration_model} "
                    f"(profile={iteration_model_profile}, effort={iteration_reasoning_effort})"
                ),
            )
            _append_ledger(
                paths,
                "iteration.start",
                iteration=iteration,
                model=iteration_model,
                reasoning_effort=iteration_reasoning_effort,
                model_profile=iteration_profile.get("model_profile"),
                task_id=iteration_profile.get("seam_id") or iteration_profile.get("task_id"),
                profile_source=iteration_profile.get("source"),
            )
            hooks.emit(
                "on_step",
                utc_now(),
                project,
                runner_id,
                iteration,
                {
                    "model": iteration_model,
                    "reasoning_effort": iteration_reasoning_effort,
                    "model_profile": iteration_profile.get("model_profile"),
                    "task_id": iteration_profile.get("seam_id") or iteration_profile.get("task_id"),
                },
            )

            resume_session_id = None
            result = run_codex_iteration(
                cwd=project_root,
                model=iteration_model,
                prompt=prompt,
                session_id=resume_session_id,
                reasoning_effort=iteration_reasoning_effort,
                json_stream=False,
                logger=lambda line: _log_line(paths, line),
            )

            session_id = result.session_id or session_id
            state = update_state(paths.state_file, state, session_id=session_id)

            for event in result.events:
                if event.get("_hook") == "tool_call":
                    hooks.emit("on_tool_call", utc_now(), project, runner_id, iteration, {"event": event})

            _append_ledger(
                paths,
                "iteration.finish",
                iteration=iteration,
                model=iteration_model,
                reasoning_effort=iteration_reasoning_effort,
                model_profile=iteration_profile.get("model_profile"),
                exit_code=result.exit_code,
                final_message=result.final_message,
                session_id=session_id,
            )

            if result.exit_code != 0:
                hooks.emit(
                    "on_error",
                    utc_now(),
                    project,
                    runner_id,
                    iteration,
                    {
                        "exit_code": result.exit_code,
                        "final_message": result.final_message,
                    },
                )

            update_payload, finalize_mode, parse_error, finalize_probe_error = _resolve_iteration_update(
                paths=paths,
                project_root=project_root,
                model=iteration_model,
                reasoning_effort=iteration_reasoning_effort,
                state=state,
                result=result,
                iteration=iteration,
            )
            hooks.emit(
                "on_finish",
                utc_now(),
                project,
                runner_id,
                iteration,
                {
                    "final_message": str(update_payload.get("summary", result.final_message)),
                    "exit_code": result.exit_code,
            "runner_update": update_payload,
            "done_candidate": bool(update_payload.get("done_candidate", False)),
            "finalize_mode": finalize_mode,
            "parse_error": parse_error,
            "finalize_probe_error": finalize_probe_error,
            "remaining_gaps": list(update_payload.get("remaining_gaps", [])),
        },
    )

            state = _apply_iteration_update(
                paths=paths,
                state=state,
                update_payload=update_payload,
                session_id=session_id,
            )
            _append_ledger(
                paths,
                "iteration.update_applied",
                iteration=iteration,
                done_candidate=bool(update_payload.get("done_candidate", False)),
                finalize_mode=finalize_mode,
            )
            completion_action = "none"
            if bool(update_payload.get("done_candidate", False)):
                _log_line(paths, "Done candidate update detected; validating gates + active ORX/Linear backlog before lock creation")
                gates_ok, gates_output = _run_gates(paths.gates_file, project_root, runner_id)
                open_tasks, backlog_label = _extract_open_runner_backlog(project_root, project=project)
                tasks_ok = len(open_tasks) == 0
                _append_ledger(
                    paths,
                    "runner.done_candidate.update",
                    iteration=iteration,
                    gates_ok=gates_ok,
                    tasks_ok=tasks_ok,
                    open_tasks_count=len(open_tasks),
                )
                if gates_ok and tasks_ok:
                    state = update_state(paths.state_file, state, done_gate_status="passed", done_candidate=True)
                    paths.done_lock.write_text(
                        f"created_at={utc_now()}\nproject={project}\nrunner_id={runner_id}\niteration={iteration}\n"
                    )
                    _append_ledger(paths, "runner.done_lock_created", iteration=iteration)
                    _log_line(paths, f"Done lock created: {paths.done_lock}")
                    completion_action = "done_lock_created"
                else:
                    state = update_state(paths.state_file, state, done_gate_status="failed", done_candidate=False)
                    if gates_output:
                        _log_line(paths, gates_output)
                    if not tasks_ok:
                        _log_line(
                            paths,
                            f"Done candidate rejected: {backlog_label} has open/in_progress/blocked seams "
                            f"({len(open_tasks)} remaining)",
                        )
                    _log_line(paths, "Done candidate rejected because validation failed")
                    completion_action = "done_rejected_validation"
            else:
                state = update_state(paths.state_file, state, done_candidate=False, done_gate_status="pending")
                completion_action = "not_candidate"

            hooks.emit(
                "on_finalize",
                utc_now(),
                project,
                runner_id,
                iteration,
                {
                    "finalize_mode": finalize_mode,
                    "done_candidate": bool(update_payload.get("done_candidate", False)),
                    "completion_action": completion_action,
                    "parse_error": parse_error,
                    "finalize_probe_error": finalize_probe_error,
                    "runner_update": update_payload,
                    "remaining_gaps": list(update_payload.get("remaining_gaps", [])),
                },
            )

            time.sleep(backoff_seconds)
    except KeyboardInterrupt:
        status = "interrupted"
        return_code = 130
        runner_error = "KeyboardInterrupt"
        _append_ledger(paths, "runner.interrupted", iteration=iteration)
        _log_line(paths, "Runner interrupted by keyboard signal")
        hooks.emit(
            "on_error",
            utc_now(),
            project,
            runner_id,
            iteration,
            {
                "error": runner_error,
            },
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        status = "error"
        return_code = 1
        runner_error = f"{type(exc).__name__}: {exc}"
        _append_ledger(
            paths,
            "runner.exception",
            iteration=iteration,
            error=runner_error,
            traceback=traceback.format_exc(),
        )
        _log_line(paths, f"Runner exception: {runner_error}")
        hooks.emit(
            "on_error",
            utc_now(),
            project,
            runner_id,
            iteration,
            {
                "error": runner_error,
            },
        )
    finally:
        try:
            paths.stop_lock.unlink(missing_ok=True)
            _mark_runner_end_time(paths, session_name)

            state = update_state(paths.state_file, state, status=status, current_step="")
            payload: dict[str, object] = {"status": status, "iteration": iteration}
            if runner_error:
                payload["error"] = runner_error
            _append_ledger(paths, "runner.end", **payload)

            if runner_error:
                _log_line(paths, f"Runner finished status={status} iteration={iteration} error={runner_error}")
            else:
                _log_line(paths, f"Runner finished status={status} iteration={iteration}")
        finally:
            paths.active_lock.unlink(missing_ok=True)

    return return_code


def parse_loop_worker_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal loop worker")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--reasoning-effort",
        default="high",
        choices=("low", "medium", "high", "xhigh"),
    )
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--backoff-seconds", type=int, default=2)
    parser.add_argument("--dev", default=os.environ.get("DEV", "/Users/jian/Dev"))
    return parser.parse_args(argv)


def run_loop_worker(argv: list[str]) -> int:
    args = parse_loop_worker_args(argv)
    return run_loop_runner(
        dev=args.dev,
        project=args.project,
        runner_id=args.runner_id,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        session_name=args.session_name,
        backoff_seconds=args.backoff_seconds,
    )


def parse_runner_profile_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve per-task runner model profile")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--dev", default=os.environ.get("DEV", "/Users/jian/Dev"))
    parser.add_argument("--default-model", required=True)
    parser.add_argument("--default-reasoning-effort", required=True)
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    return parser.parse_args(argv)


def run_runner_profile(argv: list[str]) -> int:
    args = parse_runner_profile_args(argv)
    project_root = resolve_target_project_root(
        dev=args.dev,
        project=args.project,
        runner_id=args.runner_id,
    )
    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=args.dev,
        project=args.project,
        runner_id=args.runner_id,
    )
    profile = resolve_active_seam_execution_profile(
        paths=paths,
        fallback_model=args.default_model,
        fallback_reasoning_effort=args.default_reasoning_effort,
    )
    if args.format == "shell":
        print(render_runner_profile_shell_exports(profile))
    else:
        print(json.dumps(profile))
    return 0


def parse_runner_archive_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive runner-generated Codex threads for one cwd")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--keep", type=int, default=0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    return parser.parse_args(argv)


def run_runner_archive(argv: list[str]) -> int:
    args = parse_runner_archive_args(argv)
    summary = archive_runner_threads_for_cwd(
        cwd=Path(args.project_root).expanduser().resolve(),
        keep=max(0, args.keep),
    )
    if args.format == "json":
        print(json.dumps(summary))
    else:
        print(
            "runner_thread_archive "
            f"matched={summary['matched']} kept={summary['kept']} "
            f"archived={summary['archived']} failed={summary['failed']}"
        )
    return 0 if int(summary.get("failed") or 0) == 0 else 1


def parse_runner_controller_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal infinite runner controller")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--dev", default=os.environ.get("DEV", "/Users/jian/Dev"))
    parser.add_argument("--poll-seconds", type=float, default=0.75)
    return parser.parse_args(argv)


def run_interactive_runner_controller(argv: list[str]) -> int:
    args = parse_runner_controller_args(argv)
    dev = args.dev
    project = args.project
    runner_id = args.runner_id
    session_name = args.session_name
    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    paths = build_runner_paths(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )
    state_paths = paths.state
    tmux = TmuxClient()

    ensure_memory_dir(state_paths)
    state = read_json(state_paths.state_file) or {}
    execution_packet = _orx_execution_packet(project_root, project=project)
    orx_managed = _is_orx_execution_packet(execution_packet)
    pending_update_profile = _pending_update_profile_from_state(state)
    if orx_managed:
        pending_update_profile = None
    active_step: Literal["execute", "update"] = "update" if pending_update_profile else "execute"
    next_current_step = f"{RUNNER_UPDATE_PENDING_PREFIX}:{pending_update_profile}" if pending_update_profile else "interactive_runner"
    state = update_state(state_paths.state_file, state, status="running", current_step=next_current_step)
    _sync_runner_status_snapshot(state_paths, state)
    state_paths.active_lock.write_text(
        f"started_at={utc_now()}\nproject={project}\nrunner_id={runner_id}\nsession={session_name}\nmode=interactive\n"
    )
    _append_ledger(state_paths, "runner.start", mode="interactive", session_name=session_name)
    _log_line(state_paths, f"Infinite runner started for {session_name}")

    last_output = ""
    last_output_change_at = time.time()
    in_flight = False
    injected_phase = "discover"
    dispatch_output_snapshot = ""
    dispatched_command = ""
    dispatched_at = 0.0
    saw_busy = False
    idle_since: float | None = None
    session_model_profile: str | None = pending_update_profile or None
    try:
        while True:
            if not tmux.has_session(session_name):
                _append_ledger(state_paths, "runner.session_missing", session_name=session_name)
                _log_line(state_paths, f"Runner session disappeared: {session_name}")
                state = update_state(state_paths.state_file, state, status="stopped", current_step="")
                _sync_runner_status_snapshot(state_paths, state)
                return 0

            if state_paths.stop_lock.exists():
                _append_ledger(state_paths, "runner.manual_stop", stop_lock=str(state_paths.stop_lock))
                _log_line(state_paths, "Infinite runner stop requested")
                state = update_state(state_paths.state_file, state, status="stopped", current_step="")
                _sync_runner_status_snapshot(state_paths, state)
                return 0

            if state_paths.done_lock.exists():
                _append_ledger(state_paths, "runner.done_lock_observed", done_lock=str(state_paths.done_lock))
                _log_line(state_paths, "Done lock detected; infinite runner exiting")
                state = update_state(state_paths.state_file, state, status="done", current_step="")
                _sync_runner_status_snapshot(state_paths, state)
                return 0

            output = tmux.capture_pane(session_name, lines=LINES_FULL) or ""
            if output != last_output:
                last_output = output
                last_output_change_at = time.time()
            execution_packet = _orx_execution_packet(project_root, project=project)
            orx_managed = _is_orx_execution_packet(execution_packet)
            if orx_managed and active_step != "execute":
                pending_update_profile = None
                active_step = "execute"
                state = update_state(
                    state_paths.state_file,
                    state,
                    status="running",
                    current_step="interactive_runner",
                )
                _sync_runner_status_snapshot(state_paths, state)

            process_name = tmux.get_pane_process(session_name)
            pane_state = detect_runner_state(output, process_name, last_output_change_at)
            state = read_json(state_paths.state_file) or state
            _sync_runner_status_snapshot(state_paths, state)
            exec_context = read_json(state_paths.exec_context_json) or {}
            phase = coerce_runner_phase(
                exec_context.get("phase") or state.get("current_phase"),
                default="discover",
            )
            now = time.time()
            if pane_state == "idle":
                idle_since = idle_since or now
            else:
                idle_since = None

            dispatch_idle_grace_seconds = _runner_idle_grace_seconds(
                RUNNER_DISPATCH_IDLE_GRACE_SECONDS,
                args.poll_seconds,
            )
            completion_idle_grace_seconds = _runner_idle_grace_seconds(
                RUNNER_COMPLETION_IDLE_GRACE_SECONDS,
                args.poll_seconds,
            )
            dispatch_idle_grace_satisfied = idle_since is not None and (
                dispatch_idle_grace_seconds <= 0
                or (now - idle_since) >= dispatch_idle_grace_seconds
            )
            completion_idle_grace_satisfied = idle_since is not None and (
                completion_idle_grace_seconds <= 0
                or (now - idle_since) >= completion_idle_grace_seconds
            )
            if in_flight:
                if pane_state != "idle":
                    saw_busy = True
                prompt_completed = False
                if pane_state == "idle" and completion_idle_grace_satisfied:
                    explicit_completion = _has_completion_directive_for_step(output, active_step)
                    lower_output = output.lower()
                    execute_prompt_cleared = (
                        active_step == "execute"
                        and bool(dispatched_command)
                        and not _pane_contains_exact_prompt(output, dispatched_command)
                        and "send saved prompt" not in lower_output
                    )
                    fallback_completion_stable = (now - last_output_change_at) >= max(
                        RUNNER_FALLBACK_COMPLETION_STABLE_SECONDS,
                        args.poll_seconds * 6,
                    )
                    if explicit_completion:
                        prompt_completed = True
                    elif fallback_completion_stable and saw_busy:
                        prompt_completed = True
                    elif fallback_completion_stable and execute_prompt_cleared:
                        prompt_completed = True
                    elif fallback_completion_stable and output != dispatch_output_snapshot and (
                        now - dispatched_at
                    ) >= max(2.0, args.poll_seconds * 3):
                        prompt_completed = True

                if prompt_completed and active_step == "execute":
                    if orx_managed and isinstance(execution_packet, dict):
                        result_payload, parse_error = _parse_orx_runner_result(output)
                        if parse_error:
                            _append_ledger(
                                state_paths,
                                "runner.orx_result_parse_failed",
                                phase=injected_phase,
                                error=parse_error,
                            )
                            _log_line(
                                state_paths,
                                f"ORX execute slice did not return a valid result block for phase={injected_phase}: {parse_error}",
                            )
                            state = update_state(state_paths.state_file, state, status="error", current_step="")
                            _sync_runner_status_snapshot(state_paths, state)
                            return 1
                        try:
                            result_payload["issue_key"] = str(
                                execution_packet.get("issue_key") or ""
                            ).strip()
                            result_payload["packet_key"] = str(
                                execution_packet.get("packet_key") or ""
                            ).strip()
                            result_payload["packet_revision"] = str(
                                execution_packet.get("packet_revision") or ""
                            ).strip()
                            result_payload["latest_handoff_revision"] = str(
                                execution_packet.get("latest_handoff_revision") or ""
                            ).strip()
                            result_payload["continuity_revision"] = str(
                                execution_packet.get("continuity_revision") or ""
                            ).strip()
                            result_payload["decision_epoch"] = str(
                                execution_packet.get("decision_epoch") or ""
                            ).strip()
                            submit_slice_result(
                                project_key=project,
                                slice_id=str(execution_packet.get("active_slice_id")),
                                payload=result_payload,
                            )
                        except OrxControlError as exc:
                            _append_ledger(
                                state_paths,
                                "runner.orx_result_submit_failed",
                                phase=injected_phase,
                                error=str(exc),
                            )
                            _log_line(
                                state_paths,
                                f"Failed to submit ORX slice result for phase={injected_phase}: {exc}",
                            )
                            state = update_state(state_paths.state_file, state, status="error", current_step="")
                            _sync_runner_status_snapshot(state_paths, state)
                            return 1
                        _append_ledger(
                            state_paths,
                            "runner.orx_result_submitted",
                            phase=injected_phase,
                            issue_key=execution_packet.get("issue_key"),
                            slice_id=execution_packet.get("active_slice_id"),
                            status=result_payload.get("status"),
                            verified=result_payload.get("verified"),
                        )
                        _log_line(
                            state_paths,
                            (
                                f"Submitted ORX slice result for {execution_packet.get('issue_key')} "
                                f"(status={result_payload.get('status')}, verified={result_payload.get('verified')})"
                            ),
                        )
                        eof_sent = tmux.send_eof(session_name)
                        if not eof_sent:
                            _append_ledger(
                                state_paths,
                                "runner.chat_exit_failed",
                                phase=injected_phase,
                                session_name=session_name,
                            )
                            _log_line(
                                state_paths,
                                f"ORX slice result submitted for phase={injected_phase}, but failed to exit Codex chat cleanly",
                            )
                            state = update_state(state_paths.state_file, state, status="error", current_step="")
                            _sync_runner_status_snapshot(state_paths, state)
                            return 1
                        _append_ledger(
                            state_paths,
                            "runner.chat_exit_requested",
                            phase=injected_phase,
                            session_name=session_name,
                        )
                        _log_line(
                            state_paths,
                            f"Requested Codex chat exit after ORX execute slice for phase={injected_phase}; next loop will relaunch fresh",
                        )
                        return 0
                    _append_ledger(
                        state_paths,
                        "runner.execute_complete",
                        phase=injected_phase,
                        completed_at=utc_now(),
                    )
                    _log_line(state_paths, f"Execute completed for phase={injected_phase}")
                    update_request = _parse_execute_update_request(output)
                    closeout_incomplete = injected_phase == "closeout" and not bool(update_request.get("phase_done"))
                    if closeout_incomplete and not bool(update_request.get("needs_update")):
                        update_request["needs_update"] = True
                        update_request["update_profile"] = "mini"
                        update_request["scope_status"] = "narrow"
                        update_request["update_reason"] = "closeout_phase_incomplete"
                    if bool(update_request.get("needs_update")):
                        update_profile = str(update_request.get("update_profile") or "mini")
                        update_reason = str(update_request.get("update_reason") or "").strip()
                        scope_status = str(update_request.get("scope_status") or "ok")
                        _append_ledger(
                            state_paths,
                            "runner.update_requested",
                            phase=injected_phase,
                            update_profile=update_profile,
                            update_reason=update_reason or None,
                            scope_status=scope_status,
                        )
                        _log_line(
                            state_paths,
                            (
                                f"Execute requested semantic run_govern for phase={injected_phase}; "
                                f"update_profile={update_profile}"
                            ),
                        )
                        if _can_continue_update_in_same_session(
                            requested_profile=update_profile,
                            session_profile=session_model_profile,
                        ):
                            state = _prepare_same_session_update_handoff(
                                state_paths=state_paths,
                                state=state,
                                phase=injected_phase,
                                update_profile=update_profile,
                                scope_status=scope_status,
                                reason=update_reason or None,
                                active_step="execute",
                            )
                            pending_update_profile = update_profile
                            active_step = "update"
                            in_flight = False
                            saw_busy = False
                            dispatch_output_snapshot = ""
                            dispatched_command = ""
                            dispatched_at = 0.0
                            continue
                        state = update_state(
                            state_paths.state_file,
                            state,
                            status="running",
                            current_step=f"{RUNNER_UPDATE_PENDING_PREFIX}:{update_profile}",
                        )
                    else:
                        refresh_ok, refresh_error = _run_scripted_cycle_refresh(
                            dev=dev,
                            project=project,
                            runner_id=runner_id,
                            project_root=project_root,
                        )
                        if refresh_ok:
                            state = _reload_refreshed_runner_state(state_paths.state_file, state)
                            _append_ledger(
                                state_paths,
                                "runner.scripted_refresh_complete",
                                phase=injected_phase,
                            )
                            _log_line(
                                state_paths,
                                (
                                    f"Scripted refresh completed for phase={injected_phase}; "
                                    "prepared marker written and next loop will restart fresh"
                                ),
                            )
                        else:
                            state = update_state(
                                state_paths.state_file,
                                state,
                                status="running",
                                current_step=f"{RUNNER_UPDATE_PENDING_PREFIX}:mini",
                            )
                            _append_ledger(
                                state_paths,
                                "runner.scripted_refresh_failed",
                                phase=injected_phase,
                                error=refresh_error or "unknown",
                            )
                            _log_line(
                                state_paths,
                                (
                                    f"Scripted refresh failed after execute for phase={injected_phase}; "
                                    f"falling back to semantic run_govern reason={refresh_error or 'unknown'}"
                                ),
                            )
                            if _can_continue_update_in_same_session(
                                requested_profile="mini",
                                session_profile=session_model_profile,
                            ):
                                state = _prepare_same_session_update_handoff(
                                    state_paths=state_paths,
                                    state=state,
                                    phase=injected_phase,
                                    update_profile="mini",
                                    scope_status="narrow",
                                    reason=refresh_error or None,
                                    active_step="execute",
                                )
                                pending_update_profile = "mini"
                                active_step = "update"
                                in_flight = False
                                saw_busy = False
                                dispatch_output_snapshot = ""
                                dispatched_command = ""
                                dispatched_at = 0.0
                                continue
                    eof_sent = tmux.send_eof(session_name)
                    if not eof_sent:
                        _append_ledger(
                            state_paths,
                            "runner.chat_exit_failed",
                            phase=injected_phase,
                            session_name=session_name,
                        )
                        _log_line(
                            state_paths,
                            f"Execute completed for phase={injected_phase}, but failed to exit Codex chat cleanly",
                        )
                        state = update_state(state_paths.state_file, state, status="error", current_step="")
                        _sync_runner_status_snapshot(state_paths, state)
                        return 1
                    _append_ledger(
                        state_paths,
                        "runner.chat_exit_requested",
                        phase=injected_phase,
                        session_name=session_name,
                    )
                    _log_line(
                        state_paths,
                        f"Requested Codex chat exit after execute for phase={injected_phase}; next loop will relaunch fresh",
                    )
                    return 0

                if prompt_completed and active_step == "update":
                    scope_status = _normalize_scope_status(_parse_status_directive(output, "scope_status"))
                    active_update_profile = pending_update_profile or "mini"
                    recovered = False
                    refresh_ok, refresh_error = _run_scripted_cycle_refresh(
                        dev=dev,
                        project=project,
                        runner_id=runner_id,
                        project_root=project_root,
                    )
                    if refresh_ok:
                        _append_ledger(
                            state_paths,
                            "runner.update_complete",
                            phase=injected_phase,
                            scope_status=scope_status,
                        )
                        _log_line(
                            state_paths,
                            (
                                f"Update completed for phase={injected_phase}; controller refresh succeeded "
                                "and the next loop will restart fresh"
                            ),
                        )
                        eof_sent = tmux.send_eof(session_name)
                        if not eof_sent:
                            _append_ledger(
                                state_paths,
                                "runner.chat_exit_failed",
                                phase=injected_phase,
                                session_name=session_name,
                            )
                            _log_line(
                                state_paths,
                                f"Prepared marker observed for phase={injected_phase}, but failed to exit Codex chat cleanly",
                            )
                            state = update_state(state_paths.state_file, state, status="error", current_step="")
                            _sync_runner_status_snapshot(state_paths, state)
                            return 1

                        _append_ledger(
                            state_paths,
                            "runner.chat_exit_requested",
                            phase=injected_phase,
                            session_name=session_name,
                        )
                        _log_line(
                            state_paths,
                            f"Requested Codex chat exit after update for phase={injected_phase}; next loop will relaunch fresh",
                        )
                        state = _reload_refreshed_runner_state(state_paths.state_file, state)
                        return 0
                    failure_reason = refresh_error or "scripted_refresh_failed_after_update"
                    _append_ledger(
                        state_paths,
                        "runner.update_prepare_failed",
                        phase=injected_phase,
                        session_name=session_name,
                        reason=failure_reason,
                        scope_status=scope_status,
                        update_profile=active_update_profile,
                    )
                    if active_update_profile != "high":
                        state = update_state(
                            state_paths.state_file,
                            state,
                            status="running",
                            current_step=f"{RUNNER_UPDATE_PENDING_PREFIX}:high",
                        )
                        _append_ledger(
                            state_paths,
                            "runner.update_retry_requested",
                            phase=injected_phase,
                            session_name=session_name,
                            from_profile=active_update_profile,
                            to_profile="high",
                            reason=failure_reason,
                            scope_status=scope_status,
                        )
                        _log_line(
                            state_paths,
                            (
                                f"Update completed for phase={injected_phase} but scripted refresh still failed; "
                                "scheduling a fresh high run_govern recovery cycle"
                            ),
                        )
                    else:
                        recovered = False
                        recovery_task_id = ""
                        if _is_no_durable_progress_refresh_failure(failure_reason):
                            recovered, recovery_task_id = _queue_no_progress_recovery_task(
                                state_paths=state_paths,
                                state=state,
                                project_root=project_root,
                                failure_reason=failure_reason,
                                stalled_task_id=str(state.get("next_task_id", "")).strip() or None,
                                stalled_task_title=str(state.get("next_task", "")).strip() or None,
                            )
                            if recovered:
                                _append_ledger(
                                    state_paths,
                                    "runner.no_progress_recovery_queued",
                                    phase=injected_phase,
                                    session_name=session_name,
                                    reason=failure_reason,
                                    recovery_task_id=recovery_task_id or None,
                                    stalled_task_id=str(state.get("next_task_id", "")).strip() or None,
                                )
                                _log_line(
                                    state_paths,
                                    (
                                        f"Queued recovery task {recovery_task_id or '(pending)'} after repeated no-progress "
                                        "run_govern failure; attempting deterministic refresh"
                                    ),
                                )
                                refresh_ok, refresh_error = _run_scripted_cycle_refresh(
                                    dev=dev,
                                    project=project,
                                    runner_id=runner_id,
                                    project_root=project_root,
                                )
                                if refresh_ok:
                                    state = _reload_refreshed_runner_state(state_paths.state_file, state)
                                    _append_ledger(
                                        state_paths,
                                        "runner.no_progress_recovery_ready",
                                        phase=injected_phase,
                                        session_name=session_name,
                                        recovery_task_id=recovery_task_id or None,
                                    )
                                    _log_line(
                                        state_paths,
                                        (
                                            f"Recovery task {recovery_task_id or '(pending)'} prepared successfully after "
                                            f"no-progress update failure; next loop will relaunch fresh"
                                        ),
                                    )
                                else:
                                    recovered = False
                                    _append_ledger(
                                        state_paths,
                                        "runner.no_progress_recovery_refresh_failed",
                                        phase=injected_phase,
                                        session_name=session_name,
                                        recovery_task_id=recovery_task_id or None,
                                        error=refresh_error or "unknown",
                                    )
                                    _log_line(
                                        state_paths,
                                        (
                                            f"Recovery task {recovery_task_id or '(pending)'} was queued after no-progress "
                                            f"update failure, but deterministic refresh still failed reason={refresh_error or 'unknown'}"
                                        ),
                                    )
                        if not recovered:
                            _write_runner_stop_lock(
                                state_paths=state_paths,
                                source="runner_update_failure",
                                reason=failure_reason,
                                phase=injected_phase,
                                session_name=session_name,
                            )
                            state = update_state(
                                state_paths.state_file,
                                state,
                                status="error",
                                current_step="",
                            )
                            _append_ledger(
                                state_paths,
                                "runner.update_retry_exhausted",
                                phase=injected_phase,
                                session_name=session_name,
                                reason=failure_reason,
                                scope_status=scope_status,
                            )
                            _log_line(
                                state_paths,
                                (
                                    f"High run_govern finished for phase={injected_phase} but scripted refresh still failed; "
                                    "stopping the infinite runner with an explicit recovery error"
                                ),
                            )
                    eof_sent = tmux.send_eof(session_name)
                    if not eof_sent:
                        _append_ledger(
                            state_paths,
                            "runner.chat_exit_failed",
                            phase=injected_phase,
                            session_name=session_name,
                        )
                        _log_line(
                            state_paths,
                            f"Update refresh failed for phase={injected_phase}, and Codex chat exit also failed",
                        )
                        state = update_state(state_paths.state_file, state, status="error", current_step="")
                        _sync_runner_status_snapshot(state_paths, state)
                        return 1
                    _append_ledger(
                        state_paths,
                        "runner.chat_exit_requested",
                        phase=injected_phase,
                        session_name=session_name,
                    )
                    _log_line(
                        state_paths,
                        (
                            f"Requested Codex chat exit after update refresh failure for phase={injected_phase}; "
                            f"next recovery step is {'fresh high run_govern' if active_update_profile != 'high' else ('fresh recovery triage' if recovered else 'runner error stop')}"
                        ),
                    )
                    return 0 if active_update_profile != "high" or recovered else 1

            if not in_flight and pane_state == "idle" and dispatch_idle_grace_satisfied:
                active_profile = resolve_active_seam_execution_profile(
                    paths=state_paths,
                    fallback_model="gpt-5.4",
                    fallback_reasoning_effort="high",
                )
                session_model_profile = _session_profile_for_dispatch(
                    model_profile=str(active_profile.get("model_profile") or "").strip() or None,
                    model=str(active_profile.get("model") or "").strip(),
                    reasoning_effort=str(active_profile.get("reasoning_effort") or "").strip(),
                )
                if active_step == "execute":
                    command = _build_execute_only_command(
                        dev=dev,
                        project=project,
                        runner_id=runner_id,
                        project_root=project_root,
                        phase=phase,
                    )
                else:
                    command = _build_update_command(
                        dev=dev,
                        project=project,
                        runner_id=runner_id,
                        project_root=project_root,
                    )
                injected_at = utc_now()
                if active_step == "update":
                    _append_ledger(
                        state_paths,
                        "runner.update_dispatch_start",
                        phase=phase,
                        session_name=session_name,
                    )
                    _log_line(
                        state_paths,
                        f"Dispatching govern prompt for phase={phase} in {session_name}",
                    )
                sent, dispatch_failure_reason = _submit_runner_prompt(
                    tmux=tmux,
                    session_name=session_name,
                    command=command,
                    visible_hold_seconds=RUNNER_UPDATE_VISIBLE_HOLD_SECONDS if active_step == "update" else 0.0,
                )

                if sent:
                    injected_phase = phase
                    in_flight = True
                    dispatch_output_snapshot = output
                    dispatched_command = command
                    dispatched_at = time.time()
                    saw_busy = False
                    if active_step == "update":
                        update_marker_observed = False
                    _append_ledger(
                        state_paths,
                        "runner.iteration_dispatch",
                        phase=phase,
                        step=active_step,
                        command=command,
                        injected_at=injected_at,
                        model=active_profile.get("model"),
                        reasoning_effort=active_profile.get("reasoning_effort"),
                        model_profile=active_profile.get("model_profile"),
                        task_id=active_profile.get("seam_id") or active_profile.get("task_id"),
                    )
                    if active_step == "update":
                        _append_ledger(
                            state_paths,
                            "runner.update_dispatch_sent",
                            phase=phase,
                            session_name=session_name,
                            injected_at=injected_at,
                        )
                        _log_line(
                            state_paths,
                            f"Dispatched govern prompt for phase={phase}; waiting for prepared marker before fresh restart",
                        )
                    else:
                        _log_line(
                            state_paths,
                            (
                                f"Dispatched {active_step} for phase={phase} "
                                f"(profile={active_profile.get('model_profile') or 'fallback'}, "
                                f"model={active_profile.get('model')}, "
                                f"effort={active_profile.get('reasoning_effort')})"
                            ),
                        )
                else:
                    _append_ledger(
                        state_paths,
                        "runner.dispatch_failed",
                        phase=phase,
                        step=active_step,
                        session_name=session_name,
                        reason=dispatch_failure_reason or "unknown",
                    )
                    if active_step == "update":
                        _append_ledger(
                            state_paths,
                            "runner.update_dispatch_failed",
                            phase=phase,
                            session_name=session_name,
                            reason=dispatch_failure_reason or "unknown",
                        )
                        _log_line(
                            state_paths,
                            f"Failed to dispatch govern prompt for phase={phase} in {session_name} reason={dispatch_failure_reason or 'unknown'}",
                        )
                    else:
                        _log_line(
                            state_paths,
                            f"Failed to dispatch {active_step} for phase={phase} in {session_name} reason={dispatch_failure_reason or 'unknown'}",
                        )

            time.sleep(args.poll_seconds)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _append_ledger(
            state_paths,
            "runner.controller_exception",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )
        _log_line(state_paths, f"Infinite runner exception: {type(exc).__name__}: {exc}")
        state = update_state(state_paths.state_file, state, status="error", current_step="")
        _sync_runner_status_snapshot(state_paths, state)
        return 1
    finally:
        state_paths.active_lock.unlink(missing_ok=True)
