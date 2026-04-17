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

from .runner_graph import (
    build_runner_graph_artifacts,
    load_graph_config_for_project,
    summarize_task_graph_slice,
)
from .runner_state import (
    DONE_NEXT_TASK_TEXT,
    DEFAULT_PHASE_BUDGET_MINUTES,
    append_ndjson,
    build_runner_state_paths_for_root,
    coerce_runner_phase,
    compute_worktree_fingerprint,
    count_open_seams,
    count_open_tasks,
    detect_git_context,
    ensure_memory_dir,
    load_or_init_kanban_state,
    load_or_init_state,
    load_state_snapshot,
    managed_runner_files,
    read_json,
    worktrees_home,
    update_state,
    utc_now,
    write_runner_status_snapshot,
    write_json,
)

TASK_STATUS_VALUES = {"open", "in_progress", "blocked", "done"}
TASK_PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
TASK_MODEL_PROFILES = {"mini", "high"}
TASK_FANOUT_RISKS = {"low", "medium", "high"}
TASK_DEPRECATION_PHASES = {"seam", "shim", "consumer_migration", "convergence", "removal"}
SETUP_EVENT_COALESCE_SECONDS = 90
RUNNER_PROMPT_NAMES = ("run_setup", "run_execute", "run_govern", "add")


def _default_dev() -> str:
    return str(Path.home() / "Dev")


def _repo_home() -> Path:
    return Path(__file__).resolve().parents[1]


def _prompt_source_dir() -> Path:
    return _repo_home() / "prompts"


def _codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".codex"


def _prompt_install_dir() -> Path:
    return _codex_home() / "commands"


def _validate_runner_prompt_install() -> str | None:
    source_dir = _prompt_source_dir()
    install_dir = _prompt_install_dir()
    install_cmd = f"bash {(_repo_home() / 'scripts' / 'install-codex-run-prompt.sh')}"

    for prompt_name in RUNNER_PROMPT_NAMES:
        source = source_dir / f"{prompt_name}.md"
        installed = install_dir / f"{prompt_name}.md"
        if not source.exists():
            return f"Canonical runner command missing: {source}"
        if not installed.exists():
            return (
                f"Installed runner command missing: {installed}. "
                f"Reinstall runner commands with `{install_cmd}`."
            )
        if not installed.is_symlink():
            return (
                f"Installed runner command is not linked to canonical source: {installed}. "
                f"Reinstall runner commands with `{install_cmd}`."
            )
        try:
            resolved = installed.resolve(strict=True)
        except OSError:
            return (
                f"Installed runner command link is broken: {installed}. "
                f"Reinstall runner commands with `{install_cmd}`."
            )
        if resolved != source.resolve():
            return (
                f"Installed runner command points at the wrong source: {installed} -> {resolved}. "
                f"Reinstall runner commands with `{install_cmd}`."
            )
    return None


