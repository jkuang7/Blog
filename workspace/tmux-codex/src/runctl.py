"""Deterministic file-backed control for Codex infinite runner state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner_state import (
    DEFAULT_PHASE_BUDGET_MINUTES,
    append_ndjson,
    build_runner_state_paths_for_root,
    coerce_runner_phase,
    compute_worktree_fingerprint,
    count_open_tasks,
    detect_git_context,
    ensure_memory_dir,
    load_or_init_state,
    managed_runner_files,
    read_json,
    worktrees_home,
    update_state,
    utc_now,
    write_json,
)

TASK_STATUS_VALUES = {"open", "in_progress", "blocked", "done"}
TASK_PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
SETUP_EVENT_COALESCE_SECONDS = 90


def _default_dev() -> str:
    return str(Path.home() / "Dev")


def _resolve_project_context(
    dev: str,
    explicit: str | None,
    project_root_override: str | None = None,
) -> tuple[str, Path]:
    if project_root_override:
        project_root = Path(project_root_override).expanduser().resolve()
        return project_root.name, project_root

    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_absolute() or explicit.startswith(".") or "/" in explicit:
            project_root = explicit_path.resolve()
            return project_root.name, project_root
        project = explicit
        return project, (Path(dev) / "Repos" / project).resolve()

    cwd = Path.cwd().resolve()
    repos = (Path(dev) / "Repos").resolve()
    try:
        rel = cwd.relative_to(repos)
        project = rel.parts[0] if rel.parts else cwd.name
        return project, (repos / project).resolve()
    except ValueError:
        return cwd.name, cwd


def _is_path_like_explicit(explicit: str | None) -> bool:
    if not explicit:
        return False
    explicit_path = Path(explicit).expanduser()
    return explicit_path.is_absolute() or explicit.startswith(".") or "/" in explicit


def _parse_updated_at(raw: Any) -> float:
    if not isinstance(raw, str) or not raw.strip():
        return 0.0
    try:
        parsed = datetime.strptime(raw.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return parsed.timestamp()


def _discover_saved_runner_roots(dev: str, project: str, runner_id: str) -> list[tuple[str, Path, float]]:
    def _normalize_runner_id(value: Any) -> str:
        normalized = str(value or "main").strip()
        if normalized == "default":
            return "main"
        return normalized or "main"

    def _collect_candidate(source: str, state_root: Path, sink: list[tuple[str, Path, float]]) -> None:
        memory_dir = state_root / ".memory"
        state = read_json(memory_dir / "runner" / "runtime" / "RUNNER_STATE.json")
        if not state:
            state = read_json(memory_dir / "runner" / "RUNNER_STATE.json")
        if not state:
            return
        if _normalize_runner_id(state.get("runner_id")) != runner_id:
            return

        resolved_root = state_root.resolve()
        git_worktree_raw = str(state.get("git_worktree", "")).strip()
        if git_worktree_raw:
            try:
                resolved_root = Path(git_worktree_raw).expanduser().resolve()
            except OSError:
                resolved_root = state_root.resolve()

        if not resolved_root.exists() or not resolved_root.is_dir():
            return

        updated_at = _parse_updated_at(state.get("updated_at"))
        sink.append((source, resolved_root, updated_at))

    candidates: list[tuple[str, Path, float]] = []
    canonical_root = (Path(dev) / "Repos" / project).resolve()
    _collect_candidate("canonical", canonical_root, candidates)

    worktree_roots = [worktrees_home(dev)]
    seen_worktree_roots: set[Path] = set()
    for worktrees_root in worktree_roots:
        if worktrees_root in seen_worktree_roots:
            continue
        seen_worktree_roots.add(worktrees_root)
        if not worktrees_root.exists():
            continue
        state_globs = (
            f"*/{project}/.memory/runner/runtime/RUNNER_STATE.json",
            f"*/{project}/.memory/runner/RUNNER_STATE.json",
        )
        for pattern in state_globs:
            for state_file in worktrees_root.glob(pattern):
                try:
                    state_root = state_file.parents[3] if "runtime" in state_file.parts else state_file.parents[2]
                except IndexError:
                    continue
                _collect_candidate("worktree", state_root, candidates)

    # Keep newest candidate per resolved root.
    deduped: dict[Path, tuple[str, Path, float]] = {}
    for source, root, updated_at in candidates:
        current = deduped.get(root)
        if current is None or updated_at >= current[2]:
            deduped[root] = (source, root, updated_at)
    return list(deduped.values())


def _resolve_project_root_from_saved_state(dev: str, project: str, runner_id: str) -> Path | None:
    candidates = _discover_saved_runner_roots(dev=dev, project=project, runner_id=runner_id)
    if not candidates:
        return None

    worktree_candidates = [item for item in candidates if item[0] == "worktree"]
    prioritized = worktree_candidates if worktree_candidates else candidates
    prioritized.sort(key=lambda item: item[2], reverse=True)
    return prioritized[0][1]


def _runner_context_file(dev: str, project: str) -> Path:
    return (Path(dev) / "Repos" / project / ".memory" / "RUNNER_CONTEXT.json").resolve()


def _legacy_runner_context_file(dev: str, project: str) -> Path:
    return (Path(dev) / "Repos" / project / ".memory" / "runner" / "RUNNER_CONTEXT.json").resolve()


def _resolve_project_root_from_runner_context(dev: str, project: str, runner_id: str) -> Path | None:
    context = read_json(_runner_context_file(dev=dev, project=project))
    if not isinstance(context, dict):
        context = read_json(_legacy_runner_context_file(dev=dev, project=project))
    if not isinstance(context, dict):
        return None

    runners = context.get("runners")
    if not isinstance(runners, dict):
        return None

    raw_runner = runners.get(runner_id)
    if not isinstance(raw_runner, dict):
        return None

    raw_root = str(raw_runner.get("project_root", "")).strip()
    if not raw_root:
        return None

    try:
        resolved_root = Path(raw_root).expanduser().resolve()
    except OSError:
        return None

    if not resolved_root.exists() or not resolved_root.is_dir():
        return None
    return resolved_root


def _write_runner_context(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> None:
    context_file = _runner_context_file(dev=dev, project=project)
    context = read_json(context_file)
    if not isinstance(context, dict):
        context = {}

    runners = context.get("runners")
    if not isinstance(runners, dict):
        runners = {}

    runners[runner_id] = {
        "project_root": str(project_root.resolve()),
        "updated_at": utc_now(),
    }

    context["project"] = project
    context["updated_at"] = utc_now()
    context["runners"] = runners
    write_json(context_file, context)
    _legacy_runner_context_file(dev=dev, project=project).unlink(missing_ok=True)


def _clear_runner_context_if_matches(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> None:
    context_file = _runner_context_file(dev=dev, project=project)
    context = read_json(context_file)
    if not isinstance(context, dict):
        return

    runners = context.get("runners")
    if not isinstance(runners, dict):
        return

    runner_entry = runners.get(runner_id)
    if not isinstance(runner_entry, dict):
        return

    raw_root = str(runner_entry.get("project_root", "")).strip()
    if not raw_root:
        return

    try:
        saved_root = Path(raw_root).expanduser().resolve()
    except OSError:
        saved_root = None

    if saved_root is not None and saved_root != project_root.resolve():
        return

    runners.pop(runner_id, None)
    if not runners:
        context_file.unlink(missing_ok=True)
        return

    context["runners"] = runners
    context["updated_at"] = utc_now()
    write_json(context_file, context)


def resolve_target_project_root(
    *,
    dev: str,
    project: str,
    runner_id: str = "main",
    project_root_override: str | Path | None = None,
) -> Path:
    """Resolve the effective project root for runner operations.

    Resolution order:
    1) explicit override
    2) canonical runner context pointer (RUNNER_CONTEXT.json)
    3) saved runner state git_worktree (worktree preferred over canonical)
    4) canonical $DEV/Repos/<project>
    """
    if project_root_override is not None:
        return Path(project_root_override).expanduser().resolve()
    context_root = _resolve_project_root_from_runner_context(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    if context_root is not None:
        return context_root
    saved_root = _resolve_project_root_from_saved_state(dev=dev, project=project, runner_id=runner_id)
    if saved_root is not None:
        return saved_root
    return (Path(dev) / "Repos" / project).resolve()


def _default_runner_id(explicit: str | None) -> str:
    if explicit and explicit not in {"main", "default"}:
        return explicit
    return "main"


def _new_token() -> str:
    return secrets.token_hex(8)


def _new_objective_id() -> str:
    return f"OBJ-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _parse_iso_ts(raw: Any) -> float:
    if not isinstance(raw, str) or not raw.strip():
        return 0.0
    try:
        value = datetime.strptime(raw.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return value.timestamp()


def _read_last_ndjson_event(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            if end <= 0:
                return None
            chunk_size = 4096
            data = b""
            pos = end
            while pos > 0:
                read_size = min(chunk_size, pos)
                pos -= read_size
                handle.seek(pos)
                data = handle.read(read_size) + data
                lines = data.splitlines()
                if len(lines) > 1:
                    for raw in reversed(lines):
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            return None
                        return payload if isinstance(payload, dict) else None
            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                return None
            payload = json.loads(line)
            return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _as_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _digest_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _compact_lines(text: str, *, limit: int = 8, line_chars: int = 180) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", raw_line).strip()
        if not cleaned:
            continue
        lines.append(cleaned[:line_chars])
        if len(lines) >= limit:
            break
    return lines


def _extract_required_context_paths(agents_text: str) -> list[str]:
    paths: list[str] = []
    in_required_context = False
    for raw_line in agents_text.splitlines():
        stripped = raw_line.strip()
        if stripped.lower() == "## required context load":
            in_required_context = True
            continue
        if in_required_context and stripped.startswith("## "):
            break
        if not in_required_context:
            continue
        match = re.match(r"^\d+\.\s+`?([^`]+?)`?$", stripped)
        if match:
            paths.append(match.group(1).strip())
    return paths


def _summarize_context_source(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []

    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _compact_lines(raw)
        if path.name == "context-pack.json" and isinstance(payload, dict):
            summary: list[str] = []
            repo_id = _as_text(payload.get("repoId"))
            if repo_id:
                summary.append(f"repoId={repo_id}")
            architecture_rules = payload.get("architectureRules")
            if isinstance(architecture_rules, list) and architecture_rules:
                summary.append(
                    "architectureRules=" + ", ".join(str(item).strip() for item in architecture_rules[:5] if str(item).strip())
                )
            done_criteria = payload.get("doneCriteria")
            if isinstance(done_criteria, list) and done_criteria:
                summary.append(
                    "doneCriteria=" + ", ".join(str(item).strip() for item in done_criteria[:3] if str(item).strip())
                )
            packages = payload.get("packages")
            if isinstance(packages, list) and packages:
                package_names = [str(item.get("name", "")).strip() for item in packages if isinstance(item, dict)]
                summary.append("packages=" + ", ".join(name for name in package_names[:4] if name))
            return summary[:6]
        if isinstance(payload, dict):
            summary = [f"{key}={type(value).__name__}" for key, value in list(payload.items())[:6]]
            return summary
        return [_as_text(payload)[:180]] if _as_text(payload) else []

    return _compact_lines(raw)


def _build_context_source(path: Path, *, kind: str) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    summary = _summarize_context_source(path)
    return {
        "path": str(path),
        "kind": kind,
        "digest": _digest_text(raw),
        "summary": summary,
    }


def _collect_context_sources(project_root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[Path] = set()

    def _add(path: Path, *, kind: str) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        source = _build_context_source(resolved, kind=kind)
        if source is None:
            return
        sources.append(source)
        seen.add(resolved)

    agents_path = project_root / "AGENTS.md"
    required_paths: list[str] = []
    if agents_path.exists():
        _add(agents_path, kind="repo_agents")
        try:
            required_paths = _extract_required_context_paths(agents_path.read_text(encoding="utf-8"))
        except OSError:
            required_paths = []

    declared_context_pack = any(Path(relative_path).name in {"context-pack.md", "context-pack.json"} for relative_path in required_paths)
    if required_paths and not declared_context_pack:
        _add(project_root / ".codex" / "context-pack.md", kind="context_pack")
        _add(project_root / ".codex" / "context-pack.json", kind="context_pack_json")

    for relative_path in required_paths:
        candidate = (project_root / relative_path).resolve()
        _add(candidate, kind="required_context")
        if candidate.name == "context-pack.md":
            _add(candidate.with_suffix(".json"), kind="context_pack_json")

    if not required_paths:
        _add(project_root / ".codex" / "context-pack.md", kind="context_pack")
        _add(project_root / ".codex" / "context-pack.json", kind="context_pack_json")

    memory_dir = project_root / ".memory"
    _add(memory_dir / "PRD.md", kind="repo_prd")
    _add(memory_dir / "lessons.md", kind="repo_lessons")
    _add(memory_dir / "runner" / "RUNNER_HANDOFF.md", kind="runner_handoff")
    if not (memory_dir / "PRD.md").exists():
        _add(memory_dir / "REFRACTOR_STATUS.md", kind="legacy_repo_prd")

    return sources


def _extract_open_tasks_from_payload(tasks_payload: dict[str, Any], max_items: int = 40) -> list[str]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return []

    items: list[str] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status not in {"open", "in_progress", "blocked"}:
            continue
        task_id = str(raw.get("task_id", "")).strip() or "(id)"
        title = str(raw.get("title", "")).strip() or "(untitled)"
        items.append(f"{task_id}: {title}"[:220])
        if len(items) >= max_items:
            break
    return items


def _sync_runner_handoff_file(
    *,
    paths,
    state: dict[str, Any],
    prd_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    selected_task: dict[str, Any] | None,
    phase_goal: str,
    project_root: Path,
) -> None:
    objective_title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or _as_text(state.get("current_goal"))
    next_task_id = _as_text(state.get("next_task_id"))
    next_task = _as_text(state.get("next_task"))
    next_task_reason = _as_text(state.get("next_task_reason"))
    current_phase = coerce_runner_phase(state.get("current_phase"), default="discover")
    phase_status = _as_text(state.get("phase_status")) or "active"
    status = _as_text(state.get("status")) or "ready"
    done_gate_status = _as_text(state.get("done_gate_status")) or "pending"
    phase_budget = int(state.get("phase_budget_minutes", DEFAULT_PHASE_BUDGET_MINUTES))
    task_acceptance = selected_task.get("acceptance") if isinstance(selected_task, dict) else []
    task_validation = selected_task.get("validation") if isinstance(selected_task, dict) else []
    if not isinstance(task_acceptance, list):
        task_acceptance = []
    if not isinstance(task_validation, list):
        task_validation = []
    completed_recent = [str(item).strip() for item in state.get("completed_recent", []) if str(item).strip()]
    blockers = [str(item).strip() for item in state.get("blockers", []) if str(item).strip()]
    open_items = _extract_open_tasks_from_payload(tasks_payload, max_items=8)
    selected_title = _as_text(selected_task.get("title")) if isinstance(selected_task, dict) else next_task
    lines = [
        f"# Runner Handoff ({project_root.name})",
        "",
        f"- Updated: {utc_now()}",
        f"- Objective: {objective_title or '(none)'}",
        f"- Phase: {current_phase}",
        f"- Phase status: {phase_status}",
        f"- Runner status: {status}",
        f"- Done gate: {done_gate_status}",
        f"- Phase budget minutes: {phase_budget}",
        "",
        "## Resume",
        "",
        f"- Phase goal: {phase_goal}",
        f"- Next task: {' '.join(part for part in (next_task_id, next_task or selected_title) if part).strip() or '(none)'}",
        f"- Why next: {next_task_reason or '(none)'}",
    ]
    if _as_text(state.get("last_iteration_summary")):
        lines.extend(["", "## Last Session Summary", "", _as_text(state.get("last_iteration_summary"))])
    if completed_recent:
        lines.extend(["", "## Recently Completed", ""])
        lines.extend([f"- {item}" for item in completed_recent[:5]])
    if task_acceptance:
        lines.extend(["", "## Acceptance For Current Slice", ""])
        lines.extend([f"- {str(item).strip()}" for item in task_acceptance[:5] if str(item).strip()])
    if task_validation:
        lines.extend(["", "## Validation For Current Slice", ""])
        lines.extend([f"- {str(item).strip()}" for item in task_validation[:5] if str(item).strip()])
    if blockers:
        lines.extend(["", "## Active Blockers", ""])
        lines.extend([f"- {item}" for item in blockers[:5]])
    if open_items:
        lines.extend(["", "## Remaining Open Tasks", ""])
        lines.extend([f"- {item}" for item in open_items])
    paths.runner_handoff_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _normalize_line(line: str) -> str:
    normalized = re.sub(r"\s+", " ", line).strip(" -\t")
    return normalized.strip("`*")


def _strip_task_id_prefix(title: str) -> str:
    return re.sub(r"^\(\s*TT-\d+\s*\)\s*", "", title).strip()


def _normalize_objective_title(raw: str) -> str:
    title = _normalize_line(raw)
    if not title:
        return ""
    prefixes = (
        "advance the active project objective with this next step:",
        "preserve direction from active objective:",
        "preserve direction from compacted conversation goal:",
    )
    lowered = title.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            suffix = title.split(":", 1)[1] if ":" in title else ""
            title = _normalize_line(suffix)
            break
    return title


def _task_priority_rank(priority: str) -> int:
    return TASK_PRIORITY_ORDER.get(priority.strip().lower(), 999)


def _normalize_task_entry(
    raw: dict[str, Any],
    *,
    index: int,
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> dict[str, Any]:
    task_id = _as_text(raw.get("task_id")) or f"TT-{index + 1:03d}"
    title = _strip_task_id_prefix(_normalize_line(_as_text(raw.get("title")))) or f"Task {index + 1}"
    status = _as_text(raw.get("status")) or "open"
    status = status.lower()
    if status not in TASK_STATUS_VALUES:
        status = "open"
    priority = (_as_text(raw.get("priority")) or "p1").lower()
    if priority not in TASK_PRIORITY_ORDER:
        priority = "p1"
    depends_raw = raw.get("depends_on")
    depends_on: list[str] = []
    if isinstance(depends_raw, list):
        for item in depends_raw:
            candidate = str(item).strip()
            if candidate:
                depends_on.append(candidate)
    acceptance_raw = raw.get("acceptance")
    acceptance: list[str] = []
    if isinstance(acceptance_raw, list):
        for item in acceptance_raw:
            candidate = _normalize_line(str(item))
            if candidate:
                acceptance.append(candidate[:220])
    validation_raw = raw.get("validation")
    validation: list[str] = []
    if isinstance(validation_raw, list):
        for item in validation_raw:
            candidate = _normalize_line(str(item))
            if candidate:
                validation.append(candidate[:220])
    if not acceptance:
        acceptance = [f"Complete task {task_id}: {title}"]
    if not validation:
        validation = ["Run project verification for this slice."]
    acceptance, validation = _harden_task_completion_contract(
        title=title,
        acceptance=acceptance,
        validation=validation,
    )
    updated_at = _as_text(raw.get("updated_at")) or utc_now()
    normalized = {
        "task_id": task_id,
        "title": title,
        "status": status,
        "priority": priority,
        "depends_on": depends_on,
        "project_root": _as_text(raw.get("project_root")) or str(project_root.resolve()),
        "target_branch": _as_text(raw.get("target_branch")) or target_branch,
        "acceptance": acceptance,
        "validation": validation,
        "updated_at": updated_at,
        "objective_id": _as_text(raw.get("objective_id")) or objective_id,
    }
    blocked_reason = _normalize_line(str(raw.get("blocked_reason", "")))
    if blocked_reason:
        normalized["blocked_reason"] = blocked_reason[:220]
    return normalized


def _looks_like_strict_parity_task(*, title: str, acceptance: list[str], validation: list[str]) -> bool:
    combined = " ".join([title, *acceptance, *validation]).lower()
    parity_markers = (
        "parity",
        "baseline",
        "latest committed",
        "old styling",
        "visual",
        "visualization",
        "styling",
        "spacing",
        "density",
        "ui/ux",
        "archive current tab",
        "regression",
    )
    return any(marker in combined for marker in parity_markers)


def _append_unique_line(lines: list[str], candidate: str) -> list[str]:
    normalized_candidate = _normalize_line(candidate)[:220]
    if not normalized_candidate:
        return lines
    existing = {_normalize_line(item).lower() for item in lines if _normalize_line(item)}
    if normalized_candidate.lower() not in existing:
        lines.append(normalized_candidate)
    return lines


def _harden_task_completion_contract(
    *,
    title: str,
    acceptance: list[str],
    validation: list[str],
) -> tuple[list[str], list[str]]:
    normalized_acceptance = [_normalize_line(item)[:220] for item in acceptance if _normalize_line(item)]
    normalized_validation = [_normalize_line(item)[:220] for item in validation if _normalize_line(item)]

    if not _looks_like_strict_parity_task(
        title=title,
        acceptance=normalized_acceptance,
        validation=normalized_validation,
    ):
        return normalized_acceptance, normalized_validation

    _append_unique_line(
        normalized_acceptance,
        "Completion is fail-closed: do not mark this task done until explicit baseline comparison shows no remaining known delta on the audited surfaces.",
    )
    _append_unique_line(
        normalized_validation,
        "Perform explicit side-by-side comparison against the recorded baseline for the touched surfaces.",
    )
    _append_unique_line(
        normalized_validation,
        "If any known visual or behavior delta remains, keep the task open and narrow the blocker instead of advancing.",
    )
    return normalized_acceptance, normalized_validation


def _default_prd(state: dict[str, Any], *, project: str, project_root: Path) -> dict[str, Any]:
    title = _normalize_objective_title(_as_text(state.get("current_goal"))) or f"{project} runner objective"
    objective_id = _as_text(state.get("objective_id")) or _new_objective_id()
    return {
        "objective_id": objective_id,
        "title": title,
        "scope_in": ["Complete canonical runner tasks for current objective"],
        "scope_out": ["Unscoped feature work not represented in TASKS.json"],
        "success_criteria": ["All tasks marked done and run_gates passes"],
        "constraints": ["Single runner_id=main", "Worktree/root scope must remain fixed"],
        "project_root": str(project_root.resolve()),
        "updated_at": utc_now(),
    }


def _ensure_prd(
    *,
    paths,
    state: dict[str, Any],
    project: str,
    project_root: Path,
) -> dict[str, Any]:
    payload = read_json(paths.prd_json)
    changed = False
    if not isinstance(payload, dict):
        payload = _default_prd(state, project=project, project_root=project_root)
        changed = True
    title = _normalize_objective_title(_as_text(payload.get("title")))
    if title:
        if title != _as_text(payload.get("title")):
            payload["title"] = title
            changed = True
    elif _as_text(payload.get("title")):
        payload["title"] = f"{project} runner objective"
        changed = True
    objective_id = _as_text(payload.get("objective_id"))
    if not objective_id:
        payload["objective_id"] = _new_objective_id()
        changed = True
    payload.setdefault("project_root", str(project_root.resolve()))
    payload.setdefault("scope_in", [])
    payload.setdefault("scope_out", [])
    payload.setdefault("success_criteria", [])
    payload.setdefault("constraints", [])
    payload.setdefault("updated_at", utc_now())
    if changed:
        payload["updated_at"] = utc_now()
        write_json(paths.prd_json, payload)
    return payload


def _ensure_tasks_payload(
    *,
    paths,
    prd_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
) -> tuple[dict[str, Any], bool]:
    objective_id = _as_text(prd_payload.get("objective_id")) or _new_objective_id()
    payload = read_json(paths.tasks_json)
    changed = False
    if not isinstance(payload, dict):
        payload = {"objective_id": objective_id, "tasks": []}
        changed = True
    if str(payload.get("objective_id", "")).strip() != objective_id:
        payload["objective_id"] = objective_id
        changed = True

    raw_tasks = payload.get("tasks")
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for idx, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                continue
            tasks.append(
                _normalize_task_entry(
                    raw,
                    index=idx,
                    project_root=project_root,
                    target_branch=target_branch,
                    objective_id=objective_id,
                )
            )

    if not tasks:
        tasks = [
            {
                "task_id": "TT-001",
                "title": "Execute the next concrete validated slice toward the active objective.",
                "status": "open",
                "priority": "p1",
                "depends_on": [],
                "project_root": str(project_root.resolve()),
                "target_branch": target_branch,
                "acceptance": ["Reach a concrete validated phase boundary for the active objective."],
                "validation": ["Run the validations required for the active slice and record the resulting blocker or completion state."],
                "updated_at": utc_now(),
                "objective_id": objective_id,
            }
        ]
        changed = True

    payload["tasks"] = tasks
    if changed:
        write_json(paths.tasks_json, payload)
    return payload, changed


def _task_depends_resolved(task: dict[str, Any], done_ids: set[str]) -> bool:
    deps = task.get("depends_on")
    if not isinstance(deps, list):
        return True
    for dep in deps:
        dep_id = str(dep).strip()
        if dep_id and dep_id not in done_ids:
            return False
    return True


def _select_next_task(tasks_payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None, "No tasks registry available."
    done_ids: set[str] = {
        str(task.get("task_id", "")).strip()
        for task in tasks
        if isinstance(task, dict) and str(task.get("status", "")).strip().lower() == "done"
    }
    open_tasks = [
        task for task in tasks if isinstance(task, dict) and str(task.get("status", "")).strip().lower() == "open"
    ]
    if not open_tasks:
        return None, "No open tasks remain in TASKS.json."

    def sort_key(task: dict[str, Any]) -> tuple[int, float, str]:
        priority = _task_priority_rank(str(task.get("priority", "p1")))
        updated = _parse_iso_ts(task.get("updated_at"))
        task_id = str(task.get("task_id", "")).strip()
        return (priority, updated, task_id)

    eligible = [task for task in open_tasks if _task_depends_resolved(task, done_ids)]
    if eligible:
        chosen = sorted(eligible, key=sort_key)[0]
        return chosen, "Deterministic selection: open + dependencies resolved + priority + oldest updated_at."
    blocked = sorted(open_tasks, key=sort_key)[0]
    return blocked, "Open tasks exist but dependencies are unresolved; selected oldest open task for visibility."


def _find_task_index(tasks_payload: dict[str, Any], task_id: str) -> int:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return -1
    needle = task_id.strip()
    for index, raw in enumerate(tasks):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("task_id", "")).strip() == needle:
            return index
    return -1


def _set_task_status(
    *,
    tasks_payload: dict[str, Any],
    task_id: str,
    status: str,
    blocked_reason: str | None = None,
) -> bool:
    idx = _find_task_index(tasks_payload, task_id)
    if idx < 0:
        return False
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    task = tasks[idx]
    if not isinstance(task, dict):
        return False
    task["status"] = status
    task["updated_at"] = utc_now()
    if blocked_reason:
        task["blocked_reason"] = blocked_reason[:220]
    elif "blocked_reason" in task:
        task.pop("blocked_reason", None)
    return True


def _open_task_entries(tasks_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).strip().lower()
        if status in {"open", "in_progress", "blocked"}:
            items.append(raw)
    return items


def _looks_like_done_closeout_task(task: dict[str, Any]) -> bool:
    title = _as_text(task.get("title")).lower()
    acceptance = " ".join(str(item).strip().lower() for item in task.get("acceptance", []) if str(item).strip())
    validation = " ".join(str(item).strip().lower() for item in task.get("validation", []) if str(item).strip())
    if "run_gates" not in validation:
        return False
    hints = (
        "done-state",
        "close the objective",
        "final done",
        "final gate",
        "closeout",
        "verify passes",
    )
    combined = " ".join((title, acceptance, validation))
    return any(hint in combined for hint in hints)


def _enforce_branch(project_root: Path, target_branch: str) -> tuple[bool, str]:
    branch = target_branch.strip()
    if not branch:
        return True, ""
    git_context = detect_git_context(project_root)
    current = git_context.get("git_branch") or ""
    head = git_context.get("git_head") or ""
    # Skip branch enforcement for non-git roots in dry/test environments.
    if not current and not head:
        return True, ""
    if current == branch:
        return True, ""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "checkout", branch],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, f"git checkout failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"git checkout {branch} failed"
    return True, ""


def _derive_runner_phase(
    *,
    state: dict[str, Any],
    selected_task: dict[str, Any] | None,
    next_task_text: str,
    selection_reason: str,
    blockers: list[str],
    open_tasks_count: int,
    status_value: str,
    done_gate_status_value: str,
    context_digest: str | None,
) -> str:
    previous_phase = coerce_runner_phase(state.get("current_phase"), default="discover")
    if open_tasks_count == 0 or "done-closeout" in next_task_text.lower() or done_gate_status_value == "failed":
        return "closeout"
    blocker_text = " ".join([next_task_text, selection_reason, *blockers]).lower()
    if status_value == "blocked" or any(token in blocker_text for token in ("verify", "validation", "typecheck", "test", "lint", "harness", "blocked")):
        return "verify"
    if not _as_text(state.get("phase_context_digest")) or not context_digest:
        return "discover"
    if not isinstance(selected_task, dict) or "no executable open task" in next_task_text.lower():
        return "discover"
    if previous_phase == "discover" and int(state.get("state_revision", 0)) <= 1:
        return "discover"
    return "implement"


def _build_phase_goal(
    *,
    phase: str,
    objective_title: str,
    next_task_text: str,
    blockers: list[str],
) -> str:
    if phase == "discover":
        return f"Load repo contract context and narrow the next concrete work surface for {objective_title}."
    if phase == "verify":
        blocker = blockers[0] if blockers else next_task_text
        return f"Focus on validation and blocker isolation: {blocker}"
    if phase == "closeout":
        return next_task_text or "Complete final closeout verification and converge to done."
    return next_task_text or f"Advance the current implementation surface for {objective_title}."


def _build_phase_context_delta(
    *,
    phase: str,
    objective_title: str,
    next_task_text: str,
    next_task_id: str | None,
    selection_reason: str,
    blockers: list[str],
    implementation_plan: list[str],
    open_tasks_count: int,
    done_gate_status_value: str,
    phase_status: str,
    completed_recent: list[str],
    last_iteration_summary: str,
    task_acceptance: list[str],
    task_validation: list[str],
) -> dict[str, Any]:
    return {
        "phase": phase,
        "objective_title": objective_title,
        "next_task_id": next_task_id,
        "next_task": next_task_text,
        "next_task_reason": selection_reason,
        "blockers_top3": blockers[:3],
        "implementation_plan_top2": implementation_plan[:2],
        "completed_recent_top5": completed_recent[:5],
        "last_iteration_summary": last_iteration_summary,
        "task_acceptance_top3": task_acceptance[:3],
        "task_validation_top3": task_validation[:3],
        "open_tasks_count": open_tasks_count,
        "done_gate_status": done_gate_status_value,
        "phase_status": phase_status,
    }


def _build_phase_plan(*, phase: str, phase_goal: str, next_task_id: str | None, next_task: str) -> list[str]:
    task_text = next_task or "No open tasks remain in TASKS.json."
    if next_task_id:
        task_text = f"{next_task_id}: {task_text}"
    if phase == "discover":
        return [
            phase_goal,
            "Use repo context sources plus runner delta to tighten the next work surface before coding.",
            "Refresh state once via runctl --setup, write prepared marker at phase boundary, then exit.",
        ]
    if phase == "verify":
        return [
            phase_goal,
            f"Stay on the current validation surface until blockers are cleared or narrowed: {task_text}",
            "Refresh state once via runctl --setup, write prepared marker at phase boundary, then exit.",
        ]
    if phase == "closeout":
        return [
            phase_goal,
            "Run final closeout gates and leave exactly one concrete blocker if done-state still fails.",
            "Refresh state once via runctl --setup, write prepared marker at phase boundary, then exit.",
        ]
    return [
        phase_goal,
        f"Stay on one coherent implementation surface while advancing: {task_text}",
        "Refresh state once via runctl --setup, write prepared marker at phase boundary, then exit.",
    ]


def _write_exec_context(
    *,
    paths,
    state: dict[str, Any],
    prd_payload: dict[str, Any],
    task: dict[str, Any] | None,
    project_root: Path,
    target_branch: str,
    phase: str,
    phase_goal: str,
    context_sources: list[dict[str, Any]],
    context_delta: dict[str, Any],
) -> None:
    existing_exec_context = read_json(paths.exec_context_json) or {}
    current_snapshot = _capture_progress_snapshot(state=state, project_root=project_root)
    preserved_cycle_baseline = (
        existing_exec_context.get("cycle_progress_baseline")
        if isinstance(existing_exec_context, dict)
        else None
    )
    if not isinstance(preserved_cycle_baseline, dict):
        preserved_cycle_baseline = current_snapshot
    task_id = _as_text(task.get("task_id")) if isinstance(task, dict) else ""
    task_title = _as_text(task.get("title")) if isinstance(task, dict) else ""
    acceptance = task.get("acceptance") if isinstance(task, dict) else []
    validation = task.get("validation") if isinstance(task, dict) else []
    if not isinstance(acceptance, list):
        acceptance = []
    if not isinstance(validation, list):
        validation = []
    parity_fail_closed = _looks_like_strict_parity_task(
        title=task_title,
        acceptance=[str(item) for item in acceptance if str(item).strip()],
        validation=[str(item) for item in validation if str(item).strip()],
    )
    hard_rules = [
        "stay_within_phase_goal",
        "validate_as_you_go",
        "refresh_runner_state_once",
        "write_prepared_marker_at_phase_boundary",
        "exit_session_on_phase_handoff",
    ]
    if parity_fail_closed:
        hard_rules.append("parity_tasks_fail_closed_until_no_known_delta_remains")
    payload = {
        "objective_id": _as_text(prd_payload.get("objective_id")),
        "phase": phase,
        "phase_goal": phase_goal,
        "task_id": task_id or None,
        "task_title": task_title or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "next_task_reason": _as_text(state.get("next_task_reason")) or None,
        "task_acceptance": [str(item) for item in acceptance if str(item).strip()],
        "task_validation": [str(item) for item in validation if str(item).strip()],
        "project_root": str(project_root.resolve()),
        "target_branch": target_branch or None,
        "state_revision": int(state.get("state_revision", 0)),
        "phase_budget_minutes": int(state.get("phase_budget_minutes", DEFAULT_PHASE_BUDGET_MINUTES)),
        "phase_started_at": _as_text(state.get("phase_started_at")) or utc_now(),
        "context_sources": context_sources,
        "context_delta": context_delta,
        "progress_baseline": current_snapshot,
        "cycle_progress_baseline": preserved_cycle_baseline,
        "hard_rules": hard_rules,
        "generated_at": utc_now(),
    }
    write_json(paths.exec_context_json, payload)

def _write_prepared_cycle_marker(
    *,
    paths,
    state: dict[str, Any],
    project: str,
    runner_id: str,
    project_root: Path,
) -> Path:
    exec_context = read_json(paths.exec_context_json) or {}
    phase = coerce_runner_phase(state.get("current_phase") or exec_context.get("phase"), default="implement")
    if _as_text(state.get("status")) == "blocked" and phase == "discover":
        phase = "verify"
    if _as_text(state.get("status")) == "done" and phase == "discover":
        phase = "closeout"
    phase_started_at = _as_text(state.get("phase_started_at")) or _as_text(exec_context.get("phase_started_at"))
    handoff_reason = "phase_boundary"
    if _as_text(state.get("status")) == "done":
        handoff_reason = "done"
    elif _as_text(state.get("status")) == "blocked":
        handoff_reason = "blocked"
    elif coerce_runner_phase(exec_context.get("phase"), default=phase) != phase:
        handoff_reason = "phase_change"

    budget_exhausted = False
    if phase_started_at:
        try:
            started_at = datetime.strptime(phase_started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
            budget_exhausted = elapsed_seconds >= (int(state.get("phase_budget_minutes", DEFAULT_PHASE_BUDGET_MINUTES)) * 60)
        except ValueError:
            budget_exhausted = False
    if budget_exhausted:
        handoff_reason = "budget_expired"

    marker_payload: dict[str, Any] = {
        "prepared_at": utc_now(),
        "project": project,
        "runner_id": runner_id,
        "git_worktree": str(project_root.resolve()),
        "phase": phase,
        "handoff_reason": handoff_reason,
        "budget_exhausted": budget_exhausted,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "state_revision": int(state.get("state_revision", 0)),
        "status": _as_text(state.get("status")) or None,
    }
    write_json(paths.cycle_prepared_file, marker_payload)
    return paths.cycle_prepared_file


def _capture_progress_snapshot(*, state: dict[str, Any], project_root: Path) -> dict[str, Any]:
    git_context = detect_git_context(project_root)
    return {
        "phase": coerce_runner_phase(state.get("current_phase"), default="discover"),
        "phase_status": _as_text(state.get("phase_status")) or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "status": _as_text(state.get("status")) or None,
        "git_head": _as_text(git_context.get("git_head")) or None,
        "worktree_fingerprint": compute_worktree_fingerprint(project_root),
    }


def _progress_has_advanced(*, baseline: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not isinstance(baseline, dict):
        return True
    return any(
        (
            coerce_runner_phase(baseline.get("phase"), default="discover")
            != coerce_runner_phase(current.get("phase"), default="discover"),
            _as_text(baseline.get("phase_status")) != _as_text(current.get("phase_status")),
            _as_text(baseline.get("next_task_id")) != _as_text(current.get("next_task_id")),
            _as_text(baseline.get("next_task")) != _as_text(current.get("next_task")),
            _as_text(baseline.get("status")) != _as_text(current.get("status")),
            _as_text(baseline.get("git_head")) != _as_text(current.get("git_head")),
            _as_text(baseline.get("worktree_fingerprint")) != _as_text(current.get("worktree_fingerprint")),
        )
    )


def _run_done_closeout_gates(*, project_root: Path, gates_file: Path, runner_id: str) -> tuple[bool | None, str]:
    if not gates_file.exists():
        return None, f"Missing gates file: {gates_file}"

    cmd = (
        "set -euo pipefail; "
        f"cd {shlex.quote(str(project_root))}; "
        f"source {shlex.quote(str(gates_file))}; "
        f"declare -F run_gates >/dev/null || {{ echo {shlex.quote(f'gates.sh must define run_gates: {gates_file}')}; exit 1; }}; "
        "run_gates"
    )
    env = os.environ.copy()
    env["RUNNER_ID"] = runner_id
    env["MEMORY_DIR"] = str(project_root / ".memory")
    try:
        result = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        return False, f"run_gates execution failed: {exc}"

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode == 0:
        return True, output
    return False, output or "run_gates failed"


def _summarize_gate_failure(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "run_gates failed"
    return lines[-1]


def _cleanup_runner_dir(paths) -> None:
    managed: set[Path] = {path.resolve() for path in managed_runner_files(paths)}
    managed_dirs: set[Path] = set()
    for path in managed:
        parent = path.parent
        while parent != paths.runner_dir.parent:
            managed_dirs.add(parent)
            if parent == paths.runner_dir:
                break
            parent = parent.parent
    if not paths.runner_dir.exists():
        return
    for entry in paths.runner_dir.iterdir():
        resolved = entry.resolve()
        if resolved in managed or resolved in managed_dirs:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def _remove_legacy_memory_views(paths) -> None:
    """Delete deprecated legacy view files on setup migration."""
    legacy_files = (
        paths.memory_dir / "GOALS.md",
        paths.runner_dir / "RUNNER_NEXT.md",
        paths.runner_dir / "RUNNER_DOD.md",
        paths.runner_dir / "RUNNER_PLAN.md",
        paths.runner_dir / "PRD.md",
        paths.runner_dir / "TASKS.md",
    )
    for legacy_path in legacy_files:
        legacy_path.unlink(missing_ok=True)


def _render_project_prd_markdown(
    *,
    prd_payload: dict[str, Any],
    state: dict[str, Any],
    tasks_payload: dict[str, Any],
) -> str:
    title = _as_text(prd_payload.get("title")) or "Project objective"
    objective_id = _as_text(prd_payload.get("objective_id"))
    project_root = _as_text(prd_payload.get("project_root"))
    constraints = [str(item).strip() for item in prd_payload.get("constraints", []) if str(item).strip()]
    scope_in = [str(item).strip() for item in prd_payload.get("scope_in", []) if str(item).strip()]
    scope_out = [str(item).strip() for item in prd_payload.get("scope_out", []) if str(item).strip()]
    success_criteria = [str(item).strip() for item in prd_payload.get("success_criteria", []) if str(item).strip()]
    next_task_id = _as_text(state.get("next_task_id"))
    next_task = _as_text(state.get("next_task"))
    current_phase = _as_text(state.get("current_phase"))
    target_branch = _as_text(state.get("target_branch"))
    open_tasks = [
        task for task in tasks_payload.get("tasks", [])
        if isinstance(task, dict) and str(task.get("status", "")).strip() in {"open", "in_progress", "blocked"}
    ]

    lines = [f"# {title}", ""]
    if objective_id:
        lines.append(f"- Objective ID: `{objective_id}`")
    if project_root:
        lines.append(f"- Project root: `{project_root}`")
    if target_branch:
        lines.append(f"- Target branch: `{target_branch}`")
    if current_phase:
        lines.append(f"- Current phase: `{current_phase}`")
    if next_task_id or next_task:
        task_label = " ".join(part for part in (next_task_id, next_task) if part).strip()
        lines.append(f"- Next task: {task_label}")
    lines.append("")

    def _append_section(label: str, items: list[str]) -> None:
        if not items:
            return
        lines.append(f"## {label}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    _append_section("Scope In", scope_in)
    _append_section("Scope Out", scope_out)
    _append_section("Constraints", constraints)
    _append_section("Success Criteria", success_criteria)

    if open_tasks:
        lines.append("## Open Tasks")
        lines.append("")
        for task in open_tasks:
            task_id = _as_text(task.get("task_id")) or "TT-???"
            task_title = _as_text(task.get("title")) or "Untitled task"
            task_status = _as_text(task.get("status")) or "open"
            lines.append(f"- `{task_id}` [{task_status}] {task_title}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _sync_project_prd_file(
    *,
    paths,
    prd_payload: dict[str, Any],
    state: dict[str, Any],
    tasks_payload: dict[str, Any],
) -> None:
    markdown = _render_project_prd_markdown(
        prd_payload=prd_payload,
        state=state,
        tasks_payload=tasks_payload,
    )
    paths.project_prd_file.write_text(markdown, encoding="utf-8")
    if paths.legacy_refactor_status_file.exists():
        paths.legacy_refactor_status_file.unlink(missing_ok=True)


def create_runner_state(
    dev: str,
    project: str,
    runner_id: str,
    approve_enable: str | None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Idempotent create + optional enable approval."""
    resolved_root = (project_root or (Path(dev) / "Repos" / project)).resolve()
    paths = build_runner_state_paths_for_root(
        project_root=resolved_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    ensure_memory_dir(paths)
    created: list[str] = []
    if approve_enable is None:
        _cleanup_runner_dir(paths)
        _remove_legacy_memory_views(paths)
    project_root = resolved_root
    _write_runner_context(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )

    preserved_enabled = False
    preserved_hil_decision: str | None = None
    previous_done_gate = "pending"
    previous_done_candidate = False
    previous_status = ""
    previous_state = read_json(paths.state_file) or {}
    if approve_enable is None:
        preserved_enabled = bool(previous_state.get("enabled", False))
        raw_decision = previous_state.get("last_hil_decision")
        if preserved_enabled and isinstance(raw_decision, str) and raw_decision.strip():
            preserved_hil_decision = raw_decision.strip()
        previous_status = str(previous_state.get("status", "")).strip().lower()
        previous_done_gate = str(previous_state.get("done_gate_status", "")).strip().lower()
        previous_done_candidate = bool(previous_state.get("done_candidate", False))
        paths.stop_lock.unlink(missing_ok=True)
        paths.active_lock.unlink(missing_ok=True)

    state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
    if approve_enable is None and preserved_enabled:
        state = update_state(
            paths.state_file,
            state,
            status="ready",
            enabled=True,
            last_hil_decision=preserved_hil_decision or "enable_approved",
        )

    git_context = detect_git_context(project_root)
    branch_guess = (
        _as_text(state.get("target_branch"))
        or _as_text(git_context.get("git_branch"))
        or _as_text(state.get("git_branch"))
        or "main"
    )

    prd_payload = _ensure_prd(
        paths=paths,
        state=state,
        project=project,
        project_root=project_root,
    )
    tasks_payload, tasks_changed = _ensure_tasks_payload(
        paths=paths,
        prd_payload=prd_payload,
        project_root=project_root,
        target_branch=branch_guess,
    )
    tasks_payload, intake_merged, merged_count = _merge_task_intake(
        paths=paths,
        tasks_payload=tasks_payload,
        project_root=project_root,
        target_branch=branch_guess,
        objective_id=_as_text(prd_payload.get("objective_id")) or _new_objective_id(),
    )
    if intake_merged:
        tasks_changed = True
    if tasks_changed:
        created.append(str(paths.tasks_json.relative_to(paths.memory_dir)))

    closeout_gate_result: tuple[bool | None, str] | None = None
    open_task_candidates = _open_task_entries(tasks_payload)
    if len(open_task_candidates) == 1 and _looks_like_done_closeout_task(open_task_candidates[0]):
        closeout_gate_result = _run_done_closeout_gates(
            project_root=project_root,
            gates_file=paths.gates_file,
            runner_id=runner_id,
        )
        if closeout_gate_result[0] is True:
            closeout_task_id = _as_text(open_task_candidates[0].get("task_id"))
            if closeout_task_id and _set_task_status(
                tasks_payload=tasks_payload,
                task_id=closeout_task_id,
                status="done",
            ):
                tasks_changed = True

    selected_task, selection_reason = _select_next_task(tasks_payload)
    target_branch = branch_guess
    if isinstance(selected_task, dict):
        task_branch = str(selected_task.get("target_branch", "")).strip()
        if task_branch:
            target_branch = task_branch

    branch_error = ""
    if approve_enable is None and isinstance(selected_task, dict):
        branch_ok, branch_error = _enforce_branch(project_root, target_branch)
        if not branch_ok:
            task_id = str(selected_task.get("task_id", "")).strip()
            if task_id:
                _set_task_status(
                    tasks_payload=tasks_payload,
                    task_id=task_id,
                    status="blocked",
                    blocked_reason=f"branch_enforcement_failed: {branch_error}",
                )
                write_json(paths.tasks_json, tasks_payload)
            selected_task = None
            selection_reason = "Branch enforcement failed; selected task moved to blocked."

    git_context = detect_git_context(project_root)
    objective_id = _as_text(prd_payload.get("objective_id")) or _new_objective_id()
    objective_title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or f"{project} runner objective"
    open_tasks_count = count_open_tasks(tasks_payload)

    next_task_id = _as_text(selected_task.get("task_id")) if isinstance(selected_task, dict) else None
    next_task_title = _as_text(selected_task.get("title")) if isinstance(selected_task, dict) else ""
    if next_task_title:
        next_task_text = next_task_title
    elif open_tasks_count == 0:
        next_task_text = "No open tasks remain in TASKS.json."
    else:
        next_task_text = "No executable open task is currently available."

    blockers = list(state.get("blockers", []))
    if not isinstance(blockers, list):
        blockers = []
    if branch_error:
        blockers.append(f"Branch enforcement failed: {branch_error}")
    blockers = [str(item).strip() for item in blockers if str(item).strip()][-8:]

    state_revision = int(state.get("state_revision", 0)) + 1
    status_value = "ready"
    done_candidate_value = False
    done_gate_status_value = "pending"
    if open_tasks_count == 0:
        blockers = []
    if open_tasks_count == 0:
        preserve_done_state = paths.done_lock.exists() or (
            previous_done_gate == "passed" and (previous_done_candidate or previous_status == "done")
        )
        if preserve_done_state:
            status_value = "done"
            done_candidate_value = True
            done_gate_status_value = "passed"
        else:
            if closeout_gate_result is None:
                gates_ok, gates_output = _run_done_closeout_gates(
                    project_root=project_root,
                    gates_file=paths.gates_file,
                    runner_id=runner_id,
                )
            else:
                gates_ok, gates_output = closeout_gate_result
            if gates_ok is True:
                status_value = "done"
                done_candidate_value = True
                done_gate_status_value = "passed"
                paths.done_lock.parent.mkdir(parents=True, exist_ok=True)
                paths.done_lock.write_text(
                    f"created_at={utc_now()}\nproject={project}\nrunner_id={runner_id}\nsetup_refresh=1\n",
                    encoding="utf-8",
                )
            else:
                status_value = "ready"
                paths.done_lock.unlink(missing_ok=True)
                if gates_ok is False:
                    done_gate_status_value = "failed"
                    gate_summary = _summarize_gate_failure(gates_output)
                    next_task_text = "Resolve final done-closeout gate failure."
                    selection_reason = "No open tasks remain, but done-closeout validation is failing."
                    blockers.append(f"Final done closeout blocked: {gate_summary}"[:220])
                else:
                    done_gate_status_value = "pending"
                    next_task_text = "Resolve final done-closeout validation prerequisites."
                    selection_reason = "No open tasks remain, but done-closeout validation could not run yet."
    elif branch_error:
        status_value = "blocked"
        paths.done_lock.unlink(missing_ok=True)
    else:
        status_value = "ready"
        paths.done_lock.unlink(missing_ok=True)

    _sync_project_prd_file(
        paths=paths,
        prd_payload=prd_payload,
        state=state,
        tasks_payload=tasks_payload,
    )
    context_sources = _collect_context_sources(project_root)
    phase_context_digest = _digest_text(
        "|".join(str(source.get("digest", "")) for source in context_sources if str(source.get("digest", "")).strip())
    ) if context_sources else None
    current_phase = _derive_runner_phase(
        state=state,
        selected_task=selected_task if isinstance(selected_task, dict) else None,
        next_task_text=next_task_text,
        selection_reason=selection_reason,
        blockers=blockers,
        open_tasks_count=open_tasks_count,
        status_value=status_value,
        done_gate_status_value=done_gate_status_value,
        context_digest=phase_context_digest,
    )
    previous_phase = coerce_runner_phase(state.get("current_phase"), default="discover")
    previous_phase_started_at = _as_text(state.get("phase_started_at"))
    phase_started_at = previous_phase_started_at if current_phase == previous_phase and previous_phase_started_at else utc_now()
    phase_status = "active"
    if status_value == "blocked":
        phase_status = "blocked"
    elif status_value == "done":
        phase_status = "handoff_ready"
    phase_goal = _build_phase_goal(
        phase=current_phase,
        objective_title=objective_title,
        next_task_text=next_task_text,
        blockers=blockers,
    )
    implementation_plan = _build_phase_plan(
        phase=current_phase,
        phase_goal=phase_goal,
        next_task_id=next_task_id,
        next_task=next_task_text,
    )
    context_delta = _build_phase_context_delta(
        phase=current_phase,
        objective_title=objective_title,
        next_task_text=next_task_text,
        next_task_id=next_task_id,
        selection_reason=selection_reason,
        blockers=blockers,
        implementation_plan=implementation_plan,
        open_tasks_count=open_tasks_count,
        done_gate_status_value=done_gate_status_value,
        phase_status=phase_status,
        completed_recent=[str(item).strip() for item in state.get("completed_recent", []) if str(item).strip()],
        last_iteration_summary=_as_text(state.get("last_iteration_summary")),
        task_acceptance=[
            str(item).strip()
            for item in ((selected_task or {}).get("acceptance") if isinstance(selected_task, dict) else [])
            if str(item).strip()
        ],
        task_validation=[
            str(item).strip()
            for item in ((selected_task or {}).get("validation") if isinstance(selected_task, dict) else [])
            if str(item).strip()
        ],
    )

    state = update_state(
        paths.state_file,
        state,
        status=status_value,
        current_step="",
        current_goal=objective_title,
        next_task=next_task_text,
        next_task_reason=selection_reason,
        implementation_plan=implementation_plan,
        objective_id=objective_id,
        next_task_id=next_task_id,
        current_task_id=None,
        task_selection_reason=selection_reason,
        project_root=str(project_root.resolve()),
        target_branch=target_branch,
        state_revision=state_revision,
        done_candidate=done_candidate_value,
        done_gate_status=done_gate_status_value,
        current_phase=current_phase,
        phase_status=phase_status,
        phase_started_at=phase_started_at,
        phase_budget_minutes=int(state.get("phase_budget_minutes", DEFAULT_PHASE_BUDGET_MINUTES)),
        phase_context_digest=phase_context_digest,
        blockers=blockers,
        **git_context,
    )

    _sync_project_prd_file(
        paths=paths,
        prd_payload=prd_payload,
        state=state,
        tasks_payload=tasks_payload,
    )
    _sync_runner_handoff_file(
        paths=paths,
        state=state,
        prd_payload=prd_payload,
        tasks_payload=tasks_payload,
        selected_task=selected_task if isinstance(selected_task, dict) else None,
        phase_goal=phase_goal,
        project_root=project_root,
    )
    context_sources = _collect_context_sources(project_root)
    phase_context_digest = _digest_text(
        "|".join(str(source.get("digest", "")) for source in context_sources if str(source.get("digest", "")).strip())
    ) if context_sources else None
    state = update_state(paths.state_file, state, phase_context_digest=phase_context_digest)

    write_json(paths.prd_json, prd_payload)
    write_json(paths.tasks_json, tasks_payload)
    _write_exec_context(
        paths=paths,
        state=state,
        prd_payload=prd_payload,
        task=selected_task if isinstance(selected_task, dict) else None,
        project_root=project_root,
        target_branch=target_branch,
        phase=current_phase,
        phase_goal=phase_goal,
        context_sources=context_sources,
        context_delta=context_delta,
    )

    if not paths.ledger_file.exists():
        paths.ledger_file.touch()
        created.append(str(paths.ledger_file.relative_to(paths.memory_dir)))

    pending = read_json(paths.enable_pending)
    setup_requires_enable = not bool(state.get("enabled")) and str(state.get("status", "")).strip().lower() != "done"
    if pending is None and setup_requires_enable:
        token = _new_token()
        pending = {
            "runner_id": runner_id,
            "project": project,
            "token": token,
            "status": "pending",
            "created_at": utc_now(),
            "reason": "Approve loop enablement",
        }
        write_json(paths.enable_pending, pending)
        created.append(str(paths.enable_pending.relative_to(paths.memory_dir)))
    elif pending is not None and setup_requires_enable and approve_enable is None:
        token = _new_token()
        pending["token"] = token
        pending["created_at"] = utc_now()
        pending["status"] = "pending"
        write_json(paths.enable_pending, pending)
    elif pending is not None and not setup_requires_enable:
        paths.enable_pending.unlink(missing_ok=True)
        pending = None
        token = ""
    elif pending is not None:
        token = str(pending.get("token", ""))
    else:
        token = ""

    if approve_enable is not None:
        if pending is None:
            return {
                "ok": False,
                "error": "No pending enable request exists for this runner.",
                "project": project,
                "runner_id": runner_id,
            }
        if approve_enable != token:
            return {
                "ok": False,
                "error": "Invalid --approve-enable token.",
                "project": project,
                "runner_id": runner_id,
            }

        if paths.enable_pending.exists():
            paths.enable_pending.unlink()
        state = update_state(
            paths.state_file,
            state,
            status="ready",
            enabled=True,
            last_hil_decision="enable_approved",
            runtime_policy={
                "runner_mode": "exec",
                "session_strategy": "fresh_session",
            },
        )
        append_ndjson(
            paths.ledger_file,
            {
                "ts": utc_now(),
                "event": "hil.enable.approved",
                "project": project,
                "runner_id": runner_id,
                "token": token,
            },
        )
        return {
            "ok": True,
            "project": project,
            "runner_id": runner_id,
            "enabled": True,
            "created": created,
            "state_file": str(paths.state_file),
            "ledger_file": str(paths.ledger_file),
        }

    setup_event = {
        "ts": utc_now(),
        "event": "runner.setup",
        "project": project,
        "runner_id": runner_id,
        "idempotent": True,
        "objective_id": objective_id,
        "phase": current_phase,
        "next_task_id": next_task_id,
        "open_tasks": open_tasks_count,
        "merged_task_intake_count": merged_count,
        "created": created,
    }
    last_event = _read_last_ndjson_event(paths.ledger_file)
    if isinstance(last_event, dict):
        try:
            last_open_tasks = int(last_event.get("open_tasks", -1))
        except (TypeError, ValueError):
            last_open_tasks = -1
        same_setup_shape = (
            str(last_event.get("event", "")).strip() == "runner.setup"
            and str(last_event.get("project", "")).strip() == project
            and str(last_event.get("runner_id", "")).strip() == runner_id
            and str(last_event.get("objective_id", "")).strip() == objective_id
            and str(last_event.get("phase", "")).strip() == current_phase
            and str(last_event.get("next_task_id", "")).strip() == (next_task_id or "")
            and last_open_tasks == open_tasks_count
        )
        if same_setup_shape:
            age_seconds = _parse_iso_ts(setup_event.get("ts")) - _parse_iso_ts(last_event.get("ts"))
            if 0 <= age_seconds <= SETUP_EVENT_COALESCE_SECONDS:
                return {
                    "ok": True,
                    "project": project,
                    "runner_id": runner_id,
                    "enabled": bool(state.get("enabled")),
                    "created": created,
                    "state_file": str(paths.state_file),
                    "ledger_file": str(paths.ledger_file),
                    "enable_token": token,
                    "enable_pending_file": str(paths.enable_pending) if paths.enable_pending.exists() else None,
                }

    append_ndjson(paths.ledger_file, setup_event)

    return {
        "ok": True,
        "project": project,
        "runner_id": runner_id,
        "enabled": bool(state.get("enabled")),
        "created": created,
        "state_file": str(paths.state_file),
        "ledger_file": str(paths.ledger_file),
        "enable_token": token,
        "enable_pending_file": str(paths.enable_pending) if paths.enable_pending.exists() else None,
    }


def inspect_runner_start_state(
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Validate that a runner is already prepared for loop startup."""
    return _inspect_runner_start_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
        allow_refresh_repair=True,
    )


def _inspect_runner_start_state(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path | None,
    allow_refresh_repair: bool,
) -> dict[str, Any]:
    resolved_root = (project_root or (Path(dev) / "Repos" / project)).resolve()
    paths = build_runner_state_paths_for_root(
        project_root=resolved_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )

    state = read_json(paths.state_file)
    if not isinstance(state, dict):
        return {
            "ok": False,
            "error": "Runner is not set up. Run /prompts:run_setup first.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    status = str(state.get("status", "")).strip().lower()
    if paths.done_lock.exists() or status == "done":
        return {
            "ok": False,
            "error": "Runner is already complete. Clear or set up a new run first.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    if not bool(state.get("enabled")):
        pending = read_json(paths.enable_pending)
        token_hint = ""
        if isinstance(pending, dict):
            token = str(pending.get("token", "")).strip()
            if token:
                token_hint = f" Pending enable token: {token}"
        return {
            "ok": False,
            "error": (
                "Runner is not enabled. Run /prompts:run_setup and approve enablement before starting."
                f"{token_hint}"
            ),
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    next_task_id = _as_text(state.get("next_task_id"))
    next_task = _as_text(state.get("next_task"))
    if not next_task_id or not next_task:
        if allow_refresh_repair:
            create_runner_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                approve_enable=None,
                project_root=resolved_root,
            )
            return _inspect_runner_start_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=resolved_root,
                allow_refresh_repair=False,
            )
        return {
            "ok": False,
            "error": "Runner state is missing next-task info. Run /prompts:run_setup again.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    tasks_payload = read_json(paths.tasks_json)
    tasks = tasks_payload.get("tasks", []) if isinstance(tasks_payload, dict) else []
    matching_task = None
    if isinstance(tasks, list):
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if _as_text(task.get("task_id")) == next_task_id:
                matching_task = task
                break
    if matching_task is None:
        if allow_refresh_repair:
            create_runner_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                approve_enable=None,
                project_root=resolved_root,
            )
            return _inspect_runner_start_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=resolved_root,
                allow_refresh_repair=False,
            )
        return {
            "ok": False,
            "error": f"Runner task {next_task_id} is missing from TASKS.json. Run /prompts:run_setup again.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    task_status = _as_text(matching_task.get("status")).lower()
    if task_status not in {"open", "in_progress", "blocked"}:
        if allow_refresh_repair:
            create_runner_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                approve_enable=None,
                project_root=resolved_root,
            )
            return _inspect_runner_start_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=resolved_root,
                allow_refresh_repair=False,
            )
        return {
            "ok": False,
            "error": f"Runner task {next_task_id} is not startable (status={task_status or 'unknown'}).",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    return {
        "ok": True,
        "project": project,
        "runner_id": runner_id,
        "project_root": str(resolved_root),
        "state_file": str(paths.state_file),
        "tasks_file": str(paths.tasks_json),
        "next_task_id": next_task_id,
        "next_task": next_task,
    }


def clear_runner_state(
    dev: str,
    project: str,
    runner_id: str,
    confirm: str | None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Two-phase destructive clear with explicit token confirmation."""
    resolved_root = (project_root or (Path(dev) / "Repos" / project)).resolve()
    paths = build_runner_state_paths_for_root(
        project_root=resolved_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    ensure_memory_dir(paths)

    if confirm is None:
        token = _new_token()
        manifest_paths = [path for path in managed_runner_files(paths=paths) if path.exists()]
        manifest = [str(path) for path in manifest_paths]
        payload = {
            "project": project,
            "runner_id": runner_id,
            "token": token,
            "created_at": utc_now(),
            "manifest": manifest,
        }
        write_json(paths.clear_pending, payload)
        return {
            "ok": True,
            "phase": "pending",
            "project": project,
            "runner_id": runner_id,
            "confirm_token": token,
            "clear_pending_file": str(paths.clear_pending),
            "manifest_count": len(manifest),
        }

    pending = read_json(paths.clear_pending)
    if pending is None:
        return {
            "ok": False,
            "error": "No pending clear request found. Run --clear first without --confirm.",
            "project": project,
            "runner_id": runner_id,
        }

    token = str(pending.get("token", ""))
    if confirm != token:
        return {
            "ok": False,
            "error": "Invalid --confirm token.",
            "project": project,
            "runner_id": runner_id,
        }

    deleted = 0
    runner_dir_resolved = paths.runner_dir.resolve()
    for raw_path in pending.get("manifest", []):
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            # Runner folder is removed in one recursive operation below.
            path.resolve().relative_to(runner_dir_resolved)
            continue
        except ValueError:
            pass
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            deleted += 1
        elif path.exists():
            path.unlink()
            deleted += 1

    if paths.runner_dir.exists():
        shutil.rmtree(paths.runner_dir, ignore_errors=True)
        deleted += 1

    _clear_runner_context_if_matches(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=resolved_root,
    )

    return {
        "ok": True,
        "phase": "cleared",
        "project": project,
        "runner_id": runner_id,
        "deleted": deleted,
    }


def _load_or_fail_tasks(paths) -> tuple[dict[str, Any] | None, str | None]:
    payload = read_json(paths.tasks_json)
    if not isinstance(payload, dict):
        return None, f"TASKS missing: run --setup first ({paths.tasks_json})"
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None, f"Invalid TASKS payload: {paths.tasks_json}"
    return payload, None


def _load_task_intake_payload(paths) -> dict[str, Any]:
    payload = read_json(paths.task_intake_file)
    if not isinstance(payload, dict):
        return {"tasks": []}
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        payload["tasks"] = []
    return payload


def _write_task_intake_payload(paths, payload: dict[str, Any]) -> None:
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        paths.task_intake_file.unlink(missing_ok=True)
        return
    write_json(paths.task_intake_file, payload)


def _iter_pending_intake_entries(paths) -> list[dict[str, Any]]:
    payload = _load_task_intake_payload(paths)
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in tasks:
        if isinstance(raw, dict):
            items.append(raw)
    return items


def _next_task_id(tasks_payload: dict[str, Any], pending_entries: list[dict[str, Any]]) -> str:
    highest = 0
    candidates: list[str] = []
    tasks = tasks_payload.get("tasks")
    if isinstance(tasks, list):
        for raw in tasks:
            if isinstance(raw, dict):
                candidates.append(_as_text(raw.get("task_id")))
    for raw in pending_entries:
        candidates.append(_as_text(raw.get("task_id")))
    for candidate in candidates:
        match = re.match(r"^TT-(\d+)$", candidate)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"TT-{highest + 1:03d}"


def _resolve_non_interfering_anchor_task_id(
    *,
    state: dict[str, Any],
    tasks_payload: dict[str, Any],
) -> str | None:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    openish_ids = {
        _as_text(raw.get("task_id"))
        for raw in tasks
        if isinstance(raw, dict) and _as_text(raw.get("status")).lower() in {"open", "in_progress", "blocked"}
    }
    for field in ("current_task_id", "next_task_id"):
        candidate = _as_text(state.get(field))
        if candidate and candidate in openish_ids:
            return candidate
    return None


def _collect_known_task_ids(tasks_payload: dict[str, Any], pending_entries: list[dict[str, Any]]) -> set[str]:
    known: set[str] = set()
    tasks = tasks_payload.get("tasks")
    if isinstance(tasks, list):
        for raw in tasks:
            if isinstance(raw, dict):
                task_id = _as_text(raw.get("task_id"))
                if task_id:
                    known.add(task_id)
    for raw in pending_entries:
        task_id = _as_text(raw.get("task_id"))
        if task_id:
            known.add(task_id)
    return known


def _merge_task_intake(
    *,
    paths,
    tasks_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> tuple[dict[str, Any], bool, int]:
    intake_payload = _load_task_intake_payload(paths)
    pending_entries = intake_payload.get("tasks")
    if not isinstance(pending_entries, list) or not pending_entries:
        return tasks_payload, False, 0

    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        tasks_payload["tasks"] = tasks

    existing_ids = {
        _as_text(raw.get("task_id"))
        for raw in tasks
        if isinstance(raw, dict) and _as_text(raw.get("task_id"))
    }
    merged_count = 0
    remaining_entries: list[dict[str, Any]] = []
    for raw in pending_entries:
        if not isinstance(raw, dict):
            continue
        task_id = _as_text(raw.get("task_id"))
        if not task_id or task_id in existing_ids:
            continue
        depends_on = []
        raw_depends = raw.get("depends_on")
        if isinstance(raw_depends, list):
            for item in raw_depends:
                candidate = str(item).strip()
                if candidate:
                    depends_on.append(candidate)
        anchor_task_id = _as_text(raw.get("anchor_task_id"))
        if bool(raw.get("anchor_current_task", False)) and anchor_task_id and anchor_task_id not in depends_on:
            depends_on.append(anchor_task_id)
        normalized = _normalize_task_entry(
            {
                "task_id": task_id,
                "title": _as_text(raw.get("title")),
                "status": "open",
                "priority": _as_text(raw.get("priority")) or "p1",
                "depends_on": depends_on,
                "project_root": _as_text(raw.get("project_root")) or str(project_root.resolve()),
                "target_branch": _as_text(raw.get("target_branch")) or target_branch,
                "acceptance": raw.get("acceptance"),
                "validation": raw.get("validation"),
                "updated_at": _as_text(raw.get("queued_at")) or utc_now(),
                "objective_id": _as_text(raw.get("objective_id")) or objective_id,
            },
            index=len(tasks),
            project_root=project_root,
            target_branch=target_branch,
            objective_id=objective_id,
        )
        tasks.append(normalized)
        existing_ids.add(task_id)
        merged_count += 1

    if remaining_entries:
        intake_payload["tasks"] = remaining_entries
        _write_task_intake_payload(paths, intake_payload)
    else:
        paths.task_intake_file.unlink(missing_ok=True)
    return tasks_payload, merged_count > 0, merged_count


def _handle_task_command(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
    action: str,
    task_id: str | None,
    task_status: str | None,
    query: str | None,
    title: str | None,
    priority: str | None,
    depends_on: list[str] | None,
    acceptance: list[str] | None,
    validation: list[str] | None,
    allow_preempt: bool,
) -> tuple[int, str]:
    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    tasks_payload, error = _load_or_fail_tasks(paths)
    if error:
        return 1, f"ERROR: {error}"
    assert tasks_payload is not None
    tasks = tasks_payload.get("tasks")
    assert isinstance(tasks, list)

    if action == "list":
        return 0, json.dumps(tasks_payload, indent=2, sort_keys=True)

    if action == "queue":
        return 0, json.dumps({"tasks": _iter_pending_intake_entries(paths)}, indent=2, sort_keys=True)

    if action == "show":
        if not task_id:
            return 1, "ERROR: --task-id is required for --task show"
        idx = _find_task_index(tasks_payload, task_id)
        if idx < 0:
            return 1, f"ERROR: task not found: {task_id}"
        return 0, json.dumps(tasks[idx], indent=2, sort_keys=True)

    if action == "set":
        if not task_id:
            return 1, "ERROR: --task-id is required for --task set"
        if not task_status:
            return 1, "ERROR: --status is required for --task set"
        status = task_status.strip().lower()
        if status not in TASK_STATUS_VALUES:
            return 1, "ERROR: --status must be one of open|in_progress|blocked|done"
        changed = _set_task_status(tasks_payload=tasks_payload, task_id=task_id, status=status)
        if not changed:
            return 1, f"ERROR: task not found: {task_id}"
        write_json(paths.tasks_json, tasks_payload)
        return 0, f"task_updated={task_id} status={status}"

    if action == "add":
        title_text = _normalize_line(title or "")
        if not title_text:
            return 1, "ERROR: --title is required for --task add"
        normalized_priority = (priority or "p1").strip().lower()
        if normalized_priority not in TASK_PRIORITY_ORDER:
            return 1, "ERROR: --priority must be one of p0|p1|p2|p3"
        state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
        pending_entries = _iter_pending_intake_entries(paths)
        known_ids = _collect_known_task_ids(tasks_payload, pending_entries)

        normalized_depends_on: list[str] = []
        for raw_item in depends_on or []:
            for candidate in str(raw_item).split(","):
                dependency = candidate.strip()
                if not dependency:
                    continue
                if dependency not in known_ids:
                    return 1, f"ERROR: dependency task not found: {dependency}"
                if dependency not in normalized_depends_on:
                    normalized_depends_on.append(dependency)

        anchor_task_id = None if allow_preempt else _resolve_non_interfering_anchor_task_id(state=state, tasks_payload=tasks_payload)
        if anchor_task_id and anchor_task_id not in normalized_depends_on:
            normalized_depends_on.append(anchor_task_id)

        prd_payload = read_json(paths.prd_json)
        if not isinstance(prd_payload, dict):
            prd_payload = _default_prd(state, project=project, project_root=project_root)
            write_json(paths.prd_json, prd_payload)
        objective_id = _as_text(prd_payload.get("objective_id")) or _new_objective_id()
        intake_payload = _load_task_intake_payload(paths)
        queue_tasks = intake_payload.get("tasks")
        if not isinstance(queue_tasks, list):
            queue_tasks = []
            intake_payload["tasks"] = queue_tasks
        new_task_id = _next_task_id(tasks_payload, pending_entries)
        queued_task = {
            "task_id": new_task_id,
            "title": title_text,
            "priority": normalized_priority,
            "depends_on": normalized_depends_on,
            "anchor_current_task": bool(anchor_task_id) and not allow_preempt,
            "anchor_task_id": anchor_task_id,
            "acceptance": [_normalize_line(item)[:220] for item in (acceptance or []) if _normalize_line(item)],
            "validation": [_normalize_line(item)[:220] for item in (validation or []) if _normalize_line(item)],
            "project_root": str(project_root.resolve()),
            "target_branch": _as_text(state.get("target_branch")) or "main",
            "objective_id": objective_id,
            "queued_at": utc_now(),
        }
        queue_tasks.append(queued_task)
        _write_task_intake_payload(paths, intake_payload)
        return 0, json.dumps(
            {
                "queued_task": queued_task,
                "apply_mode": "deferred_until_setup_refresh",
                "will_not_preempt_current_cycle": bool(anchor_task_id) and not allow_preempt,
                "intake_file": str(paths.task_intake_file),
            },
            indent=2,
            sort_keys=True,
        )

    if action == "next":
        task, reason = _select_next_task(tasks_payload)
        if not isinstance(task, dict):
            return 0, json.dumps({"task": None, "reason": reason}, indent=2, sort_keys=True)
        return 0, json.dumps({"task": task, "reason": reason}, indent=2, sort_keys=True)

    if action == "find":
        needle = (query or "").strip().lower()
        if not needle:
            return 1, "ERROR: --query is required for --task find"
        matches: list[dict[str, Any]] = []
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            task_id_value = str(raw.get("task_id", "")).strip()
            title = str(raw.get("title", "")).strip()
            haystack = f"{task_id_value} {title}".lower()
            if needle in haystack:
                matches.append(raw)
        return 0, json.dumps({"matches": matches}, indent=2, sort_keys=True)

    return 1, f"ERROR: unsupported --task action: {action}"


def _handle_objective_command(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
    action: str,
    objective_id: str | None,
) -> tuple[int, str]:
    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
    prd_payload = read_json(paths.prd_json)
    if not isinstance(prd_payload, dict):
        prd_payload = _default_prd(state, project=project, project_root=project_root)
        write_json(paths.prd_json, prd_payload)

    if action == "show":
        return 0, json.dumps(prd_payload, indent=2, sort_keys=True)

    if action == "set":
        if not objective_id:
            return 1, "ERROR: --objective-id is required for --objective set"
        prd_payload["objective_id"] = objective_id.strip()
        prd_payload["updated_at"] = utc_now()
        write_json(paths.prd_json, prd_payload)
        update_state(paths.state_file, state, objective_id=objective_id.strip())
        return 0, f"objective_updated={objective_id.strip()}"

    return 1, f"ERROR: unsupported --objective action: {action}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse runctl options."""
    parser = argparse.ArgumentParser(description="Deterministic control for Codex infinite runners")
    action = parser.add_mutually_exclusive_group(required=False)
    action.add_argument("--setup", action="store_true", help="Create/refresh runner memory state")
    action.add_argument("--clear", action="store_true", help="Clear runner memory state in two phases")
    action.add_argument(
        "--prepare-cycle",
        action="store_true",
        help="Write canonical RUNNER_CYCLE_PREPARED.json for deterministic phase handoff",
    )
    parser.add_argument("--task", choices=["list", "show", "set", "next", "find", "add", "queue"], help="Task command")
    parser.add_argument("--task-id", help="Task id for show/set operations")
    parser.add_argument("--title", help="Task title for --task add")
    parser.add_argument("--status", help="Task status for --task set")
    parser.add_argument("--priority", help="Task priority for --task add (p0|p1|p2|p3)")
    parser.add_argument("--depends-on", action="append", help="Dependency task id(s) for --task add (repeat or comma separate)")
    parser.add_argument("--acceptance", action="append", help="Acceptance line for --task add (repeatable)")
    parser.add_argument("--validation", action="append", help="Validation line for --task add (repeatable)")
    parser.add_argument("--allow-preempt", action="store_true", help="Allow --task add to omit automatic current-task anchoring")
    parser.add_argument("--query", help="Search query for --task find")
    parser.add_argument("--objective", choices=["show", "set"], help="Objective command")
    parser.add_argument("--objective-id", help="Objective id for --objective set")
    parser.add_argument("--project", help="Project under $DEV/Repos")
    parser.add_argument("project_arg", nargs="?", help="Project under $DEV/Repos (positional shorthand)")
    parser.add_argument(
        "--project-root",
        help="Explicit project root path (supports non-Repos worktrees)",
    )
    parser.add_argument("--runner-id", help="Runner id")
    parser.add_argument("--approve-enable", help="HIL token to approve --setup enablement")
    parser.add_argument("--confirm", help="HIL token to confirm --clear deletion")
    parser.add_argument("--dev", default=_default_dev(), help="Dev root (default: ~/Dev)")
    parser.add_argument("--quiet", action="store_true", help="Minimize CLI output for automation flows")
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    """CLI entrypoint for runctl."""
    args = parse_args(argv)

    def _out(message: str) -> None:
        print(message)

    def _info(message: str) -> None:
        if not args.quiet:
            print(message)

    def _err(message: str) -> None:
        print(message)

    if args.project and args.project_arg:
        _err("ERROR: provide either --project or positional project, not both")
        return 1

    if args.project_root and (args.project or args.project_arg):
        _err("ERROR: provide either --project/positional project or --project-root, not both")
        return 1

    if args.task and (args.setup or args.clear or args.prepare_cycle or args.approve_enable or args.confirm):
        _err("ERROR: --task cannot be combined with --setup/--clear/--prepare-cycle/--approve-enable/--confirm")
        return 1
    if args.objective and (args.setup or args.clear or args.prepare_cycle or args.approve_enable or args.confirm):
        _err("ERROR: --objective cannot be combined with --setup/--clear/--prepare-cycle/--approve-enable/--confirm")
        return 1
    if args.task and args.objective:
        _err("ERROR: choose either --task or --objective, not both")
        return 1

    runner_id = _default_runner_id(args.runner_id)
    if runner_id not in {"main", "default"}:
        _err("ERROR: single-runner mode only supports --runner-id main (or omit it)")
        return 1
    runner_id = "main"

    explicit_project = args.project or args.project_arg
    project, project_root = _resolve_project_context(
        dev=args.dev,
        explicit=explicit_project,
        project_root_override=args.project_root,
    )

    # When project name shorthand is used, reuse saved git_worktree context if available.
    if args.project_root is None and explicit_project and not _is_path_like_explicit(explicit_project):
        project_root = resolve_target_project_root(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
        )

    if not project_root.exists() or not project_root.is_dir():
        _err(f"ERROR: project root not found: {project_root}")
        return 1

    if args.task:
        code, output = _handle_task_command(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
            action=args.task,
            task_id=args.task_id,
            task_status=args.status,
            query=args.query,
            title=args.title,
            priority=args.priority,
            depends_on=args.depends_on,
            acceptance=args.acceptance,
            validation=args.validation,
            allow_preempt=bool(args.allow_preempt),
        )
        print(output)
        return code

    if args.objective:
        code, output = _handle_objective_command(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
            action=args.objective,
            objective_id=args.objective_id,
        )
        print(output)
        return code

    if args.prepare_cycle:
        paths = build_runner_state_paths_for_root(
            project_root=project_root,
            dev=args.dev,
            project=project,
            runner_id=runner_id,
        )
        ensure_memory_dir(paths)
        state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
        exec_context = read_json(paths.exec_context_json) or {}
        baseline = None
        if isinstance(exec_context, dict):
            baseline = exec_context.get("cycle_progress_baseline")
            if not isinstance(baseline, dict):
                baseline = exec_context.get("progress_baseline")
        current_snapshot = _capture_progress_snapshot(state=state, project_root=project_root)
        if not _progress_has_advanced(baseline=baseline if isinstance(baseline, dict) else None, current=current_snapshot):
            _err("ERROR: refusing to prepare cycle marker because no durable progress was detected since RUNNER_EXEC_CONTEXT.json")
            return 1
        marker = _write_prepared_cycle_marker(
            paths=paths,
            state=state,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
        )
        if isinstance(exec_context, dict):
            exec_context["progress_baseline"] = current_snapshot
            exec_context["cycle_progress_baseline"] = current_snapshot
            write_json(paths.exec_context_json, exec_context)
        if args.quiet:
            _out("ok=1")
        else:
            _info(f"project={project} runner_id={runner_id}")
            _info(f"project_root={project_root}")
            _info(f"prepared_marker={marker}")
            _info(f"phase={str(state.get('current_phase', '')).strip() or '(none)'}")
            _info(f"next_task_id={str(state.get('next_task_id', '')).strip() or '(none)'}")
        return 0

    if args.clear:
        result = clear_runner_state(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
            confirm=args.confirm,
            project_root=project_root,
        )
    else:
        # Default behavior when no explicit action is provided: setup runner state.
        result = create_runner_state(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
            approve_enable=args.approve_enable,
            project_root=project_root,
        )

    if not result.get("ok"):
        _err(f"ERROR: {result.get('error', 'unknown error')}")
        return 1

    if not args.clear:
        if args.quiet:
            if result.get("enable_pending_file"):
                _out(f"ok=1 approve_enable_token={result['enable_token']}")
            else:
                _out("ok=1")
            return 0
        else:
            state_data = read_json(Path(result["state_file"])) or {}
            _info(f"project={result['project']} runner_id={result['runner_id']}")
            _info(f"project_root={project_root}")
            _info(f"state={result['state_file']}")
            _info(f"ledger={result['ledger_file']}")
            if result.get("enable_pending_file"):
                _info(f"enable_pending={result['enable_pending_file']}")
                _info(f"approve_enable_token={result['enable_token']}")
                _info("next: rerun with --setup --approve-enable <token>")
            else:
                _info("enablement=approved")
            if result.get("created"):
                _info(f"created={','.join(result['created'])}")
            else:
                _info("created=")
            _info(f"plan_snapshot={str(state_data.get('current_goal', '')).strip() or '(none)'}")
            _info(f"phase={str(state_data.get('current_phase', '')).strip() or '(none)'}")
            _info(f"objective_id={str(state_data.get('objective_id', '')).strip() or '(none)'}")
            _info(f"next_task_id={str(state_data.get('next_task_id', '')).strip() or '(none)'}")
            _info(f"next_task={str(state_data.get('next_task', '')).strip() or '(none)'}")
            _info(f"next_task_reason={str(state_data.get('next_task_reason', '')).strip() or '(none)'}")
        return 0

    if result.get("phase") == "pending":
        if args.quiet:
            _out(f"ok=1 confirm_token={result['confirm_token']}")
        else:
            _info(f"project={result['project']} runner_id={result['runner_id']}")
            _info(f"project_root={project_root}")
            _info(f"clear_pending={result['clear_pending_file']}")
            _info(f"confirm_token={result['confirm_token']}")
            _info(f"manifest_count={result['manifest_count']}")
            _info("next: rerun with --clear --confirm <token>")
        return 0

    if args.quiet:
        _out("ok=1")
    else:
        _info(f"project={result['project']} runner_id={result['runner_id']}")
        _info(f"project_root={project_root}")
        _info(f"deleted={result['deleted']}")
        _info("clear=complete")
    return 0


def main() -> None:
    raise SystemExit(run(__import__("sys").argv[1:]))


if __name__ == "__main__":
    main()
