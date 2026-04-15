"""Command dispatch for cl/clls session manager."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
import json
from pathlib import Path

from .menu import SessionMenu
from .runner_graph import run_runner_build_graph_command
from .runctl import (
    create_runner_state,
    ensure_runner_prompt_install,
    inspect_runner_start_state,
    resolve_target_project_root,
)
from .runner_loop import (
    build_runner_paths,
    ensure_gates_file,
    make_codex_interactive_runner_script,
    resolve_runner_profile,
    run_runner_archive,
    run_interactive_runner_controller,
    run_loop_worker,
    run_runner_profile,
)
from .runner_state import build_runner_state_paths_for_root, coerce_runner_phase, read_json, write_json
from .tmux import TmuxClient


def _repo_home() -> Path:
    """Resolve standalone tmux-codex repo root."""
    override = os.environ.get("TMUX_CLI_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def get_tmux_config() -> Path | None:
    """Get tmux config path from standalone repo."""
    config = _repo_home() / "config" / "tmux" / "tmux.conf"
    if config.exists():
        return config
    return None


def detect_session_type(args: list[str]) -> tuple[str, str]:
    """Detect session type from args. Returns (prefix, display_name)."""
    if not args:
        return "codex", "session"
    normalized_args = [arg.lower() for arg in args]
    if (
        "/run" in normalized_args
        or "/prompts:run_setup" in normalized_args
        or "/prompts:run_execute" in normalized_args
        or "/prompts:run_govern" in normalized_args
    ):
        return "run", "run worker"
    args_str = " ".join(normalized_args)
    if "/integrate" in args_str:
        return "int", "integrator"
    if "/spec" in args_str:
        return "add", "add session"
    return "codex", "session"


def session_profile_overrides(args: list[str]) -> list[str]:
    """Return explicit high-end overrides for planning/execution sessions.

    Raw Codex config can carry a cheap default model, but wrapper-launched
    execution flows should still force the expensive profile up front.
    """
    if not args:
        return []

    normalized_args = [arg.lower() for arg in args]
    joined = " ".join(normalized_args)
    execution_markers = (
        "/plan",
        "/kanban",
        "/continuous-workflow",
        "/continuous_workflow",
        "continuous workflow",
        "continue workflow",
        "run continuous workflow",
        "continue kanban",
        "run kanban",
        "/run",
        "/prompts:run_",
        "/integrate",
        "/spec",
        "/refactor",
        "/commit-main",
        "/enhance",
        "/prune",
        "/review",
    )
    if not any(marker in joined for marker in execution_markers):
        return []

    return [
        "-c",
        'model="gpt-5.4"',
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        'plan_mode_reasoning_effort="high"',
    ]


def create_session(args: list[str]) -> None:
    """Create new Codex session and attach."""
    prompt_error = ensure_runner_prompt_install()
    if prompt_error:
        print("Prompt install check failed")
        print(f"Error: {prompt_error}")
        return

    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    prefix, display_name = detect_session_type(args)
    sess_name = tmux.next_session_name(prefix=prefix)

    dev = os.environ.get("DEV", "/Users/jian/Dev")
    workdir = os.getcwd()
    codex_parts = [
        "codex",
        "--search",
        "--dangerously-bypass-approvals-and-sandbox",
        *session_profile_overrides(args),
        *args,
    ]
    cmd = (
        f'cd "{workdir}" && '
        f"{shlex.join(codex_parts)}; "
        "clear; exec zsh -l"
    )

    print(f"Creating {display_name}...", end="", flush=True)
    try:
        tmux.create_session(sess_name, cmd)
    except RuntimeError as e:
        print(" failed")
        print(f"Error: {e}")
        return
    print(" ready")
    tmux.attach(sess_name)


def _resolve_list_selector(tmux: TmuxClient, selector: str) -> str | None:
    """Resolve a direct `cl ls` selector to a session name.

    Numeric selectors map to regular codex sessions using the same 1-based
    numbering shown in the interactive menu. Single letters map to runner
    sessions using the same a-z labels shown in the menu.
    """
    sessions = tmux.list_sessions(prefix="codex")
    regular_sessions = [sess for sess in sessions if not sess.startswith("runner-")]
    runner_sessions = [sess for sess in sessions if sess.startswith("runner-")]

    normalized = selector.strip()
    if normalized.isdigit() and normalized != "0":
        index = int(normalized) - 1
        if 0 <= index < len(regular_sessions):
            return regular_sessions[index]
        return None

    if len(normalized) == 1 and normalized.isalpha():
        index = ord(normalized.lower()) - ord("a")
        if 0 <= index < len(runner_sessions):
            return runner_sessions[index]
        return None

    return None


def list_sessions(selector: str | None = None) -> None:
    """Show interactive session menu or attach directly by selector."""
    dev = os.environ.get("DEV", "/Users/jian/Dev")
    os.chdir(dev)

    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    if selector is not None:
        session_name = _resolve_list_selector(tmux, selector)
        if session_name is None:
            print(f"Session selector not found: {selector}")
            return
        tmux.attach(session_name)
        return

    menu = SessionMenu(tmux)
    menu.run()


def _print_loop_usage() -> None:
    print(
        "Usage: cl loop <project> "
        "[--runner-id <id>] [--complexity <low|med|high|xhigh>] [--model <provider/model>]"
    )
    print(
        "       cl loop-bg <project> "
        "[--runner-id <id>] [--complexity <low|med|high|xhigh>] [--model <provider/model>] "
        "[--project-root <absolute_path>]"
    )


def _print_stop_usage() -> None:
    print("Usage: cl stop <project> [--runner-id <id>]")
    print("Alias: cl k <project> [--runner-id <id>]")
    print("Extra aliases: cl ka <project>, cl kb <project>, cl k* [<project>]")


def _ensure_runner_ready_for_start(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> bool:
    """Fail fast if the runner has not already been prepared via /prompts:run_setup."""
    result = inspect_runner_start_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )
    if result.get("ok"):
        return True

    print(f"Runner start blocked for {project}: {result.get('error', 'unknown error')}")
    print("Prepare the runner first with /prompts:run_setup, then start it with cl -> r=runner.")
    return False


def _auto_prepare_runner_for_start(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> bool:
    setup_result = create_runner_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        approve_enable=None,
        project_root=project_root,
    )
    if not setup_result.get("ok"):
        print(
            f"Runner auto-setup failed for {project}: {setup_result.get('error', 'unknown error')}"
        )
        return False

    token = str(setup_result.get("enable_token") or "").strip()
    if setup_result.get("enable_pending_file") and token:
        approve_result = create_runner_state(
            dev=dev,
            project=project,
            runner_id=runner_id,
            approve_enable=token,
            project_root=project_root,
        )
        if not approve_result.get("ok"):
            print(
                f"Runner enable approval failed for {project}: {approve_result.get('error', 'unknown error')}"
            )
            return False
    return _ensure_runner_ready_for_start(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )


def _github_repo_from_origin(project_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    origin = completed.stdout.strip()
    if not origin:
        return None

    match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/.]+)(?:\.git)?$", origin)
    if not match:
        return None
    return match.group("repo")


def _current_git_branch(project_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    branch = completed.stdout.strip()
    return branch or None


def _kanban_helper_path() -> Path:
    return Path.home() / ".codex" / "skills" / "kanban" / "scripts" / "github_project_issue_flow.py"


def _query_kanban_issue_item(project_root: Path, repo: str, command: str) -> dict[str, object] | None:
    helper = _kanban_helper_path()
    if not helper.exists():
        return None
    try:
        completed = subprocess.run(
            ["python3", str(helper), command, "--repo", repo],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return None

    if not payload.get("found"):
        return None
    item = payload.get("item")
    return item if isinstance(item, dict) else None


def _list_kanban_board_items(project_root: Path) -> list[dict[str, object]]:
    helper = _kanban_helper_path()
    if not helper.exists():
        return []
    try:
        completed = subprocess.run(
            ["python3", str(helper), "list"],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    try:
        payload = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _load_issue_thread(repo: str, issue_number: object, project_root: Path) -> dict[str, object] | None:
    if not repo or issue_number in (None, ""):
        return None
    try:
        completed = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "body,comments,number,title,url",
            ],
            cwd=str(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _sync_selected_issue_snapshot(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
    item: dict[str, object],
) -> None:
    from .runner_control import RunnerControlPlane

    repo = str(item.get("repo") or "").strip()
    issue_thread = _load_issue_thread(repo, item.get("number"), project_root) if repo else None
    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    RunnerControlPlane(paths).import_github_item(
        item=item,
        issue=issue_thread,
        issue_thread=issue_thread,
    )


def _sync_board_issue_snapshots(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> list[dict[str, object]]:
    from .runner_control import RunnerControlPlane

    items = _list_kanban_board_items(project_root)
    for item in items:
        repo = str(item.get("repo") or "").strip()
        if not repo:
            continue
        issue_thread = _load_issue_thread(repo, item.get("number"), project_root)
        paths = build_runner_state_paths_for_root(
            project_root=project_root,
            dev=dev,
            project=project,
            runner_id=runner_id,
        )
        RunnerControlPlane(paths).import_github_item(
            item=item,
            issue=issue_thread,
            issue_thread=issue_thread,
        )
    return items


def _board_status(item: dict[str, object] | None) -> str:
    if not isinstance(item, dict):
        return ""
    fields = item.get("fields")
    if isinstance(fields, dict):
        return str(fields.get("Status") or item.get("workflowState") or item.get("state") or "").strip()
    return str(item.get("workflowState") or item.get("state") or "").strip()


def _board_item_is_runtime_actionable(item: dict[str, object] | None) -> bool:
    return _board_status(item) in {"", "Inbox", "Ready", "In Progress"}


def _active_issue_requires_runtime_reset(*, paths, issue_url: str | None) -> bool:
    issue_url = str(issue_url or "").strip()
    if not issue_url:
        return False
    runner_status = read_json(paths.runner_status_json)
    if not isinstance(runner_status, dict):
        return False
    runtime_phase = str(runner_status.get("current_phase") or "").strip().lower()
    done_gate_status = str(runner_status.get("done_gate_status") or "").strip().lower()
    if runtime_phase not in {"closeout", "done"} and done_gate_status != "failed":
        return False

    from .runner_control import RunnerControlPlane

    control = RunnerControlPlane(paths)
    conditions = control._issue_conditions(issue_url)
    ready_for_execution = conditions.get("ready_for_execution") or {}
    planning_satisfied = conditions.get("planning_satisfied") or {}
    ready_reason = str(ready_for_execution.get("reason") or "").strip().lower()
    planning_reason = str(planning_satisfied.get("reason") or "").strip().lower()
    planning_failed = planning_satisfied.get("status") in {0, False}
    return ready_reason == "enhance_required" or (planning_failed and planning_reason == "phase_not_advanced")


def _reset_runner_runtime_for_reselection(*, paths) -> None:
    state_data = read_json(paths.state_file)
    if isinstance(state_data, dict) and state_data:
        state_data["current_phase"] = "discover"
        state_data["phase_status"] = "active"
        state_data["done_gate_status"] = "pending"
        state_data["done_candidate"] = False
        state_data["next_task"] = "Seed the first concrete task slice from the active objective."
        state_data["next_task_reason"] = "A new active issue was selected after stale closeout recovery."
        write_json(paths.state_file, state_data)

    status_data = read_json(paths.runner_status_json)
    if isinstance(status_data, dict) and status_data:
        status_data["current_phase"] = "discover"
        status_data["phase_status"] = "active"
        status_data["done_gate_status"] = "pending"
        status_data["next_task"] = "Seed the first concrete task slice from the active objective."
        status_data["next_reason"] = "A new active issue was selected after stale closeout recovery."
        write_json(paths.runner_status_json, status_data)

    exec_context = read_json(paths.exec_context_json)
    if isinstance(exec_context, dict) and exec_context:
        exec_context["phase"] = "discover"
        write_json(paths.exec_context_json, exec_context)


def _seed_kanban_state_for_background_runner(
    *,
    dev: str,
    project: str,
    runner_id: str,
    project_root: Path,
) -> dict[str, object] | None:
    paths = build_runner_state_paths_for_root(
        project_root=project_root,
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    kanban_state = read_json(paths.kanban_state_json)
    if not isinstance(kanban_state, dict):
        kanban_state = {"project": project, "mode": "ticket_native", "version": 1}
    existing_active_issue = (
        kanban_state.get("active_issue")
        if isinstance(kanban_state.get("active_issue"), dict)
        else None
    )
    existing_active_issue_url = str((existing_active_issue or {}).get("url") or "").strip() or None

    active_checkout = kanban_state.get("active_checkout")
    if not isinstance(active_checkout, dict):
        active_checkout = {}
        kanban_state["active_checkout"] = active_checkout
    active_checkout["repo_root"] = str(project_root)
    active_checkout["worktree"] = str(project_root)
    active_checkout["branch"] = _current_git_branch(project_root)

    loop_state = kanban_state.get("loop")
    if not isinstance(loop_state, dict):
        loop_state = {}
        kanban_state["loop"] = loop_state
    loop_state["continue_until"] = "board_complete_or_all_blocked"

    board = kanban_state.get("board")
    if not isinstance(board, dict):
        board = {}
        kanban_state["board"] = board

    board_items = _sync_board_issue_snapshots(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )
    board["snapshot_count"] = len(board_items)
    board_items_by_url = {
        str(item.get("url") or "").strip(): item
        for item in board_items
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    }

    existing_board_item = board_items_by_url.get(existing_active_issue_url or "")
    runtime_reset_existing_issue = False
    if existing_active_issue_url and existing_board_item and not _board_item_is_runtime_actionable(existing_board_item):
        kanban_state["active_issue"] = None
        existing_active_issue = None
        existing_active_issue_url = None
    if existing_active_issue_url and _active_issue_requires_runtime_reset(paths=paths, issue_url=existing_active_issue_url):
        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"
        existing_active_issue = None
        existing_active_issue_url = None
        loop_state["resume_source"] = "runtime_recovery_reset"
        runtime_reset_existing_issue = True
        _reset_runner_runtime_for_reselection(paths=paths)

    repo = _github_repo_from_origin(project_root)
    item = None
    if repo:
        if not runtime_reset_existing_issue:
            item = _query_kanban_issue_item(project_root, repo, "active")
            if item and not _board_item_is_runtime_actionable(board_items_by_url.get(str(item.get("url") or "").strip())):
                item = None
        if item is None:
            item = _query_kanban_issue_item(project_root, repo, "next")

    if item and not existing_active_issue_url:
        _sync_selected_issue_snapshot(
            dev=dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
            item=item,
        )
        kanban_state["active_issue"] = {
            "repo": str(item.get("repo") or "").strip() or None,
            "number": item.get("number"),
            "title": str(item.get("title") or "").strip() or None,
            "url": str(item.get("url") or "").strip() or None,
        }
        status_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        board["last_known_status"] = str(status_fields.get("Status") or item.get("workflowState") or item.get("state") or "").strip() or None
        kanban_state["phase"] = "executing"
        loop_state["resume_source"] = "github_project_issue_flow"
    elif item:
        status_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        board["last_known_status"] = str(status_fields.get("Status") or item.get("workflowState") or item.get("state") or "").strip() or None
    elif existing_board_item:
        board["last_known_status"] = _board_status(existing_board_item) or None
    else:
        kanban_state["active_issue"] = None
        kanban_state["phase"] = "selecting"

    kanban_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(paths.kanban_state_json, kanban_state)
    return item


def parse_loop_args(args: list[str]) -> dict[str, str]:
    """Parse loop command options."""
    project: str | None = None
    runner_id: str | None = None
    complexity = "med"
    model_override: str | None = None
    project_root: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg.startswith("--runner-id="):
            runner_id = arg.split("=", 1)[1]
        elif arg == "--runner-id":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --runner-id")
            runner_id = args[i]
        elif arg.startswith("--complexity="):
            complexity = arg.split("=", 1)[1].lower()
        elif arg == "--complexity":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --complexity")
            complexity = args[i].lower()
        elif arg.startswith("--model="):
            model_override = arg.split("=", 1)[1]
        elif arg == "--model":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --model")
            model_override = args[i]
        elif arg.startswith("--project-root="):
            project_root = arg.split("=", 1)[1]
        elif arg == "--project-root":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --project-root")
            project_root = args[i]
        elif arg.startswith("-"):
            raise ValueError(f"Unknown option: {arg}")
        else:
            if project is not None:
                raise ValueError("Only one project argument is allowed")
            project = arg

        i += 1

    if not project:
        raise ValueError("Project is required")

    if runner_id and runner_id not in {"main", "default"}:
        raise ValueError("Single-runner mode: omit --runner-id or use --runner-id main")
    resolved_runner_id = "main"

    model, reasoning_effort = resolve_runner_profile(complexity, model_override)

    return {
        "project": project,
        "runner_id": resolved_runner_id,
        "complexity": complexity,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "project_root": project_root or "",
    }


def parse_stop_args(args: list[str]) -> dict[str, str]:
    """Parse stop command options."""
    project: str | None = None
    runner_id: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg.startswith("--runner-id="):
            runner_id = arg.split("=", 1)[1]
        elif arg == "--runner-id":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --runner-id")
            runner_id = args[i]
        elif arg.startswith("-"):
            raise ValueError(f"Unknown option: {arg}")
        else:
            if project is not None:
                raise ValueError("Only one project argument is allowed")
            project = arg

        i += 1

    if not project:
        raise ValueError("Project is required")
    if runner_id and runner_id not in {"main", "default"}:
        raise ValueError("Single-runner mode: omit --runner-id or use --runner-id main")

    return {
        "project": project,
        "runner_id": "main",
    }


def _prepare_loop_runner(
    dev: str,
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    project_root: Path,
):
    """Build session name, paths, and script for one interactive CLI runner."""
    session_name = f"runner-{project}"
    paths = build_runner_paths(dev=dev, project=project, runner_id=runner_id, project_root=project_root)
    script = make_codex_interactive_runner_script(
        dev=dev,
        project=project,
        runner_id=runner_id,
        model=model,
        reasoning_effort=reasoning_effort,
        paths=paths,
    )
    return session_name, paths, script


def start_loop_session(
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    *,
    project_root_override: Path | None = None,
    attach: bool = True,
    auto_setup: bool = False,
) -> None:
    """Create a Codex CLI runner, optionally attaching to tmux."""
    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    dev = os.environ.get("DEV", "/Users/jian/Dev")
    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root_override=project_root_override,
    )
    if not project_root.exists():
        print(f"Missing project directory: {project_root}")
        return

    gates_path, created_now = ensure_gates_file(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )
    if created_now:
        print(f"Created gates template: {gates_path}")

    ready = (
        _auto_prepare_runner_for_start(
            dev=dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
        )
        if auto_setup
        else _ensure_runner_ready_for_start(
            dev=dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
        )
    )
    if not ready:
        return

    selected_issue = _seed_kanban_state_for_background_runner(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )

    session_name, paths, script = _prepare_loop_runner(
        dev=dev,
        project=project,
        runner_id=runner_id,
        model=model,
        reasoning_effort=reasoning_effort,
        project_root=project_root,
    )

    existing = tmux.list_sessions()
    if session_name in existing:
        print(f"Runner already exists: {session_name}")
        print("Restarting existing runner to apply current launcher...")
        if not tmux.kill_session(session_name):
            print(f"Error: failed to stop existing session {session_name}")
            return

    print(f"Starting runner {session_name}...", end="", flush=True)
    try:
        tmux.create_session(session_name, script)
    except RuntimeError as e:
        print(" failed")
        print(f"Error: {e}")
        return

    print(" ready")
    initial_phase = "discover"
    state_paths = getattr(paths, "state", None)
    exec_context_path = getattr(state_paths, "exec_context_json", None)
    state_file_path = getattr(state_paths, "state_file", None)
    if exec_context_path is not None or state_file_path is not None:
        exec_context = read_json(exec_context_path) if exec_context_path is not None else {}
        state_data = read_json(state_file_path) if state_file_path is not None else {}
        if not isinstance(exec_context, dict):
            exec_context = {}
        if not isinstance(state_data, dict):
            state_data = {}
        initial_phase = coerce_runner_phase(
            exec_context.get("phase") or state_data.get("current_phase"),
            default="discover",
        )
    print(f"  Project:   {project}")
    print(f"  Runner ID: {runner_id}")
    print(f"  Default model:  {model}")
    print(f"  Default effort: {reasoning_effort}")
    print("  Task routing:   per-task (`mini` => cheap model, `high` => gpt-5.4 high)")
    print("  Mode:      interactive-cli")
    print(f"  Phase:     {initial_phase}")
    print(f"  Session:   {session_name}")
    print("  Driver:    __runner-controller")
    print(f"  Root:      {project_root}")
    if selected_issue:
        issue_number = selected_issue.get("number")
        issue_title = str(selected_issue.get("title") or "").strip()
        print(f"  Issue:     #{issue_number} {issue_title}")
    print(f"  Done lock: {paths.complete_lock}")
    print(f"  Stop lock: {paths.stop_file}")
    print()

    if attach:
        tmux.attach(session_name)


def create_loop_session(
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
) -> None:
    """Create and attach to interactive Codex CLI runner."""
    start_loop_session(
        project=project,
        runner_id=runner_id,
        model=model,
        reasoning_effort=reasoning_effort,
        attach=True,
        auto_setup=False,
    )


def stop_loop_session(project: str, runner_id: str) -> None:
    """Stop a running loop session by writing stop lock and killing session."""
    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    dev = os.environ.get("DEV", "/Users/jian/Dev")
    project_root = resolve_target_project_root(
        dev=dev,
        project=project,
        runner_id=runner_id,
    )
    if not project_root.exists():
        print(f"Missing project directory: {project_root}")
        return

    paths = build_runner_paths(dev=dev, project=project, runner_id=runner_id)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.stop_file.write_text(
        f"requested_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"project={project}\n"
        f"runner_id={runner_id}\n"
    )

    session_name = f"runner-{project}"
    existing = tmux.list_sessions()
    killed = False
    if session_name in existing:
        killed = tmux.kill_session(session_name)
    paths.active_lock.unlink(missing_ok=True)
    paths.stop_file.unlink(missing_ok=True)
    if killed:
        print(f"Stopped runner session: {session_name}")
    else:
        print(f"Runner session not active: {session_name}")
    print("Transient locks cleared: RUNNER_STOP.lock, RUNNER_ACTIVE.lock")
    print(f"Root: {project_root}")


def stop_all_loop_sessions() -> None:
    """Stop all active runner sessions and write stop locks."""
    config = get_tmux_config()
    tmux = TmuxClient(config=config)
    dev = os.environ.get("DEV", "/Users/jian/Dev")

    sessions = [sess for sess in tmux.list_sessions() if sess.startswith("runner-")]
    projects: set[str] = set()
    for session_name in sessions:
        project = session_name.replace("runner-", "", 1).strip()
        if project:
            projects.add(project)

    if not sessions:
        print("No active runner sessions found.")
        return

    for project in sorted(projects):
        paths = build_runner_paths(dev=dev, project=project, runner_id="main")
        paths.memory_dir.mkdir(parents=True, exist_ok=True)
        paths.stop_file.write_text(
            f"requested_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"project={project}\n"
            "runner_id=main\n"
            "source=cl_k_star\n"
        )

    for session_name in sessions:
        tmux.kill_session(session_name)
        print(f"Stopped tmux session: {session_name}")

    for project in sorted(projects):
        paths = build_runner_paths(dev=dev, project=project, runner_id="main")
        paths.active_lock.unlink(missing_ok=True)
        paths.stop_file.unlink(missing_ok=True)
    print("Transient locks cleared for all discovered runner projects.")


def spawn_all_loop_runners() -> None:
    """Spawn one default loop runner per project with .memory directory."""
    dev = os.environ.get("DEV", "/Users/jian/Dev")
    repos_base = Path(dev) / "Repos"

    if not repos_base.exists():
        print("No Repos directory found")
        return

    projects = [d.name for d in repos_base.iterdir() if d.is_dir() and (d / ".memory").exists()]
    if not projects:
        print("No projects with .memory found")
        return

    config = get_tmux_config()
    tmux = TmuxClient(config=config)
    existing = set(tmux.list_sessions())

    spawned = []
    skipped = []

    for project in sorted(projects):
        runner_id = "main"
        model, reasoning_effort = resolve_runner_profile("med", None)
        project_root = resolve_target_project_root(
            dev=dev,
            project=project,
            runner_id=runner_id,
        )
        gates_path, created_now = ensure_gates_file(
            dev=dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
        )
        if created_now:
            print(f"Created gates template for {project}: {gates_path}")

        if not _ensure_runner_ready_for_start(
            dev=dev,
            project=project,
            runner_id=runner_id,
            project_root=project_root,
        ):
            skipped.append((project, "runner not prepared"))
            continue

        session_name, _, script = _prepare_loop_runner(
            dev=dev,
            project=project,
            runner_id=runner_id,
            model=model,
            reasoning_effort=reasoning_effort,
        )

        if session_name in existing:
            skipped.append(session_name)
            continue

        print(f"Spawning {session_name}...", end="", flush=True)
        try:
            tmux.create_session(session_name, script)
        except RuntimeError as e:
            print(" failed")
            print(f"  Error: {e}")
            continue
        print(" ready")
        spawned.append(session_name)

    print()
    if spawned:
        print("Spawned:")
        for sess in spawned:
            print(f"  - {sess}")
    if skipped:
        print("Skipped:")
        for sess in skipped:
            print(f"  - {sess}")


def main() -> None:
    """Main entry point."""
    args = sys.argv[1:]

    # Legacy internal worker commands retained for compatibility only.
    # Public runner entrypoints launch the interactive CLI runner.
    if args and args[0] == "__runner-loop":
        raise SystemExit(run_loop_worker(args[1:]))
    if args and args[0] == "__runner-profile":
        raise SystemExit(run_runner_profile(args[1:]))
    if args and args[0] == "__runner-archive":
        raise SystemExit(run_runner_archive(args[1:]))
    if args and args[0] == "__runner-build-graph":
        if len(args) < 2:
            raise SystemExit("Usage: __runner-build-graph --project-root <root> [--runner-id main]")
        project_root: Path | None = None
        runner_id = "main"
        idx = 1
        while idx < len(args):
            arg = args[idx]
            if arg == "--project-root":
                idx += 1
                if idx >= len(args):
                    raise SystemExit("Missing value for --project-root")
                project_root = Path(args[idx]).expanduser().resolve()
            elif arg.startswith("--project-root="):
                project_root = Path(arg.split("=", 1)[1]).expanduser().resolve()
            elif arg == "--runner-id":
                idx += 1
                if idx >= len(args):
                    raise SystemExit("Missing value for --runner-id")
                runner_id = args[idx]
            elif arg.startswith("--runner-id="):
                runner_id = arg.split("=", 1)[1]
            else:
                raise SystemExit(f"Unknown option: {arg}")
            idx += 1
        if project_root is None:
            raise SystemExit("Missing --project-root")
        dev = os.environ.get("DEV", "/Users/jian/Dev")
        paths = build_runner_state_paths_for_root(
            project_root=project_root,
            dev=dev,
            project=project_root.name,
            runner_id=runner_id or "main",
        )
        seams_payload = read_json(paths.seams_json) or {}
        state = read_json(paths.state_file) or {}
        selected_task_id = str(state.get("active_seam_id") or state.get("next_task_id") or "").strip()
        selected_task = None
        for index, raw in enumerate(seams_payload.get("seams", [])):
            if isinstance(raw, dict) and str(raw.get("seam_id") or "").strip() == selected_task_id:
                selected_task = {
                    "task_id": str(raw.get("seam_id") or "").strip(),
                    "title": str(raw.get("title") or raw.get("owner_problem") or "").strip(),
                    "status": str(raw.get("status") or "").strip(),
                    "touch_paths": raw.get("touch_paths") or raw.get("write_set") or [],
                    "coupling_notes": raw.get("coupling_notes") or [],
                    "fanout_risk": raw.get("fanout_risk"),
                    "model_profile": raw.get("model_profile"),
                    "deprecation_phase": raw.get("deprecation_phase"),
                    "priority": raw.get("priority") or "p1",
                    "updated_at": raw.get("updated_at"),
                }
                break
        print(
            json.dumps(
                run_runner_build_graph_command(
                    project_root=project_root,
                    paths=paths,
                    selected_task=selected_task,
                )
            )
        )
        raise SystemExit(0)
    if args and args[0] == "__runner-controller":
        raise SystemExit(run_interactive_runner_controller(args[1:]))

    if not args:
        list_sessions()
        return

    if args[0] == "ls":
        if len(args) > 2:
            raise SystemExit("Usage: cl ls [selector]")
        list_sessions(args[1] if len(args) == 2 else None)
        return

    if args[0] in ("loop", "runner", "run", "r", "loop-bg"):
        loop_args = args[1:]
        if loop_args and loop_args[0] in ("-h", "--help", "help"):
            _print_loop_usage()
            return
        if args[0] != "loop-bg" and loop_args and loop_args[0] in ("--all", "all"):
            spawn_all_loop_runners()
            return

        try:
            parsed = parse_loop_args(loop_args)
        except ValueError as e:
            print(f"Error: {e}")
            _print_loop_usage()
            return

        if args[0] == "loop-bg":
            project_root = parsed.get("project_root") or None
            start_loop_session(
                project=parsed["project"],
                runner_id=parsed["runner_id"],
                model=parsed["model"],
                reasoning_effort=parsed["reasoning_effort"],
                project_root_override=Path(project_root).expanduser().resolve()
                if project_root
                else None,
                attach=False,
                auto_setup=True,
            )
        else:
            create_loop_session(
                project=parsed["project"],
                runner_id=parsed["runner_id"],
                model=parsed["model"],
                reasoning_effort=parsed["reasoning_effort"],
            )
        return

    if args[0] in ("stop", "k") or re.fullmatch(r"k[a-z*]", args[0] or ""):
        stop_args = args[1:]
        if args[0] == "k*" and not stop_args:
            stop_all_loop_sessions()
            return
        if stop_args and stop_args[0] in ("-h", "--help", "help"):
            _print_stop_usage()
            return
        try:
            parsed = parse_stop_args(stop_args)
        except ValueError as e:
            print(f"Error: {e}")
            _print_stop_usage()
            return
        stop_loop_session(
            project=parsed["project"],
            runner_id=parsed["runner_id"],
        )
        return

    if args[0] in ("-h", "--help", "help"):
        print("Usage:")
        print("  cl                    # interactive session menu")
        print("  cl ls [selector]      # list sessions or attach directly (e.g. cl ls 1, cl ls a)")
        print(
            "  cl loop <project> [--runner-id <id>] [--complexity <low|med|high|xhigh>] "
            "[--model <provider/model>]"
        )
        print(
            "  cl loop-bg <project> [--runner-id <id>] [--complexity <low|med|high|xhigh>] "
            "[--model <provider/model>] [--project-root <absolute_path>]"
        )
        print("  cl runner <project>   # alias of loop")
        print("  cl stop <project>     # stop runner (alias: cl k <project>)")
        print("  cl ka <project>       # stop runner alias")
        print("  cl kb <project>       # stop runner alias")
        print("  cl k*                 # stop all runner sessions")
        return

    escaped_args = [shlex.quote(a) for a in args]
    create_session(escaped_args)


if __name__ == "__main__":
    main()