def _repair_runner_prompt_install() -> str | None:
    script = _repo_home() / "scripts" / "install-codex-run-prompt.sh"
    install_cmd = f"bash {script}"
    try:
        completed = subprocess.run(
            ["bash", str(script)],
            cwd=str(_repo_home()),
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return f"Runner command auto-install failed while running `{install_cmd}`: {exc}"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            return f"Runner command auto-install failed while running `{install_cmd}`: {detail}"
        return f"Runner command auto-install failed while running `{install_cmd}` with exit code {exc.returncode}."

    validation_error = _validate_runner_prompt_install()
    if validation_error:
        output = (completed.stdout or "").strip()
        if output:
            return f"{validation_error} Auto-install output: {output}"
        return validation_error
    return None


def _ensure_runner_prompt_install() -> str | None:
    validation_error = _validate_runner_prompt_install()
    if validation_error is None:
        return None
    if validation_error.startswith("Canonical runner command missing:"):
        return validation_error
    return _repair_runner_prompt_install()


def ensure_runner_prompt_install() -> str | None:
    """Validate installed runner commands and auto-repair symlink drift when possible."""
    return _ensure_runner_prompt_install()


def _read_lock_metadata(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key_text = key.strip()
        value_text = value.strip()
        if key_text and value_text:
            metadata[key_text] = value_text
    return metadata


def _normalize_project_root_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.name == ".memory":
        return resolved.parent
    for parent in resolved.parents:
        if parent.name == ".memory":
            return parent.parent
    return resolved


def _resolve_project_context(
    dev: str,
    explicit: str | None,
    project_root_override: str | None = None,
) -> tuple[str, Path]:
    if project_root_override:
        project_root = Path(project_root_override).expanduser().resolve()
        if explicit and not _is_path_like_explicit(explicit):
            return explicit, project_root
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
    _add(memory_dir / "runner" / "KANBAN_STATE.json", kind="runner_kanban")
    _add(memory_dir / "runner" / "RUNNER_PARITY.json", kind="runner_parity")
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
    kanban_state: dict[str, Any] | None = None,
) -> None:
    objective_title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or _as_text(state.get("current_goal"))
    next_task_id = _as_text(state.get("active_seam_id")) or _as_text(state.get("next_task_id"))
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
    model_profile = _normalize_choice(selected_task.get("model_profile"), TASK_MODEL_PROFILES) if isinstance(selected_task, dict) else None
    profile_reason = _normalize_line(str(selected_task.get("profile_reason", ""))) if isinstance(selected_task, dict) else ""
    fanout_risk = _normalize_choice(selected_task.get("fanout_risk"), TASK_FANOUT_RISKS) if isinstance(selected_task, dict) else None
    deprecation_phase = (
        _normalize_choice(selected_task.get("deprecation_phase"), TASK_DEPRECATION_PHASES)
        if isinstance(selected_task, dict)
        else None
    )
    touch_paths = _normalize_text_list(selected_task.get("touch_paths"), item_chars=220, max_items=8) if isinstance(selected_task, dict) else []
    validation_commands = (
        _normalize_text_list(selected_task.get("validation_commands"), item_chars=220, max_items=3)
        if isinstance(selected_task, dict)
        else []
    )
    coupling_notes = (
        _normalize_text_list(selected_task.get("coupling_notes"), item_chars=220, max_items=4)
        if isinstance(selected_task, dict)
        else []
    )
    completed_recent = [str(item).strip() for item in state.get("completed_recent", []) if str(item).strip()]
    blockers = [str(item).strip() for item in state.get("blockers", []) if str(item).strip()]
    open_items = _extract_open_tasks_from_payload(tasks_payload, max_items=8)
    selected_title = _as_text(selected_task.get("title")) if isinstance(selected_task, dict) else next_task
    kanban_state = kanban_state if isinstance(kanban_state, dict) else {}
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
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
    if active_issue:
        lines.extend(
            [
                f"- Linear issue: {' '.join(part for part in (_as_text(active_issue.get('identifier')), _as_text(active_issue.get('title'))) if part).strip() or '(none)'}",
                f"- Linear link: {_as_text(active_issue.get('external_url')) or _as_text(active_issue.get('url')) or '(none)'}",
                f"- Project key: {_as_text(active_issue.get('project_key')) or _as_text(active_issue.get('repo')) or '(none)'}",
                f"- Worktree: {_as_text(active_issue.get('worktree')) or '(none)'}",
                f"- Branch: {_as_text(active_issue.get('branch')) or '(none)'}",
            ]
        )
    if model_profile:
        lines.append(f"- Model profile: {model_profile}")
    if profile_reason:
        lines.append(f"- Profile reason: {profile_reason}")
    if fanout_risk:
        lines.append(f"- Fanout risk: {fanout_risk}")
    if deprecation_phase:
        lines.append(f"- Deprecation phase: {deprecation_phase}")
    if _as_text(state.get("last_iteration_summary")):
        lines.extend(["", "## Last Session Summary", "", _as_text(state.get("last_iteration_summary"))])
    if touch_paths:
        lines.extend(["", "## Touch Paths", ""])
        lines.extend([f"- {item}" for item in touch_paths])
    if validation_commands:
        lines.extend(["", "## Validation Commands", ""])
        lines.extend([f"- {item}" for item in validation_commands])
    if coupling_notes:
        lines.extend(["", "## Coupling Notes", ""])
        lines.extend([f"- {item}" for item in coupling_notes])
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


def _build_active_backlog_entries(
    *,
    tasks_payload: dict[str, Any],
    selected_task_id: str,
    max_items: int = 4,
) -> list[dict[str, Any]]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = _as_text(raw.get("status")).lower()
        if status not in {"open", "in_progress", "blocked"}:
            continue
        items.append(
            {
                "task_id": _as_text(raw.get("task_id")) or None,
                "title": _normalize_line(_as_text(raw.get("title")))[:140] or None,
                "status": status or None,
                "blocked_reason": _normalize_line(_as_text(raw.get("blocked_reason")))[:140] or None,
                "depends_on": [str(item).strip() for item in raw.get("depends_on", []) if str(item).strip()][:3],
                "model_profile": _normalize_choice(raw.get("model_profile"), TASK_MODEL_PROFILES),
            }
        )
    items.sort(
        key=lambda item: (
            0 if _as_text(item.get("task_id")) == selected_task_id else 1,
            {"open": 0, "in_progress": 1, "blocked": 2}.get(_as_text(item.get("status")).lower(), 9),
            _as_text(item.get("task_id")),
        )
    )
    return items[:max_items]


def _write_active_backlog_view(
    *,
    paths,
    state: dict[str, Any],
    prd_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    selected_task: dict[str, Any] | None,
    phase_goal: str,
    graph_summary: dict[str, Any] | None = None,
    parity_scope: dict[str, Any] | None = None,
    kanban_state: dict[str, Any] | None = None,
) -> None:
    selected_task_id = _as_text(selected_task.get("task_id")) if isinstance(selected_task, dict) else ""
    selected_title = _as_text(selected_task.get("title")) if isinstance(selected_task, dict) else ""
    graph_summary = graph_summary if isinstance(graph_summary, dict) else {}
    parity_scope = parity_scope if isinstance(parity_scope, dict) else {}
    kanban_state = kanban_state if isinstance(kanban_state, dict) else {}
    runtime_policy = state.get("runtime_policy") if isinstance(state.get("runtime_policy"), dict) else {}
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    loop_state = kanban_state.get("loop") if isinstance(kanban_state.get("loop"), dict) else {}
    payload = {
        "objective_id": _as_text(state.get("objective_id")) or None,
        "active_seam_id": _as_text(state.get("active_seam_id")) or selected_task_id or None,
        "next_seam_id": _as_text(state.get("next_seam_id") or state.get("next_task_id")) or None,
        "next_seam_reason": _normalize_line(_as_text(state.get("next_seam_reason") or state.get("next_task_reason")))[:180] or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task_reason": _normalize_line(_as_text(state.get("next_task_reason")))[:180] or None,
        "blockers_top2": [
            _normalize_line(str(item).strip())[:180]
            for item in state.get("blockers", [])
            if str(item).strip()
        ][:2],
        "selected_task": {
            "task_id": selected_task_id or None,
            "model_profile": (
                _normalize_choice(selected_task.get("model_profile"), TASK_MODEL_PROFILES)
                if isinstance(selected_task, dict)
                else None
            ),
            "fanout_risk": (
                _normalize_choice(selected_task.get("fanout_risk"), TASK_FANOUT_RISKS)
                if isinstance(selected_task, dict)
                else None
            ),
            "acceptance_top3": (
                _normalize_text_list(selected_task.get("acceptance"), item_chars=160, max_items=1)
                if isinstance(selected_task, dict)
                else []
            ),
            "validation_top3": (
                _normalize_text_list(selected_task.get("validation"), item_chars=160, max_items=1)
                if isinstance(selected_task, dict)
                else []
            ),
            "graph_cluster_label": _as_text(graph_summary.get("graph_cluster_label")) or None,
            "graph_slice_reason": _as_text(graph_summary.get("graph_slice_reason")) or None,
            "graph_adjacent_families_top3": [
                str(item).strip()
                for item in graph_summary.get("graph_adjacent_families_top3", [])
                if str(item).strip()
            ][:3],
            "graph_boundary_warnings_top2": [
                str(item).strip()
                for item in graph_summary.get("graph_boundary_warnings_top3", [])
                if str(item).strip()
            ][:2],
            "parity_enabled": bool(parity_scope.get("parity_enabled", False)),
            "parity_baseline_ref": _as_text(parity_scope.get("parity_baseline_ref")) or None,
            "parity_surface_ids_top3": [
                str(item).strip()
                for item in parity_scope.get("parity_surface_ids", [])
                if str(item).strip()
            ][:3],
            "parity_audit_mode": _as_text(parity_scope.get("parity_audit_mode")) or None,
        },
        "runtime_policy": {
            "runner_mode": _as_text(runtime_policy.get("runner_mode")) or None,
            "task_source": _as_text(runtime_policy.get("task_source")) or None,
            "kanban_enabled": bool(runtime_policy.get("kanban_enabled", False)),
            "completion_policy": _as_text(runtime_policy.get("completion_policy")) or None,
        },
        "kanban": {
            "mode": _as_text(kanban_state.get("mode")) or None,
            "phase": _as_text(kanban_state.get("phase")) or None,
            "active_issue_identifier": _as_text(active_issue.get("identifier")) or None,
            "active_issue_title": _as_text(active_issue.get("title")) or None,
            "active_issue_url": _as_text(active_issue.get("url")) or None,
            "active_issue_external_url": _as_text(active_issue.get("external_url")) or None,
            "active_issue_repo": _as_text(active_issue.get("repo")) or None,
            "active_issue_project_key": _as_text(active_issue.get("project_key")) or None,
            "active_issue_project_name": _as_text(active_issue.get("project_name")) or None,
            "active_issue_branch": _as_text(active_issue.get("branch")) or None,
            "active_issue_worktree": _as_text(active_issue.get("worktree")) or None,
            "continue_until": _as_text(loop_state.get("continue_until")) or None,
        },
        "active_backlog": _build_active_backlog_entries(
            tasks_payload=tasks_payload,
            selected_task_id=selected_task_id,
        ),
        "generated_at": utc_now(),
    }
    write_json(paths.active_backlog_json, payload)


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


def _active_issue_seed_context(kanban_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(kanban_state, dict):
        return None
    active_issue = kanban_state.get("active_issue")
    if not isinstance(active_issue, dict):
        return None
    identifier = _as_text(active_issue.get("identifier"))
    title = _normalize_objective_title(_as_text(active_issue.get("title")))
    if not identifier and not title:
        return None
    issue_label = " ".join(part for part in (identifier, title) if part).strip()
    description_lines = _compact_lines(_as_text(active_issue.get("description")), limit=3, line_chars=180)
    project_key = _as_text(active_issue.get("project_key")) or _as_text(active_issue.get("repo"))
    worktree = _as_text(active_issue.get("worktree"))
    branch = _as_text(active_issue.get("branch"))
    constraints = []
    if project_key:
        constraints.append(f"Linear project context must remain in `{project_key}`.")
    if worktree:
        constraints.append(f"Execute in worktree `{worktree}`.")
    if branch:
        constraints.append(f"Stay on branch `{branch}`.")
    scope_in = [f"Execute Linear issue {issue_label or identifier or title}."]
    scope_in.extend(description_lines[:2])
    success_criteria = [f"Advance {issue_label or identifier or title} with code and validation evidence."]
    if description_lines:
        success_criteria.append(f"Honor the current ticket context: {description_lines[0]}")
    return {
        "objective_title": issue_label or title or identifier,
        "scope_in": scope_in,
        "success_criteria": success_criteria,
        "constraints": constraints,
    }


def _task_priority_rank(priority: str) -> int:
    return TASK_PRIORITY_ORDER.get(priority.strip().lower(), 999)


def _normalize_choice(raw: Any, allowed: set[str]) -> str | None:
    value = _as_text(raw).lower()
    if value in allowed:
        return value
    return None


def _normalize_text_list(raw: Any, *, item_chars: int = 220, max_items: int = 16) -> list[str]:
    if not isinstance(raw, list):
        return []
    items: list[str] = []
    for item in raw:
        candidate = re.sub(r"\s+", " ", str(item)).strip(" -\t").strip("`")
        if not candidate:
            continue
        items.append(candidate[:item_chars])
        if len(items) >= max_items:
            break
    return items


def _normalize_parity_surface_ids(raw: Any, *, max_items: int = 8) -> list[str]:
    return _normalize_text_list(raw, item_chars=80, max_items=max_items)


def _normalize_parity_harness_commands(raw: Any, *, max_items: int = 4) -> list[str]:
    return _normalize_text_list(raw, item_chars=220, max_items=max_items)


def _coerce_int(raw: Any, *, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _normalize_runner_parity_policy(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    audit_mode = _as_text(source.get("audit_mode")).lower()
    if audit_mode not in {"targeted", "diff_only", "skip"}:
        audit_mode = "targeted"
    return {
        "fail_closed": bool(source.get("fail_closed", True)),
        "audit_mode": audit_mode,
        "default_max_surface_checks_mini": max(
            0,
            min(4, _coerce_int(source.get("default_max_surface_checks_mini", 1) or 1, default=1)),
        ),
        "default_max_surface_checks_high": max(
            0,
            min(6, _coerce_int(source.get("default_max_surface_checks_high", 2) or 2, default=2)),
        ),
        "diff_first": bool(source.get("diff_first", True)),
        "mcp_when": _normalize_text_list(source.get("mcp_when"), item_chars=140, max_items=6),
    }


def _normalize_runner_parity_surface(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    surface_id = _normalize_line(_as_text(raw.get("surface_id")))[:80]
    if not surface_id:
        return None
    runtime = _as_text(raw.get("runtime")).lower()
    if runtime not in {"desktop", "web", "shared"}:
        runtime = "shared"
    kind = _normalize_line(_as_text(raw.get("kind")))[:80] or "ui_surface"
    preferred_mcp = _normalize_text_list(raw.get("preferred_mcp"), item_chars=60, max_items=3)
    normalized = {
        "surface_id": surface_id,
        "runtime": runtime,
        "kind": kind,
        "owner_paths": _normalize_text_list(raw.get("owner_paths"), item_chars=220, max_items=12),
        "watch_paths": _normalize_text_list(raw.get("watch_paths"), item_chars=220, max_items=12),
        "preferred_mcp": preferred_mcp,
        "baseline_expectation": _normalize_line(_as_text(raw.get("baseline_expectation")))[:220] or None,
        "harness_commands": _normalize_parity_harness_commands(raw.get("harness_commands"), max_items=4),
    }
    return normalized


def _normalize_runner_parity_payload(
    raw: Any,
    *,
    objective_id: str,
    baseline_ref: str | None = None,
    baseline_git_head: str | None = None,
) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    normalized = {
        "objective_id": _as_text(payload.get("objective_id")) or objective_id,
        "baseline_ref": _as_text(payload.get("baseline_ref")) or baseline_ref or None,
        "baseline_label": _normalize_line(_as_text(payload.get("baseline_label")))[:140] or None,
        "baseline_git_head": _as_text(payload.get("baseline_git_head")) or baseline_git_head or None,
        "policy": _normalize_runner_parity_policy(payload.get("policy")),
        "surfaces": [],
        "updated_at": _as_text(payload.get("updated_at")) or utc_now(),
    }
    seen_surface_ids: set[str] = set()
    for item in payload.get("surfaces", []) if isinstance(payload.get("surfaces"), list) else []:
        surface = _normalize_runner_parity_surface(item)
        if surface is None:
            continue
        surface_id = _as_text(surface.get("surface_id"))
        if surface_id in seen_surface_ids:
            continue
        seen_surface_ids.add(surface_id)
        normalized["surfaces"].append(surface)
    return normalized


def _task_has_parity_metadata(task: dict[str, Any]) -> bool:
    return any(
        (
            _as_text(task.get("parity_baseline_ref")),
            bool(_normalize_parity_surface_ids(task.get("parity_surface_ids"))),
            bool(_normalize_parity_harness_commands(task.get("parity_harness_commands"))),
            _as_text(task.get("parity_notes")),
        )
    )


def _build_default_runner_parity_payload(
    *,
    prd_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    git_context = detect_git_context(project_root)
    objective_id = _as_text(prd_payload.get("objective_id")) or _new_objective_id()
    baseline_ref: str | None = None
    surfaces: list[dict[str, Any]] = []
    seen_surface_ids: set[str] = set()
    tasks = tasks_payload.get("tasks")
    if isinstance(tasks, list):
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            if baseline_ref is None:
                candidate_baseline = _normalize_line(_as_text(raw.get("parity_baseline_ref")))[:80]
                if candidate_baseline:
                    baseline_ref = candidate_baseline
            for surface_id in _normalize_parity_surface_ids(raw.get("parity_surface_ids")):
                if surface_id in seen_surface_ids:
                    continue
                seen_surface_ids.add(surface_id)
                surfaces.append(
                    {
                        "surface_id": surface_id,
                        "runtime": "shared",
                        "kind": "ui_surface",
                        "owner_paths": [],
                        "watch_paths": _normalize_text_list(raw.get("touch_paths"), item_chars=220, max_items=8),
                        "preferred_mcp": [],
                        "baseline_expectation": None,
                        "harness_commands": _normalize_parity_harness_commands(raw.get("parity_harness_commands")),
                    }
                )
    return {
        "objective_id": objective_id,
        "baseline_ref": baseline_ref,
        "baseline_label": None,
        "baseline_git_head": _as_text(git_context.get("git_head")) or None,
        "policy": _normalize_runner_parity_policy({}),
        "surfaces": surfaces,
        "updated_at": utc_now(),
    }


def _ensure_runner_parity_payload(
    *,
    paths,
    prd_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    project_root: Path,
) -> tuple[dict[str, Any], bool]:
    existing = read_json(paths.runner_parity_json)
    baseline_head = _as_text(detect_git_context(project_root).get("git_head")) or None
    if existing is None:
        normalized = _build_default_runner_parity_payload(
            prd_payload=prd_payload,
            tasks_payload=tasks_payload,
            project_root=project_root,
        )
    else:
        normalized = _normalize_runner_parity_payload(
            existing,
            objective_id=_as_text(prd_payload.get("objective_id")) or _new_objective_id(),
            baseline_git_head=baseline_head,
        )
    known_surface_ids = {
        _as_text(item.get("surface_id"))
        for item in normalized.get("surfaces", [])
        if isinstance(item, dict) and _as_text(item.get("surface_id"))
    }
    tasks = tasks_payload.get("tasks")
    if isinstance(tasks, list):
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            if not _task_has_parity_metadata(raw):
                continue
            if not _as_text(normalized.get("baseline_ref")):
                candidate_baseline = _normalize_line(_as_text(raw.get("parity_baseline_ref")))[:80]
                if candidate_baseline:
                    normalized["baseline_ref"] = candidate_baseline
            for surface_id in _normalize_parity_surface_ids(raw.get("parity_surface_ids")):
                if surface_id in known_surface_ids:
                    continue
                known_surface_ids.add(surface_id)
                normalized["surfaces"].append(
                    {
                        "surface_id": surface_id,
                        "runtime": "shared",
                        "kind": "ui_surface",
                        "owner_paths": [],
                        "watch_paths": _normalize_text_list(raw.get("touch_paths"), item_chars=220, max_items=8),
                        "preferred_mcp": [],
                        "baseline_expectation": None,
                        "harness_commands": _normalize_parity_harness_commands(raw.get("parity_harness_commands")),
                    }
                )
    changed = existing != normalized
    if changed:
        write_json(paths.runner_parity_json, normalized)
    return normalized, changed


def _resolve_task_parity_scope(
    *,
    task: dict[str, Any] | None,
    parity_payload: dict[str, Any] | None,
    title: str,
    acceptance: list[str],
    validation: list[str],
) -> dict[str, Any]:
    policy = _normalize_runner_parity_policy((parity_payload or {}).get("policy"))
    task_dict = task if isinstance(task, dict) else {}
    parity_surface_ids = _normalize_parity_surface_ids(task_dict.get("parity_surface_ids"))
    surfaces_by_id = {
        _as_text(item.get("surface_id")): item
        for item in ((parity_payload or {}).get("surfaces") if isinstance((parity_payload or {}).get("surfaces"), list) else [])
        if isinstance(item, dict) and _as_text(item.get("surface_id"))
    }
    parity_surfaces = [surfaces_by_id[surface_id] for surface_id in parity_surface_ids if surface_id in surfaces_by_id]
    task_audit_mode = _as_text(task_dict.get("parity_audit_mode")).lower()
    if task_audit_mode not in {"targeted", "diff_only", "skip"}:
        task_audit_mode = _as_text(policy.get("audit_mode")).lower() or "targeted"
    parity_baseline_ref = (
        _as_text(task_dict.get("parity_baseline_ref"))
        or _as_text((parity_payload or {}).get("baseline_ref"))
        or None
    )
    parity_harness_commands = _normalize_parity_harness_commands(task_dict.get("parity_harness_commands"))
    if not parity_harness_commands:
        for surface in parity_surfaces:
            parity_harness_commands.extend(
                command
                for command in _normalize_parity_harness_commands(surface.get("harness_commands"))
                if command not in parity_harness_commands
            )
            if len(parity_harness_commands) >= 4:
                break
    parity_fail_closed = bool(policy.get("fail_closed", True)) and (
        bool(parity_surface_ids)
        or bool(parity_baseline_ref)
        or _looks_like_strict_parity_task(title=title, acceptance=acceptance, validation=validation)
    )
    trigger_reason_parts: list[str] = []
    if parity_surface_ids:
        trigger_reason_parts.append("task declares parity surfaces")
    if parity_baseline_ref:
        trigger_reason_parts.append(f"baseline={parity_baseline_ref}")
    if not trigger_reason_parts and parity_fail_closed:
        trigger_reason_parts.append("task acceptance/validation reads like parity work")
    return {
        "parity_enabled": parity_fail_closed,
        "parity_baseline_ref": parity_baseline_ref,
        "parity_surface_ids": parity_surface_ids,
        "parity_surfaces": parity_surfaces,
        "parity_audit_mode": task_audit_mode,
        "parity_harness_commands": parity_harness_commands[:4],
        "parity_fail_closed": parity_fail_closed,
        "parity_trigger_reason": "; ".join(trigger_reason_parts)[:220] or None,
        "parity_notes": _normalize_line(_as_text(task_dict.get("parity_notes")))[:220] or None,
        "parity_policy": policy,
    }


def _task_marked_superseded(task: dict[str, Any]) -> bool:
    title = _as_text(task.get("title")).lower()
    acceptance = " ".join(str(item).strip().lower() for item in task.get("acceptance", []) if str(item).strip())
    validation = " ".join(str(item).strip().lower() for item in task.get("validation", []) if str(item).strip())
    combined = " ".join((title, acceptance, validation))
    return "superseded" in combined


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
        "model_profile": _normalize_choice(raw.get("model_profile"), TASK_MODEL_PROFILES) or "high",
        "fanout_risk": _normalize_choice(raw.get("fanout_risk"), TASK_FANOUT_RISKS) or "high",
    }
    blocked_reason = _normalize_line(str(raw.get("blocked_reason", "")))
    if blocked_reason:
        normalized["blocked_reason"] = blocked_reason[:220]
    profile_reason = _normalize_line(str(raw.get("profile_reason", "")))
    if profile_reason:
        normalized["profile_reason"] = profile_reason[:220]
    deprecation_phase = _normalize_choice(raw.get("deprecation_phase"), TASK_DEPRECATION_PHASES)
    if deprecation_phase:
        normalized["deprecation_phase"] = deprecation_phase
    touch_paths = _normalize_text_list(raw.get("touch_paths"), item_chars=220, max_items=24)
    if touch_paths:
        normalized["touch_paths"] = touch_paths
    validation_commands = _normalize_text_list(raw.get("validation_commands"), item_chars=220, max_items=8)
    if validation_commands:
        normalized["validation_commands"] = validation_commands
    spillover_paths = _normalize_text_list(raw.get("spillover_paths"), item_chars=220, max_items=16)
    if spillover_paths:
        normalized["spillover_paths"] = spillover_paths
    coupling_notes = _normalize_text_list(raw.get("coupling_notes"), item_chars=220, max_items=8)
    if coupling_notes:
        normalized["coupling_notes"] = coupling_notes
    parity_baseline_ref = _normalize_line(_as_text(raw.get("parity_baseline_ref")))[:80]
    if parity_baseline_ref:
        normalized["parity_baseline_ref"] = parity_baseline_ref
    parity_surface_ids = _normalize_parity_surface_ids(raw.get("parity_surface_ids"))
    if parity_surface_ids:
        normalized["parity_surface_ids"] = parity_surface_ids
    parity_audit_mode = _as_text(raw.get("parity_audit_mode")).lower()
    if parity_audit_mode in {"targeted", "diff_only", "skip"}:
        normalized["parity_audit_mode"] = parity_audit_mode
    parity_harness_commands = _normalize_parity_harness_commands(raw.get("parity_harness_commands"))
    if parity_harness_commands:
        normalized["parity_harness_commands"] = parity_harness_commands
    parity_notes = _normalize_line(_as_text(raw.get("parity_notes")))[:220]
    if parity_notes:
        normalized["parity_notes"] = parity_notes
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
    issue_context = _active_issue_seed_context(read_json(project_root / ".memory" / "runner" / "KANBAN_STATE.json"))
    title = issue_context["objective_title"] if issue_context else (
        _normalize_objective_title(_as_text(state.get("current_goal"))) or f"{project} runner objective"
    )
    objective_id = _as_text(state.get("objective_id")) or _new_objective_id()
    scope_in = issue_context["scope_in"] if issue_context else [f"Seed concrete TT-001.. tasks from the active objective: {title}"]
    success_criteria = issue_context["success_criteria"] if issue_context else [f"TT-001 names the first concrete slice for {title}"]
    constraints = ["Single runner_id=main", "Worktree/root scope must remain fixed"]
    if issue_context:
        constraints.extend(issue_context["constraints"])
    return {
        "objective_id": objective_id,
        "title": title,
        "scope_in": scope_in,
        "scope_out": ["Unscoped feature work not represented in TASKS.json"],
        "success_criteria": success_criteria,
        "constraints": constraints,
        "project_root": str(project_root.resolve()),
        "updated_at": utc_now(),
    }


def _default_objective_payload(
    state: dict[str, Any],
    *,
    project: str,
    project_root: Path,
) -> dict[str, Any]:
    issue_context = _active_issue_seed_context(read_json(project_root / ".memory" / "runner" / "KANBAN_STATE.json"))
    title = issue_context["objective_title"] if issue_context else (
        _normalize_objective_title(_as_text(state.get("current_goal"))) or f"{project} runner objective"
    )
    objective_id = _as_text(state.get("objective_id")) or _new_objective_id()
    success_criteria = issue_context["success_criteria"] if issue_context else [f"Complete the prepared seams for {title} and pass final closeout gates."]
    constraints = ["Single runner_id=main", "Worktree/root scope must remain fixed"]
    if issue_context:
        constraints.extend(issue_context["constraints"])
    return {
        "objective_id": objective_id,
        "title": title,
        "goal_summary": title,
        "success_criteria": success_criteria,
        "constraints": constraints,
        "non_goals": ["Unscoped feature work outside the prepared seam backlog"],
        "completion_gate": "Final done-closeout `run_gates` passes after required seams complete.",
        "project_root": str(project_root.resolve()),
        "updated_at": utc_now(),
    }


def _task_to_seam(
    task: dict[str, Any],
    *,
    objective_id: str,
    graph_cluster_label: str | None = None,
) -> dict[str, Any]:
    task_id = _as_text(task.get("task_id"))
    title = _as_text(task.get("title"))
    seam: dict[str, Any] = {
        "seam_id": task_id or None,
        "title": title or None,
        "status": _as_text(task.get("status")).lower() or "open",
        "priority": _as_text(task.get("priority")) or "p1",
        "objective_id": objective_id,
        "project_root": _as_text(task.get("project_root")) or None,
        "target_branch": _as_text(task.get("target_branch")) or None,
        "owner_problem": title or None,
        "why_now": _as_text(task.get("profile_reason")) or _as_text(task.get("blocked_reason")) or None,
        "write_set": [str(item).strip() for item in task.get("touch_paths", []) if str(item).strip()],
        "acceptance": [str(item).strip() for item in task.get("acceptance", []) if str(item).strip()],
        "checks": [str(item).strip() for item in task.get("validation", []) if str(item).strip()],
        "depends_on": [str(item).strip() for item in task.get("depends_on", []) if str(item).strip()],
        "model_profile": _normalize_choice(task.get("model_profile"), TASK_MODEL_PROFILES) or "high",
        "fanout_risk": _normalize_choice(task.get("fanout_risk"), TASK_FANOUT_RISKS) or "high",
        "touch_paths": [str(item).strip() for item in task.get("touch_paths", []) if str(item).strip()],
        "validation_commands": [str(item).strip() for item in task.get("validation_commands", []) if str(item).strip()],
        "spillover_paths": [str(item).strip() for item in task.get("spillover_paths", []) if str(item).strip()],
        "coupling_notes": [str(item).strip() for item in task.get("coupling_notes", []) if str(item).strip()],
        "deprecation_phase": _as_text(task.get("deprecation_phase")) or None,
        "parity_baseline_ref": _as_text(task.get("parity_baseline_ref")) or None,
        "parity_surface_ids": [str(item).strip() for item in task.get("parity_surface_ids", []) if str(item).strip()],
        "parity_audit_mode": _as_text(task.get("parity_audit_mode")) or None,
        "parity_harness_commands": [
            str(item).strip() for item in task.get("parity_harness_commands", []) if str(item).strip()
        ],
        "parity_notes": _as_text(task.get("parity_notes")) or None,
        "graph_cluster_label": graph_cluster_label or None,
        "updated_at": _as_text(task.get("updated_at")) or utc_now(),
    }
    if "blocked_reason" in task and _as_text(task.get("blocked_reason")):
        seam["blocked_reason"] = _as_text(task.get("blocked_reason"))
    return seam


def _seam_to_task(
    seam: dict[str, Any],
    *,
    index: int,
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> dict[str, Any]:
    return _normalize_task_entry(
        {
            "task_id": _as_text(seam.get("seam_id")) or f"SM-{index + 1:03d}",
            "title": _as_text(seam.get("title")) or _as_text(seam.get("owner_problem")) or f"Seam {index + 1}",
            "status": _as_text(seam.get("status")) or "open",
            "priority": _as_text(seam.get("priority")) or "p1",
            "depends_on": seam.get("depends_on") or [],
            "project_root": _as_text(seam.get("project_root")) or str(project_root.resolve()),
            "target_branch": _as_text(seam.get("target_branch")) or target_branch,
            "acceptance": seam.get("acceptance") or [],
            "validation": seam.get("checks") or seam.get("validation_commands") or [],
            "updated_at": _as_text(seam.get("updated_at")) or utc_now(),
            "objective_id": _as_text(seam.get("objective_id")) or objective_id,
            "model_profile": _as_text(seam.get("model_profile")) or "high",
            "fanout_risk": _as_text(seam.get("fanout_risk")) or "high",
            "profile_reason": _as_text(seam.get("why_now")) or "",
            "deprecation_phase": _as_text(seam.get("deprecation_phase")) or "",
            "touch_paths": seam.get("touch_paths") or seam.get("write_set") or [],
            "validation_commands": seam.get("validation_commands") or seam.get("checks") or [],
            "spillover_paths": seam.get("spillover_paths") or [],
            "coupling_notes": seam.get("coupling_notes") or [],
            "parity_baseline_ref": _as_text(seam.get("parity_baseline_ref")) or "",
            "parity_surface_ids": seam.get("parity_surface_ids") or [],
            "parity_audit_mode": _as_text(seam.get("parity_audit_mode")) or "",
            "parity_harness_commands": seam.get("parity_harness_commands") or [],
            "parity_notes": _as_text(seam.get("parity_notes")) or "",
            "blocked_reason": _as_text(seam.get("blocked_reason")) or "",
        },
        index=index,
        project_root=project_root,
        target_branch=target_branch,
        objective_id=objective_id,
    )


def _build_seams_from_tasks(
    *,
    tasks_payload: dict[str, Any],
    objective_id: str,
) -> dict[str, Any]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    seams = [_task_to_seam(task, objective_id=objective_id) for task in tasks if isinstance(task, dict)]
    return {
        "objective_id": objective_id,
        "active_seam_id": _as_text(next((task.get("task_id") for task in tasks if isinstance(task, dict) and _as_text(task.get("status")).lower() in {"open", "in_progress"}), "")) or None,
        "seams": seams,
        "updated_at": utc_now(),
    }


def _seams_payload_from_tasks_file(
    *,
    paths,
    objective_id: str,
) -> dict[str, Any] | None:
    tasks_payload = read_json(paths.tasks_json)
    if not isinstance(tasks_payload, dict):
        return None
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    return _build_seams_from_tasks(tasks_payload=tasks_payload, objective_id=objective_id)


def _build_tasks_from_seams(
    *,
    seams_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> dict[str, Any]:
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        seams = []
    tasks = [
        _seam_to_task(
            seam,
            index=index,
            project_root=project_root,
            target_branch=target_branch,
            objective_id=objective_id,
        )
        for index, seam in enumerate(seams)
        if isinstance(seam, dict)
    ]
    return {
        "objective_id": objective_id,
        "tasks": tasks,
    }


def _open_seam_entries(seams_payload: dict[str, Any]) -> list[dict[str, Any]]:
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in seams:
        if not isinstance(raw, dict):
            continue
        status = _as_text(raw.get("status")).lower()
        if status in {"open", "in_progress", "blocked"}:
            items.append(raw)
    return items


def _has_completed_done_closeout_seam(seams_payload: dict[str, Any]) -> bool:
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        return False
    for raw in seams:
        if not isinstance(raw, dict):
            continue
        if _as_text(raw.get("status")).lower() == "done" and _looks_like_done_closeout_task(
            {
                "title": _as_text(raw.get("title")),
                "acceptance": raw.get("acceptance") or [],
                "validation": raw.get("checks") or [],
            }
        ):
            return True
    return False


def _next_closeout_seam_id(seams_payload: dict[str, Any]) -> str:
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        return "SM-001"
    max_index = 0
    for raw in seams:
        if not isinstance(raw, dict):
            continue
        seam_id = _as_text(raw.get("seam_id"))
        match = re.fullmatch(r"SM-(\d+)", seam_id)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"SM-{max_index + 1:03d}"


def _ensure_done_closeout_seam(
    *,
    seams_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> tuple[str | None, bool]:
    seams = seams_payload.get("seams")
    if not isinstance(seams, list):
        return None, False
    for raw in seams:
        if not isinstance(raw, dict):
            continue
        if not _looks_like_done_closeout_task(
            {
                "title": _as_text(raw.get("title")),
                "acceptance": raw.get("acceptance") or [],
                "validation": raw.get("checks") or [],
            }
        ):
            continue
        changed = False
        if _as_text(raw.get("status")).lower() != "open":
            raw["status"] = "open"
            raw["updated_at"] = utc_now()
            changed = True
        if "blocked_reason" in raw:
            raw.pop("blocked_reason", None)
            changed = True
        seams_payload["active_seam_id"] = _as_text(raw.get("seam_id")) or seams_payload.get("active_seam_id")
        return _as_text(raw.get("seam_id")) or None, changed

    seam_id = _next_closeout_seam_id(seams_payload)
    seams.append(
        {
            "seam_id": seam_id,
            "title": "Run final done-closeout `run_gates` check before closing the objective.",
            "status": "open",
            "priority": "p1",
            "depends_on": [],
            "project_root": str(project_root.resolve()),
            "target_branch": target_branch,
            "objective_id": objective_id,
            "model_profile": "mini",
            "owner_problem": "Objective closeout has not yet been verified through final repository gates.",
            "why_now": "All required seams are complete or no actionable seams remain, so explicit closeout verification is now the only truthful frontier.",
            "acceptance": [
                "Complete: No open runner seams remain after the active backlog has been safely cleared.",
                "Complete: Final done-closeout `run_gates` validation passes before the runner writes `RUNNER_DONE.lock`.",
            ],
            "checks": [
                "Run final done-state gate check (`run_gates`).",
            ],
            "write_set": [],
            "touch_paths": [],
            "validation_commands": ["run_gates"],
            "updated_at": utc_now(),
        }
    )
    seams_payload["active_seam_id"] = seam_id
    return seam_id, True


def _ensure_objective_payload(
    *,
    paths,
    state: dict[str, Any],
    project: str,
    project_root: Path,
    prd_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    payload = read_json(paths.objective_json)
    changed = False
    if not isinstance(payload, dict):
        payload = _default_objective_payload(state, project=project, project_root=project_root)
        changed = True
    title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or _normalize_objective_title(_as_text(payload.get("title")))
    if title and title != _as_text(payload.get("title")):
        payload["title"] = title
        payload["goal_summary"] = title
        changed = True
    objective_id = _as_text(prd_payload.get("objective_id")) or _as_text(payload.get("objective_id")) or _new_objective_id()
    if objective_id != _as_text(payload.get("objective_id")):
        payload["objective_id"] = objective_id
        changed = True
    payload.setdefault("goal_summary", _as_text(payload.get("title")) or title)
    payload["project_root"] = str(project_root.resolve())
    payload["success_criteria"] = [
        str(item).strip()
        for item in (prd_payload.get("success_criteria") or payload.get("success_criteria") or [])
        if str(item).strip()
    ] or [f"Complete the prepared seams for {title or project} and pass final closeout gates."]
    payload["constraints"] = [
        str(item).strip()
        for item in (prd_payload.get("constraints") or payload.get("constraints") or [])
        if str(item).strip()
    ] or ["Single runner_id=main", "Worktree/root scope must remain fixed"]
    payload.setdefault("non_goals", ["Unscoped feature work outside the prepared seam backlog"])
    payload.setdefault("completion_gate", "Final done-closeout `run_gates` passes after required seams complete.")
    payload["updated_at"] = utc_now()
    write_json(paths.objective_json, payload)
    return payload, changed


def _ensure_seams_payload(
    *,
    paths,
    objective_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    objective_id = _as_text(objective_payload.get("objective_id")) or _new_objective_id()
    derived = _build_seams_from_tasks(tasks_payload=tasks_payload, objective_id=objective_id)
    existing = read_json(paths.seams_json)
    existing_digest = json.dumps(existing, sort_keys=True) if isinstance(existing, dict) else ""
    derived_digest = json.dumps(derived, sort_keys=True)
    changed = existing_digest != derived_digest
    if changed or not isinstance(existing, dict):
        write_json(paths.seams_json, derived)
    return (derived if changed or not isinstance(existing, dict) else existing), changed


def _ensure_gaps_payload(
    *,
    paths,
    objective_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    payload = read_json(paths.gaps_json)
    changed = False
    if not isinstance(payload, dict):
        payload = {
            "objective_id": _as_text(objective_payload.get("objective_id")) or _new_objective_id(),
            "gaps": [],
            "updated_at": utc_now(),
        }
        changed = True
    if _as_text(payload.get("objective_id")) != _as_text(objective_payload.get("objective_id")):
        payload["objective_id"] = _as_text(objective_payload.get("objective_id")) or _new_objective_id()
        changed = True
    if not isinstance(payload.get("gaps"), list):
        payload["gaps"] = []
        changed = True
    payload["updated_at"] = utc_now()
    if changed:
        write_json(paths.gaps_json, payload)
    return payload, changed


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


def _seed_tasks_from_prd(
    *,
    prd_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> list[dict[str, Any]]:
    objective_title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or f"{project_root.name} objective"

    def _collect_sources(field: str) -> list[str]:
        raw_items = prd_payload.get(field)
        if not isinstance(raw_items, list):
            return []
        items: list[str] = []
        for raw in raw_items:
            candidate = _normalize_line(str(raw))
            if candidate:
                items.append(candidate[:220])
        return items

    seed_sources = _collect_sources("scope_in")
    seed_kind = "scope_in"
    if not seed_sources:
        seed_sources = _collect_sources("success_criteria")
        seed_kind = "success_criteria"
    if not seed_sources:
        seed_sources = _collect_sources("constraints")
        seed_kind = "constraints"
    if not seed_sources:
        fallback_title = _normalize_line(objective_title)
        if fallback_title:
            seed_sources = [fallback_title]
            seed_kind = "objective"

    tasks: list[dict[str, Any]] = []
    for idx, source in enumerate(seed_sources[:3]):
        title = _strip_task_id_prefix(source) or f"{objective_title} slice {idx + 1}"
        if seed_kind == "scope_in":
            acceptance = [f"Complete the scoped slice: {title}."]
            validation = [f"Run the smallest targeted proof for {title}."]
        elif seed_kind == "success_criteria":
            acceptance = [f"Satisfy the seeded success criterion: {title}."]
            validation = [f"Prove the criterion with the narrowest applicable check for {title}."]
        elif seed_kind == "constraints":
            acceptance = [f"Honor the seeded constraint while completing the objective: {title}."]
            validation = [f"Confirm the constraint remains satisfied after the slice for {title}."]
        else:
            acceptance = [f"Complete the objective slice: {title}."]
            validation = [f"Run the smallest targeted proof for {title}."]
        tasks.append(
            _normalize_task_entry(
                {
                    "task_id": f"TT-{idx + 1:03d}",
                    "title": title,
                    "status": "open",
                    "priority": "p1",
                    "depends_on": [],
                    "project_root": str(project_root.resolve()),
                    "target_branch": target_branch,
                    "acceptance": acceptance,
                    "validation": validation,
                    "updated_at": utc_now(),
                    "objective_id": objective_id,
                },
                index=idx,
                project_root=project_root,
                target_branch=target_branch,
                objective_id=objective_id,
            )
        )
    return tasks


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
        tasks = _seed_tasks_from_prd(
            prd_payload=prd_payload,
            project_root=project_root,
            target_branch=target_branch,
            objective_id=objective_id,
        )
        if not tasks:
            raise ValueError("run_setup requires a concrete objective and task seed; generic TT-001 fallback is disabled.")
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


def _dependency_block_reason(task: dict[str, Any], done_ids: set[str]) -> str:
    deps = task.get("depends_on")
    if not isinstance(deps, list):
        return ""
    unresolved = [str(dep).strip() for dep in deps if str(dep).strip() and str(dep).strip() not in done_ids]
    if not unresolved:
        return ""
    if len(unresolved) > 3:
        unresolved = [*unresolved[:3], "..."]
    return f"waiting_on: {', '.join(unresolved)}"[:220]


def _list_dirty_paths(project_root: Path) -> list[str]:
    git_context = detect_git_context(project_root)
    if not _as_text(git_context.get("git_root")):
        return []
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    paths: list[str] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        payload = line[3:].strip()
        if not payload:
            continue
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1].strip()
        normalized = payload.replace("\\", "/").lstrip("./")
        if normalized:
            paths.append(normalized)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _clear_graph_frontier_blocks(tasks_payload: dict[str, Any]) -> bool:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        blocked_reason = _as_text(task.get("blocked_reason"))
        if not blocked_reason.startswith("graph_frontier_waiting_on:"):
            continue
        if _as_text(task.get("status")).lower() == "blocked":
            task["status"] = "open"
            task["updated_at"] = utc_now()
        task.pop("blocked_reason", None)
        changed = True
    return changed


def _phase_rank(task: dict[str, Any], phase_boundaries: list[str]) -> int:
    phase = _normalize_choice(task.get("deprecation_phase"), TASK_DEPRECATION_PHASES)
    if not phase:
        return len(phase_boundaries) + 1
    try:
        return phase_boundaries.index(phase)
    except ValueError:
        return len(phase_boundaries) + 1


def _task_scope_size(task: dict[str, Any]) -> int:
    return sum(
        len(_normalize_text_list(task.get(key), item_chars=220, max_items=64))
        for key in ("touch_paths", "spillover_paths", "coupling_notes", "acceptance", "validation")
    )


def _fanout_rank(task: dict[str, Any]) -> int:
    fanout = _normalize_choice(task.get("fanout_risk"), TASK_FANOUT_RISKS)
    return {"low": 0, "medium": 1, "high": 2}.get(fanout or "", 1)


def _model_profile_rank(task: dict[str, Any]) -> int:
    profile = _normalize_choice(task.get("model_profile"), TASK_MODEL_PROFILES)
    return 0 if profile == "mini" else 1


def _normalize_dependency_blockers(tasks_payload: dict[str, Any]) -> bool:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    done_ids: set[str] = {
        str(task.get("task_id", "")).strip()
        for task in tasks
        if isinstance(task, dict) and str(task.get("status", "")).strip().lower() == "done"
    }
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status", "")).strip().lower()
        if status == "done":
            if "blocked_reason" in task:
                task.pop("blocked_reason", None)
                changed = True
            continue
        dependency_reason = _dependency_block_reason(task, done_ids)
        blocked_reason = _as_text(task.get("blocked_reason"))
        dependency_blocked = blocked_reason.startswith("waiting_on:")
        if dependency_reason:
            if status in {"open", "in_progress"}:
                task["status"] = "blocked"
                task["blocked_reason"] = dependency_reason
                task["updated_at"] = utc_now()
                changed = True
            elif status == "blocked" and (dependency_blocked or not blocked_reason) and blocked_reason != dependency_reason:
                task["blocked_reason"] = dependency_reason
                task["updated_at"] = utc_now()
                changed = True
        elif status == "blocked" and dependency_blocked:
            task["status"] = "open"
            task.pop("blocked_reason", None)
            task["updated_at"] = utc_now()
            changed = True
        elif status in {"open", "in_progress"} and dependency_blocked:
            task.pop("blocked_reason", None)
            changed = True
    return changed


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
    candidate_tasks = [task for task in open_tasks if not _task_marked_superseded(task)]
    if not candidate_tasks:
        candidate_tasks = open_tasks

    def sort_key(task: dict[str, Any]) -> tuple[int, float, str]:
        priority = _task_priority_rank(str(task.get("priority", "p1")))
        updated = _parse_iso_ts(task.get("updated_at"))
        task_id = str(task.get("task_id", "")).strip()
        return (priority, updated, task_id)

    eligible = [task for task in candidate_tasks if _task_depends_resolved(task, done_ids)]
    if eligible:
        chosen = sorted(eligible, key=sort_key)[0]
        return chosen, "Deterministic selection: open + dependencies resolved + priority + oldest updated_at."
    return None, "No dependency-resolved open tasks are currently available."


def _graph_priority_rank(cluster_label: str, slice_priority: list[str]) -> int:
    if not cluster_label:
        return len(slice_priority) + 1
    try:
        return slice_priority.index(cluster_label)
    except ValueError:
        return len(slice_priority) + 1


def _apply_graph_frontier(tasks_payload: dict[str, Any], *, selected_task_id: str) -> bool:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list) or not selected_task_id:
        return False
    changed = False
    blocked_reason = f"graph_frontier_waiting_on: {selected_task_id}"[:220]
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = _as_text(task.get("task_id"))
        status = _as_text(task.get("status")).lower()
        if task_id == selected_task_id:
            if status == "blocked" and _as_text(task.get("blocked_reason")).startswith("graph_frontier_waiting_on:"):
                task["status"] = "open"
                task.pop("blocked_reason", None)
                task["updated_at"] = utc_now()
                changed = True
            continue
        if status == "open":
            task["status"] = "blocked"
            task["blocked_reason"] = blocked_reason
            task["updated_at"] = utc_now()
            changed = True
        elif status == "blocked" and _as_text(task.get("blocked_reason")).startswith("graph_frontier_waiting_on:"):
            if _as_text(task.get("blocked_reason")) != blocked_reason:
                task["blocked_reason"] = blocked_reason
                task["updated_at"] = utc_now()
                changed = True
    return changed


def _select_next_task_graph_first(
    *,
    tasks_payload: dict[str, Any],
    project_root: Path,
    paths,
    dirty_paths: list[str],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None, dict[str, Any] | None]:
    config = load_graph_config_for_project(project_root)
    if not isinstance(config, dict):
        task, reason = _select_next_task(tasks_payload)
        return task, reason, None, None

    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None, "No tasks registry available.", None, config
    done_ids: set[str] = {
        str(task.get("task_id", "")).strip()
        for task in tasks
        if isinstance(task, dict) and str(task.get("status", "")).strip().lower() == "done"
    }
    open_tasks = [
        task for task in tasks if isinstance(task, dict) and str(task.get("status", "")).strip().lower() == "open"
    ]
    if not open_tasks:
        return None, "No open tasks remain in TASKS.json.", None, config
    candidate_tasks = [task for task in open_tasks if not _task_marked_superseded(task)]
    if not candidate_tasks:
        candidate_tasks = open_tasks
    eligible = [task for task in candidate_tasks if _task_depends_resolved(task, done_ids)]
    if not eligible:
        return None, "No dependency-resolved open tasks are currently available.", None, config

    summaries: dict[str, dict[str, Any]] = {}
    for task in eligible:
        task_id = _as_text(task.get("task_id"))
        if not task_id:
            continue
        summaries[task_id] = summarize_task_graph_slice(
            project_root=project_root,
            paths=paths,
            task=task,
            dirty_paths=dirty_paths,
        )

    dominant_dirty_cluster = None
    if _as_text(config.get("graph_policy")) == "hybrid" and dirty_paths:
        counts: dict[str, int] = {}
        for summary in summaries.values():
            label = _as_text(summary.get("graph_cluster_label"))
            overlap = int(summary.get("dirty_overlap_count") or 0)
            if label and overlap > 0:
                counts[label] = max(counts.get(label, 0), overlap)
        if counts:
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                dominant_dirty_cluster = ranked[0][0]

    slice_priority = [str(item).strip() for item in config.get("slice_priority", []) if str(item).strip()]
    phase_boundaries = [str(item).strip() for item in config.get("phase_boundaries", []) if str(item).strip()]

    def sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
        task_id = _as_text(task.get("task_id"))
        summary = summaries.get(task_id, {})
        cluster_label = _as_text(summary.get("graph_cluster_label"))
        adjacent_count = len(summary.get("graph_adjacent_families_top3", []) or [])
        boundary_count = len(summary.get("graph_boundary_warnings_top3", []) or [])
        dirty_overlap = int(summary.get("dirty_overlap_count") or 0)
        priority = _task_priority_rank(str(task.get("priority", "p1")))
        updated = _parse_iso_ts(task.get("updated_at"))
        dirty_rank = 1
        if dominant_dirty_cluster and cluster_label == dominant_dirty_cluster and dirty_overlap > 0:
            dirty_rank = 0
        return (
            _phase_rank(task, phase_boundaries),
            dirty_rank,
            -dirty_overlap if dirty_rank == 0 else 0,
            _graph_priority_rank(cluster_label, slice_priority),
            adjacent_count + boundary_count,
            _fanout_rank(task),
            _model_profile_rank(task),
            _task_scope_size(task),
            priority,
            updated,
            task_id,
        )

    chosen = sorted(eligible, key=sort_key)[0]
    chosen_summary = summaries.get(_as_text(chosen.get("task_id"))) or {}
    reason = _as_text(chosen_summary.get("graph_slice_reason"))
    if reason:
        return chosen, reason, chosen_summary, config
    cluster_label = _as_text(chosen_summary.get("graph_cluster_label"))
    if cluster_label:
        return (
            chosen,
            f"Graph-first selection kept one actionable `{cluster_label}` slice after sequencing, dependency, and decoupling checks.",
            chosen_summary,
            config,
        )
    return chosen, "Graph-enabled selection chose the smallest dependency-safe slice.", chosen_summary, config


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


def _has_completed_done_closeout_task(tasks_payload: dict[str, Any]) -> bool:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return False
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        if _as_text(raw.get("status")).lower() == "done" and _looks_like_done_closeout_task(raw):
            return True
    return False


def _next_closeout_task_id(tasks_payload: dict[str, Any]) -> str:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return "TT-001"
    max_index = 0
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        task_id = _as_text(raw.get("task_id"))
        match = re.fullmatch(r"TT-(\d+)", task_id)
        if not match:
            continue
        max_index = max(max_index, int(match.group(1)))
    return f"TT-{max_index + 1:03d}"


def _ensure_done_closeout_task(
    *,
    tasks_payload: dict[str, Any],
    project_root: Path,
    target_branch: str,
    objective_id: str,
) -> tuple[str | None, bool]:
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None, False

    for raw in tasks:
        if not isinstance(raw, dict) or not _looks_like_done_closeout_task(raw):
            continue
        changed = False
        if _as_text(raw.get("status")).lower() != "open":
            raw["status"] = "open"
            raw["updated_at"] = utc_now()
            changed = True
        if "blocked_reason" in raw:
            raw.pop("blocked_reason", None)
            changed = True
        return _as_text(raw.get("task_id")) or None, changed

    task_id = _next_closeout_task_id(tasks_payload)
    tasks.append(
        {
            "task_id": task_id,
            "title": "Run final done-closeout `run_gates` check before closing the objective.",
            "status": "open",
            "priority": "p1",
            "depends_on": [],
            "project_root": str(project_root.resolve()),
            "target_branch": target_branch,
            "objective_id": objective_id,
            "model_profile": "mini",
            "acceptance": [
                "Complete: No open runner tasks remain after the active backlog has been safely cleared.",
                "Complete: Final done-closeout `run_gates` validation passes before the runner writes `RUNNER_DONE.lock`.",
            ],
            "validation": [
                "Run final done-state gate check (`run_gates`).",
            ],
            "updated_at": utc_now(),
        }
    )
    return task_id, True


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
    if open_tasks_count == 0 or "done-closeout" in next_task_text.lower() or done_gate_status_value in {"failed", "passed"}:
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
    model_profile: str | None,
    fanout_risk: str | None,
    touch_paths: list[str],
    validation_commands: list[str],
    coupling_notes: list[str],
    parity_baseline_ref: str | None,
    parity_surface_ids: list[str],
    parity_audit_mode: str | None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "objective_title": objective_title,
        "active_seam_id": next_task_id,
        "next_task_id": next_task_id,
        "next_task": next_task_text,
        "next_task_reason": selection_reason,
        "blockers_top3": blockers[:3],
        "implementation_plan_top2": implementation_plan[:2],
        "completed_recent_top5": completed_recent[:5],
        "last_iteration_summary": last_iteration_summary,
        "task_acceptance_top3": task_acceptance[:3],
        "task_validation_top3": task_validation[:3],
        "model_profile": model_profile,
        "fanout_risk": fanout_risk,
        "touch_paths_top8": touch_paths[:8],
        "validation_commands_top3": validation_commands[:3],
        "coupling_notes_top4": coupling_notes[:4],
        "parity_baseline_ref": parity_baseline_ref,
        "parity_surface_ids_top3": parity_surface_ids[:3],
        "parity_audit_mode": parity_audit_mode,
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
            "Exit cleanly so the controller can refresh runner state and prepare the next cycle.",
        ]
    if phase == "verify":
        return [
            phase_goal,
            f"Stay on the current validation surface until blockers are cleared or narrowed: {task_text}",
            "Exit cleanly so the controller can refresh runner state and prepare the next cycle.",
        ]
    if phase == "closeout":
        return [
            phase_goal,
            "Run final closeout gates and leave exactly one concrete blocker if done-state still fails.",
            "Exit cleanly so the controller can refresh runner state and prepare the next cycle.",
        ]
    return [
        phase_goal,
        f"Stay on one coherent implementation surface while advancing: {task_text}",
        "Exit cleanly so the controller can refresh runner state and prepare the next cycle.",
    ]


def _write_exec_context(
    *,
    paths,
    state: dict[str, Any],
    prd_payload: dict[str, Any],
    tasks_payload: dict[str, Any],
    task: dict[str, Any] | None,
    project_root: Path,
    target_branch: str,
    phase: str,
    phase_goal: str,
    context_sources: list[dict[str, Any]],
    context_delta: dict[str, Any],
    graph_summary: dict[str, Any] | None = None,
    parity_scope: dict[str, Any] | None = None,
    kanban_state: dict[str, Any] | None = None,
) -> None:
    existing_exec_context = read_json(paths.exec_context_json) or {}
    graph_summary = graph_summary if isinstance(graph_summary, dict) else {}
    parity_scope = parity_scope if isinstance(parity_scope, dict) else {}
    kanban_state = kanban_state if isinstance(kanban_state, dict) else {}
    runtime_policy = state.get("runtime_policy") if isinstance(state.get("runtime_policy"), dict) else {}
    current_snapshot = _capture_progress_snapshot(state=state, project_root=project_root, tasks_payload=tasks_payload)
    preserved_cycle_baseline = (
        existing_exec_context.get("cycle_progress_baseline")
        if isinstance(existing_exec_context, dict)
        else None
    )
    task_id = _as_text(task.get("task_id")) if isinstance(task, dict) else ""
    task_title = _as_text(task.get("title")) if isinstance(task, dict) else ""
    existing_objective_id = _as_text(existing_exec_context.get("objective_id")) if isinstance(existing_exec_context, dict) else ""
    existing_project_root = _as_text(existing_exec_context.get("project_root")) if isinstance(existing_exec_context, dict) else ""
    existing_target_branch = _as_text(existing_exec_context.get("target_branch")) if isinstance(existing_exec_context, dict) else ""
    if (
        not isinstance(preserved_cycle_baseline, dict)
        or (existing_objective_id and existing_objective_id != _as_text(prd_payload.get("objective_id")))
        or (existing_project_root and existing_project_root != str(project_root.resolve()))
        or (existing_target_branch and target_branch and existing_target_branch != target_branch)
    ):
        preserved_cycle_baseline = current_snapshot
    acceptance = task.get("acceptance") if isinstance(task, dict) else []
    validation = task.get("validation") if isinstance(task, dict) else []
    if not isinstance(acceptance, list):
        acceptance = []
    if not isinstance(validation, list):
        validation = []
    model_profile = _normalize_choice(task.get("model_profile"), TASK_MODEL_PROFILES) if isinstance(task, dict) else None
    profile_reason = _normalize_line(str(task.get("profile_reason", ""))) if isinstance(task, dict) else ""
    fanout_risk = _normalize_choice(task.get("fanout_risk"), TASK_FANOUT_RISKS) if isinstance(task, dict) else None
    deprecation_phase = (
        _normalize_choice(task.get("deprecation_phase"), TASK_DEPRECATION_PHASES) if isinstance(task, dict) else None
    )
    touch_paths = _normalize_text_list(task.get("touch_paths"), item_chars=220, max_items=24) if isinstance(task, dict) else []
    validation_commands = (
        _normalize_text_list(task.get("validation_commands"), item_chars=220, max_items=8) if isinstance(task, dict) else []
    )
    spillover_paths = (
        _normalize_text_list(task.get("spillover_paths"), item_chars=220, max_items=16) if isinstance(task, dict) else []
    )
    coupling_notes = (
        _normalize_text_list(task.get("coupling_notes"), item_chars=220, max_items=8) if isinstance(task, dict) else []
    )
    parity_fail_closed = bool(parity_scope.get("parity_fail_closed", False))
    hard_rules = [
        "stay_within_phase_goal",
        "validate_as_you_go",
        "controller_refreshes_runner_state_after_prompt_exit",
        "controller_writes_prepared_marker_after_prompt_exit",
        "exit_session_on_phase_handoff",
    ]
    if parity_fail_closed:
        hard_rules.append("parity_tasks_fail_closed_until_no_known_delta_remains")
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    active_checkout = (
        kanban_state.get("active_checkout") if isinstance(kanban_state.get("active_checkout"), dict) else {}
    )
    kanban_loop = kanban_state.get("loop") if isinstance(kanban_state.get("loop"), dict) else {}
    telegram_state = kanban_state.get("telegram") if isinstance(kanban_state.get("telegram"), dict) else {}
    payload = {
        "objective_id": _as_text(prd_payload.get("objective_id")),
        "phase": phase,
        "phase_goal": phase_goal,
        "seam_id": task_id or None,
        "task_id": task_id or None,
        "active_seam_id": _as_text(state.get("active_seam_id")) or task_id or None,
        "seam_title": task_title or None,
        "task_title": task_title or None,
        "next_seam_id": _as_text(state.get("next_seam_id") or state.get("next_task_id")) or None,
        "next_seam": _as_text(state.get("next_seam") or state.get("next_task")) or None,
        "next_seam_reason": _as_text(state.get("next_seam_reason") or state.get("next_task_reason")) or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "next_task_reason": _as_text(state.get("next_task_reason")) or None,
        "task_acceptance": [str(item) for item in acceptance if str(item).strip()],
        "task_validation": [str(item) for item in validation if str(item).strip()],
        "model_profile": model_profile or None,
        "profile_reason": profile_reason or None,
        "fanout_risk": fanout_risk or None,
        "deprecation_phase": deprecation_phase or None,
        "touch_paths": touch_paths,
        "validation_commands": validation_commands,
        "spillover_paths": spillover_paths,
        "coupling_notes": coupling_notes,
        "parity_enabled": bool(parity_scope.get("parity_enabled", False)),
        "parity_baseline_ref": _as_text(parity_scope.get("parity_baseline_ref")) or None,
        "parity_surface_ids": [
            str(item).strip()
            for item in parity_scope.get("parity_surface_ids", [])
            if str(item).strip()
        ],
        "parity_surfaces": parity_scope.get("parity_surfaces", []),
        "parity_audit_mode": _as_text(parity_scope.get("parity_audit_mode")) or None,
        "parity_harness_commands": [
            str(item).strip()
            for item in parity_scope.get("parity_harness_commands", [])
            if str(item).strip()
        ],
        "parity_fail_closed": bool(parity_scope.get("parity_fail_closed", False)),
        "parity_trigger_reason": _as_text(parity_scope.get("parity_trigger_reason")) or None,
        "parity_notes": _as_text(parity_scope.get("parity_notes")) or None,
        "parity_policy": parity_scope.get("parity_policy", {}),
        "graph_enabled": bool(graph_summary.get("graph_enabled", False)),
        "graph_digest": _as_text(graph_summary.get("graph_digest")) or None,
        "graph_active_slice_digest": _as_text(graph_summary.get("graph_active_slice_digest")) or None,
        "graph_cluster_label": _as_text(graph_summary.get("graph_cluster_label")) or None,
        "graph_slice_reason": _as_text(graph_summary.get("graph_slice_reason")) or None,
        "graph_adjacent_families_top3": [
            str(item).strip()
            for item in graph_summary.get("graph_adjacent_families_top3", [])
            if str(item).strip()
        ][:3],
        "graph_boundary_warnings_top3": [
            str(item).strip()
            for item in graph_summary.get("graph_boundary_warnings_top3", [])
            if str(item).strip()
        ][:3],
        "runtime_policy": {
            "runner_mode": _as_text(runtime_policy.get("runner_mode")) or None,
            "session_strategy": _as_text(runtime_policy.get("session_strategy")) or None,
            "task_source": _as_text(runtime_policy.get("task_source")) or None,
            "kanban_enabled": bool(runtime_policy.get("kanban_enabled", False)),
            "completion_policy": _as_text(runtime_policy.get("completion_policy")) or None,
        },
        "kanban_state_file": str(paths.kanban_state_json.resolve()),
        "kanban_mode": _as_text(kanban_state.get("mode")) or None,
        "kanban_phase": _as_text(kanban_state.get("phase")) or None,
        "kanban_continue_until": _as_text(kanban_loop.get("continue_until")) or None,
        "kanban_active_issue_identifier": _as_text(active_issue.get("identifier")) or None,
        "kanban_active_issue_title": _as_text(active_issue.get("title")) or None,
        "kanban_active_issue_url": _as_text(active_issue.get("url")) or None,
        "kanban_active_issue_external_url": _as_text(active_issue.get("external_url")) or None,
        "kanban_active_issue_repo": _as_text(active_issue.get("repo")) or None,
        "kanban_active_issue_project_key": _as_text(active_issue.get("project_key")) or None,
        "kanban_active_issue_project_name": _as_text(active_issue.get("project_name")) or None,
        "kanban_active_issue_linear_id": _as_text(active_issue.get("linear_id")) or None,
        "kanban_active_issue_state_name": _as_text(active_issue.get("state_name")) or None,
        "kanban_active_issue_state_type": _as_text(active_issue.get("state_type")) or None,
        "kanban_active_issue_description": _as_text(active_issue.get("description")) or None,
        "kanban_checkout_root": _as_text(active_checkout.get("repo_root")) or None,
        "kanban_checkout_worktree": _as_text(active_checkout.get("worktree")) or None,
        "kanban_checkout_branch": _as_text(active_checkout.get("branch")) or None,
        "kanban_session_key": _as_text(telegram_state.get("session_key")) or None,
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
        "next_seam_id": _as_text(state.get("next_seam_id") or state.get("next_task_id")) or None,
        "next_seam": _as_text(state.get("next_seam") or state.get("next_task")) or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "state_revision": int(state.get("state_revision", 0)),
        "status": _as_text(state.get("status")) or None,
    }
    write_json(paths.cycle_prepared_file, marker_payload)
    return paths.cycle_prepared_file


def _active_backlog_digest(tasks_payload: dict[str, Any] | None) -> str | None:
    if not isinstance(tasks_payload, dict):
        return None
    tasks = tasks_payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    active_entries: list[dict[str, Any]] = []
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        status = _as_text(raw.get("status")).lower()
        if status not in {"open", "in_progress", "blocked"}:
            continue
        active_entries.append(
            {
                "task_id": _as_text(raw.get("task_id")) or None,
                "title": _as_text(raw.get("title")) or None,
                "status": status or None,
                "priority": _as_text(raw.get("priority")) or None,
                "blocked_reason": _as_text(raw.get("blocked_reason")) or None,
                "depends_on": [str(item).strip() for item in raw.get("depends_on", []) if str(item).strip()],
                "acceptance": [str(item).strip() for item in raw.get("acceptance", []) if str(item).strip()],
                "validation": [str(item).strip() for item in raw.get("validation", []) if str(item).strip()],
                "model_profile": _as_text(raw.get("model_profile")) or None,
                "profile_reason": _as_text(raw.get("profile_reason")) or None,
                "fanout_risk": _as_text(raw.get("fanout_risk")) or None,
                "deprecation_phase": _as_text(raw.get("deprecation_phase")) or None,
                "touch_paths": [str(item).strip() for item in raw.get("touch_paths", []) if str(item).strip()],
                "validation_commands": [
                    str(item).strip() for item in raw.get("validation_commands", []) if str(item).strip()
                ],
                "spillover_paths": [str(item).strip() for item in raw.get("spillover_paths", []) if str(item).strip()],
                "coupling_notes": [str(item).strip() for item in raw.get("coupling_notes", []) if str(item).strip()],
                "objective_id": _as_text(raw.get("objective_id")) or None,
                "parity_baseline_ref": _as_text(raw.get("parity_baseline_ref")) or None,
                "parity_surface_ids": [str(item).strip() for item in raw.get("parity_surface_ids", []) if str(item).strip()],
                "parity_audit_mode": _as_text(raw.get("parity_audit_mode")) or None,
                "parity_notes": _as_text(raw.get("parity_notes")) or None,
            }
        )
    if not active_entries:
        return None
    active_entries.sort(key=lambda item: (_as_text(item.get("task_id")), _as_text(item.get("title"))))
    payload = json.dumps(active_entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _capture_progress_snapshot(
    *,
    state: dict[str, Any],
    project_root: Path,
    tasks_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    git_context = detect_git_context(project_root)
    return {
        "phase": coerce_runner_phase(state.get("current_phase"), default="discover"),
        "phase_status": _as_text(state.get("phase_status")) or None,
        "active_seam_id": _as_text(state.get("active_seam_id")) or None,
        "next_seam_id": _as_text(state.get("next_seam_id") or state.get("next_task_id")) or None,
        "next_seam": _as_text(state.get("next_seam") or state.get("next_task")) or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "status": _as_text(state.get("status")) or None,
        "git_head": _as_text(git_context.get("git_head")) or None,
        "worktree_fingerprint": compute_worktree_fingerprint(project_root),
        "active_backlog_digest": _active_backlog_digest(tasks_payload),
    }


def _progress_has_advanced(*, baseline: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not isinstance(baseline, dict):
        return True
    return any(
        (
            coerce_runner_phase(baseline.get("phase"), default="discover")
            != coerce_runner_phase(current.get("phase"), default="discover"),
            _as_text(baseline.get("phase_status")) != _as_text(current.get("phase_status")),
            _as_text(baseline.get("active_seam_id")) != _as_text(current.get("active_seam_id")),
            _as_text(baseline.get("next_seam_id")) != _as_text(current.get("next_seam_id")),
            _as_text(baseline.get("next_seam")) != _as_text(current.get("next_seam")),
            _as_text(baseline.get("next_task_id")) != _as_text(current.get("next_task_id")),
            _as_text(baseline.get("next_task")) != _as_text(current.get("next_task")),
            _as_text(baseline.get("status")) != _as_text(current.get("status")),
            _as_text(baseline.get("git_head")) != _as_text(current.get("git_head")),
            _as_text(baseline.get("worktree_fingerprint")) != _as_text(current.get("worktree_fingerprint")),
            _as_text(baseline.get("active_backlog_digest")) != _as_text(current.get("active_backlog_digest")),
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
    previous_state = read_json(paths.state_file) or {}
    had_kanban_state = paths.kanban_state_json.exists()
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
    kanban_state = load_or_init_kanban_state(paths=paths, project=project)
    if not had_kanban_state:
        created.append(str(paths.kanban_state_json.relative_to(paths.memory_dir)))
    if approve_enable is None and preserved_enabled:
        state = update_state(
            paths.state_file,
            state,
            status="ready",
            enabled=True,
            last_hil_decision=preserved_hil_decision or "enable_approved",
        )

    git_context = detect_git_context(project_root)
    active_checkout = kanban_state.get("active_checkout")
    if not isinstance(active_checkout, dict):
        active_checkout = {}
    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    active_checkout = {
        "repo_root": _as_text(active_issue.get("repo_root")) or str(project_root),
        "worktree": str(project_root),
        "branch": _as_text(active_issue.get("branch")) or _as_text(git_context.get("git_branch")) or active_checkout.get("branch"),
    }
    if kanban_state.get("active_checkout") != active_checkout:
        kanban_state["active_checkout"] = active_checkout
        kanban_state["updated_at"] = utc_now()
        write_json(paths.kanban_state_json, kanban_state)

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
    objective_payload, objective_changed = _ensure_objective_payload(
        paths=paths,
        state=state,
        project=project,
        project_root=project_root,
        prd_payload=prd_payload,
    )
    if objective_changed:
        created.append(str(paths.objective_json.relative_to(paths.memory_dir)))
    seams_payload, seams_changed = _ensure_seams_payload(
        paths=paths,
        objective_payload=objective_payload,
        tasks_payload=tasks_payload,
    )
    if seams_changed:
        created.append(str(paths.seams_json.relative_to(paths.memory_dir)))
    gaps_payload, gaps_changed = _ensure_gaps_payload(
        paths=paths,
        objective_payload=objective_payload,
    )
    if gaps_changed:
        created.append(str(paths.gaps_json.relative_to(paths.memory_dir)))
    tasks_payload = _build_tasks_from_seams(
        seams_payload=seams_payload,
        project_root=project_root,
        target_branch=branch_guess,
        objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
    )
    write_json(paths.tasks_json, tasks_payload)
    parity_payload, parity_changed = _ensure_runner_parity_payload(
        paths=paths,
        prd_payload=prd_payload,
        tasks_payload=tasks_payload,
        project_root=project_root,
    )
    if parity_changed:
        created.append(str(paths.runner_parity_json.relative_to(paths.memory_dir)))

    if _normalize_dependency_blockers(tasks_payload):
        tasks_changed = True
        write_json(paths.tasks_json, tasks_payload)
        seams_payload = _build_seams_from_tasks(
            tasks_payload=tasks_payload,
            objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
        )
        write_json(paths.seams_json, seams_payload)
    if _clear_graph_frontier_blocks(tasks_payload):
        tasks_changed = True
        write_json(paths.tasks_json, tasks_payload)
        seams_payload = _build_seams_from_tasks(
            tasks_payload=tasks_payload,
            objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
        )
        write_json(paths.seams_json, seams_payload)

    closeout_gate_result: tuple[bool | None, str] | None = None
    synthesized_closeout_task = False
    preserve_done_state = (
        paths.done_lock.exists() or (previous_done_gate == "passed" and (previous_done_candidate or previous_status == "done"))
    ) and _has_completed_done_closeout_seam(seams_payload)
    open_task_candidates = _open_seam_entries(seams_payload)
    if not open_task_candidates and not preserve_done_state:
        _, closeout_task_changed = _ensure_done_closeout_seam(
            seams_payload=seams_payload,
            project_root=project_root,
            target_branch=branch_guess,
            objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
        )
        if closeout_task_changed:
            synthesized_closeout_task = True
            tasks_changed = True
            tasks_payload = _build_tasks_from_seams(
                seams_payload=seams_payload,
                project_root=project_root,
                target_branch=branch_guess,
                objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
            )
            write_json(paths.tasks_json, tasks_payload)
            write_json(paths.seams_json, seams_payload)
        open_task_candidates = _open_seam_entries(seams_payload)
    if (
        not synthesized_closeout_task
        and len(open_task_candidates) == 1
        and _looks_like_done_closeout_task(
            {
                "title": _as_text(open_task_candidates[0].get("title")),
                "acceptance": open_task_candidates[0].get("acceptance") or [],
                "validation": open_task_candidates[0].get("checks") or [],
            }
        )
    ):
        closeout_gate_result = _run_done_closeout_gates(
            project_root=project_root,
            gates_file=paths.gates_file,
            runner_id=runner_id,
        )

    dirty_paths = _list_dirty_paths(project_root)
    build_runner_graph_artifacts(
        project_root=project_root,
        paths=paths,
        selected_task=None,
        dirty_paths=dirty_paths,
    )
    selected_task, selection_reason, graph_summary, graph_config = _select_next_task_graph_first(
        tasks_payload=tasks_payload,
        project_root=project_root,
        paths=paths,
        dirty_paths=dirty_paths,
    )
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
                seams_payload = _build_seams_from_tasks(
                    tasks_payload=tasks_payload,
                    objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
                )
                write_json(paths.seams_json, seams_payload)
            selected_task = None
            selection_reason = "Branch enforcement failed; selected task moved to blocked."

    if (
        isinstance(selected_task, dict)
        and isinstance(graph_config, dict)
        and _as_text(graph_config.get("actionable_frontier")) == "one_open"
    ):
        selected_task_id = _as_text(selected_task.get("task_id"))
        if selected_task_id and _apply_graph_frontier(tasks_payload, selected_task_id=selected_task_id):
            tasks_changed = True
            write_json(paths.tasks_json, tasks_payload)
            seams_payload = _build_seams_from_tasks(
                tasks_payload=tasks_payload,
                objective_id=_as_text(objective_payload.get("objective_id")) or _new_objective_id(),
            )
            write_json(paths.seams_json, seams_payload)
            selected_task, selection_reason, graph_summary, graph_config = _select_next_task_graph_first(
                tasks_payload=tasks_payload,
                project_root=project_root,
                paths=paths,
                dirty_paths=dirty_paths,
            )

    git_context = detect_git_context(project_root)
    objective_id = _as_text(prd_payload.get("objective_id")) or _new_objective_id()
    previous_objective_id = _as_text(previous_state.get("objective_id"))
    objective_changed = bool(previous_objective_id and previous_objective_id != objective_id)
    objective_title = _normalize_objective_title(_as_text(prd_payload.get("title"))) or f"{project} runner objective"
    open_tasks_count = count_open_seams(seams_payload)

    next_task_id = _as_text(selected_task.get("task_id")) if isinstance(selected_task, dict) else None
    next_task_title = _as_text(selected_task.get("title")) if isinstance(selected_task, dict) else ""
    active_seam_id = next_task_id or _as_text(seams_payload.get("active_seam_id")) or None
    selected_parity_scope = _resolve_task_parity_scope(
        task=selected_task if isinstance(selected_task, dict) else None,
        parity_payload=parity_payload,
        title=next_task_title,
        acceptance=[
            str(item).strip()
            for item in ((selected_task or {}).get("acceptance") if isinstance(selected_task, dict) else [])
            if str(item).strip()
        ],
        validation=[
            str(item).strip()
            for item in ((selected_task or {}).get("validation") if isinstance(selected_task, dict) else [])
            if str(item).strip()
        ],
    )
    if next_task_title:
        next_task_text = next_task_title
    elif open_tasks_count == 0:
        next_task_text = DONE_NEXT_TASK_TEXT
    else:
        next_task_text = "No executable open task is currently available."

    blockers = list(state.get("blockers", []))
    if not isinstance(blockers, list):
        blockers = []
    if branch_error:
        blockers.append(f"Branch enforcement failed: {branch_error}")
    blockers = [str(item).strip() for item in blockers if str(item).strip()][-8:]
    if not isinstance(selected_task, dict) and open_tasks_count > 0:
        blockers.append(selection_reason[:220])
        blockers = blockers[-8:]

    state_revision = int(state.get("state_revision", 0)) + 1
    status_value = "ready"
    done_candidate_value = False
    done_gate_status_value = "pending"
    if closeout_gate_result is not None and open_tasks_count > 0:
        gates_ok, gates_output = closeout_gate_result
        if gates_ok is True:
            done_gate_status_value = "passed"
            selection_reason = (
                "Final done-closeout validation is already green; keep the explicit closeout task "
                "open until the runner converges cleanly and marks done from a closeout cycle."
            )
        elif gates_ok is False:
            done_gate_status_value = "failed"
            next_task_text = "Resolve final done-closeout gate failure."
            selection_reason = "Final done-closeout validation is failing while the closeout task remains open."
            blockers.append(f"Final done closeout blocked: {_summarize_gate_failure(gates_output)}"[:220])
            blockers = blockers[-8:]
        elif gates_ok is None:
            done_gate_status_value = "pending"
            next_task_text = "Resolve final done-closeout validation prerequisites."
            selection_reason = "Final done-closeout validation could not run yet while the closeout task remains open."
    if open_tasks_count == 0:
        blockers = []
    if open_tasks_count == 0:
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
    elif branch_error or (open_tasks_count > 0 and not isinstance(selected_task, dict)):
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
    graph_summary = build_runner_graph_artifacts(
        project_root=project_root,
        paths=paths,
        selected_task=selected_task if isinstance(selected_task, dict) else None,
        dirty_paths=dirty_paths,
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
    phase_started_at = (
        previous_phase_started_at
        if current_phase == previous_phase and previous_phase_started_at and not objective_changed
        else utc_now()
    )
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
        completed_recent=[] if objective_changed else [str(item).strip() for item in state.get("completed_recent", []) if str(item).strip()],
        last_iteration_summary="" if objective_changed else _as_text(state.get("last_iteration_summary")),
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
        model_profile=(
            _normalize_choice((selected_task or {}).get("model_profile"), TASK_MODEL_PROFILES)
            if isinstance(selected_task, dict)
            else None
        ),
        fanout_risk=(
            _normalize_choice((selected_task or {}).get("fanout_risk"), TASK_FANOUT_RISKS)
            if isinstance(selected_task, dict)
            else None
        ),
        touch_paths=(
            _normalize_text_list((selected_task or {}).get("touch_paths"), item_chars=220, max_items=24)
            if isinstance(selected_task, dict)
            else []
        ),
        validation_commands=(
            _normalize_text_list((selected_task or {}).get("validation_commands"), item_chars=220, max_items=8)
            if isinstance(selected_task, dict)
            else []
        ),
        coupling_notes=(
            _normalize_text_list((selected_task or {}).get("coupling_notes"), item_chars=220, max_items=8)
            if isinstance(selected_task, dict)
            else []
        ),
        parity_baseline_ref=_as_text(selected_parity_scope.get("parity_baseline_ref")) or None,
        parity_surface_ids=[
            str(item).strip()
            for item in selected_parity_scope.get("parity_surface_ids", [])
            if str(item).strip()
        ],
        parity_audit_mode=_as_text(selected_parity_scope.get("parity_audit_mode")) or None,
    )

    state = update_state(
        paths.state_file,
        state,
        status=status_value,
        current_step="",
        current_goal=objective_title,
        next_seam=next_task_text,
        next_seam_reason=selection_reason,
        next_task=next_task_text,
        next_task_reason=selection_reason,
        implementation_plan=implementation_plan,
        completed_recent=[] if objective_changed else [str(item).strip() for item in state.get("completed_recent", []) if str(item).strip()],
        last_iteration_summary="" if objective_changed else _as_text(state.get("last_iteration_summary")),
        objective_id=objective_id,
        next_seam_id=next_task_id,
        next_task_id=next_task_id,
        active_seam_id=active_seam_id,
        current_seam_id=active_seam_id,
        current_task_id=None,
        seam_selection_reason=selection_reason,
        task_selection_reason=selection_reason,
        project_root=str(project_root.resolve()),
        target_branch=target_branch,
        state_revision=state_revision,
        done_candidate=done_candidate_value,
        done_gate_status=done_gate_status_value,
        dod_status="done" if status_value == "done" else "in_progress",
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
        kanban_state=kanban_state,
    )
    _write_active_backlog_view(
        paths=paths,
        state=state,
        prd_payload=prd_payload,
        tasks_payload=tasks_payload,
        selected_task=selected_task if isinstance(selected_task, dict) else None,
        phase_goal=phase_goal,
        graph_summary=graph_summary,
        parity_scope=selected_parity_scope,
        kanban_state=kanban_state,
    )
    write_runner_status_snapshot(paths, state, kanban_state)
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
        tasks_payload=tasks_payload,
        task=selected_task if isinstance(selected_task, dict) else None,
        project_root=project_root,
        target_branch=target_branch,
        phase=current_phase,
        phase_goal=phase_goal,
        context_sources=context_sources,
        context_delta=context_delta,
        graph_summary=graph_summary,
        parity_scope=selected_parity_scope,
        kanban_state=kanban_state,
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
                "task_source": "orx_linear_project_issues",
                "kanban_enabled": True,
                "completion_policy": "tasks_done_and_gates_green",
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
            "error": "Runner is not prepared. Run /run_setup first to compile the objective bundle.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    objective_payload = read_json(paths.objective_json)
    seams_payload = read_json(paths.seams_json)
    gaps_payload = read_json(paths.gaps_json)
    if not isinstance(objective_payload, dict) or not isinstance(seams_payload, dict) or not isinstance(gaps_payload, dict):
        return {
            "ok": False,
            "error": (
                "Runner objective bundle is missing or invalid. "
                "Run /run_setup first to prepare OBJECTIVE.json, SEAMS.json, and GAPS.json."
            ),
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    objective_id = _as_text(objective_payload.get("objective_id")) or _as_text(state.get("objective_id")) or _new_objective_id()
    derived_seams_payload = _seams_payload_from_tasks_file(paths=paths, objective_id=objective_id)
    if isinstance(derived_seams_payload, dict):
        current_digest = json.dumps(seams_payload, sort_keys=True)
        derived_digest = json.dumps(derived_seams_payload, sort_keys=True)
        if current_digest != derived_digest:
            seams_payload = derived_seams_payload
            write_json(paths.seams_json, seams_payload)

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
                "Runner is not enabled. Run /run_setup and approve enablement before starting."
                f"{token_hint}"
            ),
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    if paths.stop_lock.exists():
        stop_meta = _read_lock_metadata(paths.stop_lock)
        source = _as_text(stop_meta.get("source"))
        if source == "runner_update_failure" and allow_refresh_repair:
            create_runner_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                approve_enable=None,
                project_root=resolved_root,
            )
            repaired = _inspect_runner_start_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=resolved_root,
                allow_refresh_repair=False,
            )
            if repaired.get("ok"):
                return repaired
            stop_lines = [f"requested_at={_as_text(stop_meta.get('requested_at')) or utc_now()}"]
            for key in ("source", "reason", "phase", "session"):
                value = _as_text(stop_meta.get(key))
                if value:
                    stop_lines.append(f"{key}={value}")
            paths.stop_lock.parent.mkdir(parents=True, exist_ok=True)
            paths.stop_lock.write_text("\n".join(stop_lines) + "\n", encoding="utf-8")
            return repaired
        if source == "runner_no_progress":
            error_text = (
                "Runner is paused because the last cycle made no durable progress. "
                "Run /run_setup again after narrowing the blocker before starting."
            )
        else:
            error_text = (
                "Runner stop lock is present. Clear the stop condition with /run_setup before starting."
            )
        reason = _as_text(stop_meta.get("reason"))
        if reason:
            error_text = f"{error_text} Reason: {reason}."
        return {
            "ok": False,
            "error": error_text,
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    prompt_error = _ensure_runner_prompt_install()
    if prompt_error:
        return {
            "ok": False,
            "error": prompt_error,
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
            "error": "Runner state is missing next-task info. Run /run_setup again.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    seams = seams_payload.get("seams", []) if isinstance(seams_payload, dict) else []
    matching_seam = None
    if isinstance(seams, list):
        for seam in seams:
            if not isinstance(seam, dict):
                continue
            if _as_text(seam.get("seam_id")) == next_task_id:
                matching_seam = seam
                break
    if matching_seam is None:
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
            "error": f"Runner seam {next_task_id} is missing from SEAMS.json. Run /run_setup again.",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    task_status = _as_text(matching_seam.get("status")).lower()
    if task_status == "blocked":
        blocked_reason = _as_text(matching_seam.get("blocked_reason"))
        blocked_suffix = f" Reason: {blocked_reason}." if blocked_reason else ""
        return {
            "ok": False,
            "error": f"Runner seam {next_task_id} is blocked.{blocked_suffix}",
            "project": project,
            "runner_id": runner_id,
            "project_root": str(resolved_root),
        }

    if task_status not in {"open", "in_progress"}:
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
            "error": f"Runner seam {next_task_id} is not startable (status={task_status or 'unknown'}).",
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
        "objective_file": str(paths.objective_json),
        "seams_file": str(paths.seams_json),
        "gaps_file": str(paths.gaps_json),
        "active_seam_id": _as_text(state.get("active_seam_id")) or next_task_id,
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
        state = load_state_snapshot(paths=paths, project=project, runner_id=runner_id)
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
    objective_payload, _ = _ensure_objective_payload(
        paths=paths,
        state=state,
        project=project,
        project_root=project_root,
        prd_payload=prd_payload,
    )

    if action == "show":
        return 0, json.dumps(objective_payload, indent=2, sort_keys=True)

    if action == "set":
        if not objective_id:
            return 1, "ERROR: --objective-id is required for --objective set"
        objective_payload["objective_id"] = objective_id.strip()
        objective_payload["updated_at"] = utc_now()
        prd_payload["objective_id"] = objective_id.strip()
        prd_payload["updated_at"] = utc_now()
        write_json(paths.objective_json, objective_payload)
        write_json(paths.prd_json, prd_payload)
        update_state(paths.state_file, state, objective_id=objective_id.strip())
        return 0, f"objective_updated={objective_id.strip()}"

    return 1, f"ERROR: unsupported --objective action: {action}"


def _handle_operator_command(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
    action: str,
    issue_url: str | None,
    issue_repo: str | None,
    issue_number: int | None,
    issue_title: str | None,
    run_id: str | None,
    reason: str | None,
    requested_by: str | None,
) -> tuple[int, str]:
    from .runner_control import RunnerControlPlane

    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    ensure_memory_dir(paths)
    state = load_or_init_state(paths=paths, project=project, runner_id=runner_id)
    kanban_state = load_or_init_kanban_state(paths=paths, project=project)
    control = RunnerControlPlane(paths)

    active_issue = kanban_state.get("active_issue") if isinstance(kanban_state.get("active_issue"), dict) else {}
    current_issue_url = _as_text((active_issue or {}).get("url"))

    if action == "status":
        return 0, json.dumps(
            {
                "control": control.describe(issue_url=current_issue_url),
                "runner_status": read_json(paths.runner_status_json) or {},
            },
            indent=2,
            sort_keys=True,
        )

    if action == "queue":
        return 0, json.dumps(
            {
                "control": control.describe(issue_url=current_issue_url),
                "action_queue": read_json(paths.action_queue_json) or {},
                "reconcile_result": read_json(paths.reconcile_result_json) or {},
            },
            indent=2,
            sort_keys=True,
        )

    target_issue_url = issue_url or current_issue_url
    if action in {"force", "skip"} and not target_issue_url:
        return 1, f"ERROR: --issue-url is required for --operator {action} when no active issue is selected"
    if action == "resume-run" and not run_id:
        return 1, "ERROR: --run-id is required for --operator resume-run"

    override_action = {
        "block": "mark_blocked",
        "resume-run": "resume_run",
        "override-split": "override_split",
    }.get(action, action.replace("-", "_"))

    requested = control.request_override(
        action=override_action,
        requested_by=requested_by or "runctl",
        reason=reason,
        issue_url=current_issue_url,
        target_issue={
            "url": target_issue_url,
            "repo": issue_repo,
            "number": issue_number,
            "title": issue_title,
        }
        if action in {"force", "skip"}
        else None,
        target_run_id=run_id if action == "resume-run" else None,
    )
    write_runner_status_snapshot(paths, state, kanban_state)
    return 0, json.dumps({"requested": requested, "control": control.describe(issue_url=target_issue_url)}, indent=2, sort_keys=True)


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
    parser.add_argument(
        "--operator",
        choices=["status", "queue", "pause", "resume", "force", "skip", "block", "resume-run", "override-split"],
        help="Operator command",
    )
    parser.add_argument("--project", help="Project under $DEV/Repos")
    parser.add_argument("project_arg", nargs="?", help="Project under $DEV/Repos (positional shorthand)")
    parser.add_argument(
        "--project-root",
        help="Explicit project root path (supports non-Repos worktrees)",
    )
    parser.add_argument("--runner-id", help="Runner id")
    parser.add_argument("--approve-enable", help="HIL token to approve --setup enablement")
    parser.add_argument("--confirm", help="HIL token to confirm --clear deletion")
    parser.add_argument("--issue-url", help="Issue URL for operator commands")
    parser.add_argument("--issue-repo", help="Issue repo for operator force/skip")
    parser.add_argument("--issue-number", type=int, help="Issue number for operator force/skip")
    parser.add_argument("--issue-title", help="Issue title for operator force/skip")
    parser.add_argument("--run-id", help="Run id for operator resume-run")
    parser.add_argument("--reason", help="Operator override reason")
    parser.add_argument("--requested-by", help="Operator identity for durable overrides")
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

    if args.task and (args.setup or args.clear or args.prepare_cycle or args.approve_enable or args.confirm):
        _err("ERROR: --task cannot be combined with --setup/--clear/--prepare-cycle/--approve-enable/--confirm")
        return 1
    if args.objective and (args.setup or args.clear or args.prepare_cycle or args.approve_enable or args.confirm):
        _err("ERROR: --objective cannot be combined with --setup/--clear/--prepare-cycle/--approve-enable/--confirm")
        return 1
    if args.operator and (args.setup or args.clear or args.prepare_cycle or args.approve_enable or args.confirm):
        _err("ERROR: --operator cannot be combined with --setup/--clear/--prepare-cycle/--approve-enable/--confirm")
        return 1
    selected_modes = [
        name
        for name, enabled in (
            ("task", args.task),
            ("objective", args.objective),
            ("operator", args.operator),
        )
        if enabled
    ]
    if len(selected_modes) > 1:
        _err("ERROR: choose only one of --task, --objective, or --operator")
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

    if args.operator:
        code, output = _handle_operator_command(
            dev=args.dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
            action=args.operator,
            issue_url=args.issue_url,
            issue_repo=args.issue_repo,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            run_id=args.run_id,
            reason=args.reason,
            requested_by=args.requested_by,
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
        tasks_payload = read_json(paths.tasks_json) or {}
        current_snapshot = _capture_progress_snapshot(
            state=state,
            project_root=project_root,
            tasks_payload=tasks_payload if isinstance(tasks_payload, dict) else None,
        )
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
