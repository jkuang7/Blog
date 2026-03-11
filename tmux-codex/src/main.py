"""Command dispatch for cl/clls session manager."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from .menu import SessionMenu
from .runctl import create_runner_state, resolve_target_project_root
from .runner_loop import (
    build_runner_paths,
    ensure_gates_file,
    make_codex_chat_loop_script,
    make_codex_exec_loop_script,
    resolve_runner_profile,
    run_loop_worker,
)
from .runner_state import build_phase_prompt_commands, coerce_runner_phase, read_json
from .runner_watchdog import spawn_runner_watchdog
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
    args_str = " ".join(args).lower()
    phase_prompts = ("runner-discover", "runner-implement", "runner-verify", "runner-closeout", "runner-cycle")
    if any(f"/prompts:{prompt_name}" in args_str for prompt_name in phase_prompts) or "/prompts:run" in args_str or "/run" in args_str:
        return "run", "run worker"
    if "/integrate" in args_str:
        return "int", "integrator"
    if "/spec" in args_str:
        return "add", "add session"
    return "codex", "session"


def create_session(args: list[str]) -> None:
    """Create new Codex session and attach."""
    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    prefix, display_name = detect_session_type(args)
    sess_name = tmux.next_session_name(prefix=prefix)

    dev = os.environ.get("DEV", "/Users/jian/Dev")
    workdir = os.getcwd()
    extra_args = " ".join(args) if args else ""
    cmd = (
        f'cd "{workdir}" && '
        f'codex --search --dangerously-bypass-approvals-and-sandbox {extra_args}; '
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


def list_sessions() -> None:
    """Show interactive session menu."""
    dev = os.environ.get("DEV", "/Users/jian/Dev")
    os.chdir(dev)

    config = get_tmux_config()
    tmux = TmuxClient(config=config)

    menu = SessionMenu(tmux)
    menu.run()


def _print_loop_usage() -> None:
    print(
        "Usage: cl loop <project> "
        "[--runner-id <id>] [--complexity <low|med|high|xhigh>] [--model <provider/model>] "
        "[--hil-mode <setup-only>] [--runner-mode <interactive-watchdog|exec>]"
    )


def _print_stop_usage() -> None:
    print("Usage: cl stop <project> [--runner-id <id>]")
    print("Alias: cl k <project> [--runner-id <id>]")
    print("Extra aliases: cl ka <project>, cl kb <project>, cl k* [<project>]")


def _extract_cli_option(command: str, option: str) -> str | None:
    """Extract one CLI option value from a process command line."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        if token.startswith(f"{option}="):
            return token.split("=", 1)[1]
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _iter_watchdog_processes() -> list[tuple[int, str]]:
    """List active runner watchdog processes as (pid, command)."""
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    processes: list[tuple[int, str]] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        command = match.group(2)
        if "src.runner_watchdog" not in command:
            continue
        processes.append((pid, command))
    return processes


def _find_runner_watchdogs(project: str | None = None, runner_id: str | None = None) -> list[tuple[int, str]]:
    """Filter watchdog processes by optional project/runner scope."""
    matches: list[tuple[int, str]] = []
    for pid, command in _iter_watchdog_processes():
        cmd_project = _extract_cli_option(command, "--project")
        cmd_runner_id = _extract_cli_option(command, "--runner-id") or "main"
        cmd_session = _extract_cli_option(command, "--session") or ""
        if project and cmd_project != project and cmd_session != f"runner-{project}":
            continue
        if runner_id and cmd_runner_id != runner_id:
            continue
        matches.append((pid, command))
    return matches


def _pid_ps_field(pid: int, field: str) -> str | None:
    """Read one ps field value for a pid."""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", f"{field}="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _pid_process_state(pid: int) -> str | None:
    """Return process state string from ps stat (e.g. R, S, Z)."""
    return _pid_ps_field(pid, "stat")


def _pid_parent_pid(pid: int) -> int | None:
    raw = _pid_ps_field(pid, "ppid")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _pid_command(pid: int) -> str | None:
    return _pid_ps_field(pid, "command")


def _best_effort_reap_zombie(pid: int) -> bool:
    """Try to unblock zombie reap by terminating a watchdog parent process."""
    parent_pid = _pid_parent_pid(pid)
    if parent_pid is None or parent_pid <= 1 or parent_pid == os.getpid():
        return False
    parent_cmd = (_pid_command(parent_pid) or "").strip()
    if "src.runner_watchdog" not in parent_cmd:
        return False
    try:
        os.kill(parent_pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _cleanup_stale_watchdogs(
    *,
    tmux: TmuxClient,
    project: str | None = None,
    runner_id: str | None = None,
) -> dict[str, int]:
    """Self-heal stale watchdog processes (zombie/orphaned)."""
    watchdogs = _find_runner_watchdogs(project=project, runner_id=runner_id)
    stale_pids: list[int] = []
    zombie_count = 0
    orphan_count = 0

    for pid, command in watchdogs:
        state = (_pid_process_state(pid) or "").strip().upper()
        if state.startswith("Z"):
            stale_pids.append(pid)
            zombie_count += 1
            continue
        session_name = (_extract_cli_option(command, "--session") or "").strip()
        if session_name and not tmux.has_session(session_name):
            stale_pids.append(pid)
            orphan_count += 1

    terminated = forced = remaining = 0
    if stale_pids:
        terminated, forced, remaining = _terminate_pids(stale_pids, grace_seconds=0.8, poll_seconds=0.05)
    return {
        "matched": len(watchdogs),
        "stale": len(stale_pids),
        "zombies": zombie_count,
        "orphans": orphan_count,
        "terminated": terminated,
        "forced": forced,
        "remaining": remaining,
    }


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    state = (_pid_process_state(pid) or "").strip().upper()
    if state.startswith("Z"):
        _best_effort_reap_zombie(pid)
        return False
    return True


def _terminate_pids(pids: list[int], *, grace_seconds: float = 1.5, poll_seconds: float = 0.1) -> tuple[int, int, int]:
    """Terminate PIDs with TERM->wait->KILL semantics.

    Returns (terminated_count, forced_kill_count, remaining_count).
    """
    alive = [pid for pid in pids if _pid_is_alive(pid)]
    if not alive:
        return (0, 0, 0)

    for pid in alive:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue

    deadline = time.time() + max(grace_seconds, 0.0)
    while time.time() < deadline:
        if not any(_pid_is_alive(pid) for pid in alive):
            break
        time.sleep(max(poll_seconds, 0.05))

    remaining = [pid for pid in alive if _pid_is_alive(pid)]
    forced_kills = 0
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
            forced_kills += 1
        except OSError:
            continue

    # Give the OS a brief moment to reap.
    time.sleep(max(poll_seconds, 0.05))
    final_remaining = [pid for pid in alive if _pid_is_alive(pid)]
    terminated = len(alive) - len(final_remaining)
    return (terminated, forced_kills, len(final_remaining))


def parse_loop_args(args: list[str]) -> dict[str, str]:
    """Parse loop command options."""
    project: str | None = None
    runner_id: str | None = None
    complexity = "med"
    model_override: str | None = None
    hil_mode = "setup-only"
    runner_mode = "interactive-watchdog"

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
        elif arg.startswith("--hil-mode="):
            hil_mode = arg.split("=", 1)[1].lower()
        elif arg == "--hil-mode":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --hil-mode")
            hil_mode = args[i].lower()
        elif arg.startswith("--runner-mode="):
            runner_mode = arg.split("=", 1)[1].lower()
        elif arg == "--runner-mode":
            i += 1
            if i >= len(args):
                raise ValueError("Missing value for --runner-mode")
            runner_mode = args[i].lower()
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
    if hil_mode != "setup-only":
        raise ValueError("Invalid --hil-mode. Only setup-only is supported")
    if runner_mode not in {"interactive-watchdog", "exec"}:
        raise ValueError("Invalid --runner-mode. Use interactive-watchdog or exec")

    model, reasoning_effort = resolve_runner_profile(complexity, model_override)

    return {
        "project": project,
        "runner_id": resolved_runner_id,
        "complexity": complexity,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "hil_mode": hil_mode,
        "runner_mode": runner_mode,
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
    hil_mode: str,
    runner_mode: str,
):
    """Build session name, paths, and script for one loop runner."""
    session_name = f"runner-{project}"
    paths = build_runner_paths(dev=dev, project=project, runner_id=runner_id)
    if runner_mode == "exec":
        script = make_codex_exec_loop_script(
            dev=dev,
            project=project,
            runner_id=runner_id,
            model=model,
            reasoning_effort=reasoning_effort,
            hil_mode=hil_mode,
            paths=paths,
        )
    else:
        script = make_codex_chat_loop_script(
            dev=dev,
            project=project,
            runner_id=runner_id,
            session_name=session_name,
            model=model,
            reasoning_effort=reasoning_effort,
            hil_mode=hil_mode,
            paths=paths,
        )
    return session_name, paths, script


def create_loop_session(
    project: str,
    runner_id: str,
    model: str,
    reasoning_effort: str,
    hil_mode: str,
    runner_mode: str,
) -> None:
    """Create and attach to deterministic codex loop runner."""
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

    stale_summary = _cleanup_stale_watchdogs(
        tmux=tmux,
        project=project,
        runner_id=runner_id,
    )
    if stale_summary["stale"] > 0:
        print(
            "Watchdog self-heal: "
            f"stale={stale_summary['stale']} zombies={stale_summary['zombies']} "
            f"orphans={stale_summary['orphans']} terminated={stale_summary['terminated']} "
            f"forced={stale_summary['forced']} remaining={stale_summary['remaining']}"
        )

    gates_path, created_now = ensure_gates_file(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
    )
    if created_now:
        print(f"Created gates template: {gates_path}")

    runctl_path = _repo_home() / "bin" / "runctl"
    create_result = create_runner_state(
        dev=dev,
        project=project,
        runner_id=runner_id,
        approve_enable=None,
        project_root=project_root,
    )
    if create_result.get("ok") and create_result.get("enable_token"):
        print(f"Enable token required: {create_result['enable_token']}")
        print(
            "Approve with: "
            f"python3 {runctl_path} "
            f"--setup --project-root {project_root} --runner-id {runner_id} --approve-enable {create_result['enable_token']}"
        )

    session_name, paths, script = _prepare_loop_runner(
        dev=dev,
        project=project,
        runner_id=runner_id,
        model=model,
        reasoning_effort=reasoning_effort,
        hil_mode=hil_mode,
        runner_mode=runner_mode,
    )

    existing = tmux.list_sessions()
    if session_name in existing:
        print(f"Runner already exists: {session_name}")
        print("Restarting existing runner to apply current launcher...")
        if not tmux.kill_session(session_name):
            print(f"Error: failed to stop existing session {session_name}")
            return

    lingering_watchdogs = _find_runner_watchdogs(project=project, runner_id=runner_id)
    lingering_pids = [pid for pid, _ in lingering_watchdogs]
    if lingering_pids:
        terminated, forced, remaining = _terminate_pids(lingering_pids, grace_seconds=1.0, poll_seconds=0.05)
        print(
            "Watchdog reset before spawn: "
            f"matched={len(lingering_pids)} terminated={terminated} forced={forced} remaining={remaining}"
        )
        if remaining > 0:
            print("Warning: some old watchdogs are still alive; new session will still be started.")

    print(f"Starting loop runner {session_name}...", end="", flush=True)
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
        initial_phase = coerce_runner_phase(
            exec_context.get("phase") if isinstance(exec_context, dict) else state_data.get("current_phase"),
            default="discover",
        )
    execute_only_scope = build_phase_prompt_commands(
        dev=dev,
        project=project,
        runner_id=runner_id,
        project_root=project_root,
        phase=initial_phase,
    )[0]
    print(f"  Project:   {project}")
    print(f"  Runner ID: {runner_id}")
    print(f"  Model:     {model}")
    print(f"  Effort:    {reasoning_effort}")
    print(f"  HIL mode:  {hil_mode}")
    print(f"  Mode:      {runner_mode}")
    print(f"  Phase:     {initial_phase}")
    print(f"  Session:   {session_name}")
    print(
        "  Scope:     "
        f"{execute_only_scope}"
    )
    print(f"  Root:      {project_root}")
    print(f"  Done lock: {paths.complete_lock}")
    print(f"  Stop lock: {paths.stop_file}")
    print()

    if runner_mode == "interactive-watchdog":
        spawn_runner_watchdog(
            session=session_name,
            project=project,
            runner_id=runner_id,
            dev=dev,
            project_root=str(project_root),
            model=model,
            reasoning_effort=reasoning_effort,
            socket=tmux.socket,
        )

    tmux.attach(session_name)


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
    watchdogs = _find_runner_watchdogs(project=project, runner_id=runner_id)
    watchdog_pids = [pid for pid, _ in watchdogs]
    watchdog_terminated, watchdog_forced, watchdog_remaining = _terminate_pids(watchdog_pids)
    # Session/processes are hard-stopped; clear transient locks unless a watchdog still remains.
    paths.active_lock.unlink(missing_ok=True)
    if watchdog_remaining == 0:
        paths.stop_file.unlink(missing_ok=True)
    if killed:
        print(f"Stopped runner session: {session_name}")
    else:
        print(f"Runner session not active: {session_name}")
    if watchdog_pids:
        print(
            "Watchdog stop summary: "
            f"matched={len(watchdog_pids)} terminated={watchdog_terminated} "
            f"forced={watchdog_forced} remaining={watchdog_remaining}"
        )
    else:
        print("Watchdog not active for requested runner scope.")
    if watchdog_remaining == 0:
        print("Transient locks cleared: RUNNER_STOP.lock, RUNNER_ACTIVE.lock")
    else:
        print(f"Stop lock retained due to remaining watchdog processes: {paths.stop_file}")
    print(f"Root: {project_root}")


def stop_all_loop_sessions() -> None:
    """Stop all active runner sessions and write stop locks."""
    config = get_tmux_config()
    tmux = TmuxClient(config=config)
    dev = os.environ.get("DEV", "/Users/jian/Dev")

    sessions = [sess for sess in tmux.list_sessions() if sess.startswith("runner-")]
    watchdogs = _find_runner_watchdogs()

    projects: set[str] = set()
    for session_name in sessions:
        project = session_name.replace("runner-", "", 1).strip()
        if project:
            projects.add(project)
    for _, command in watchdogs:
        cmd_project = _extract_cli_option(command, "--project")
        if cmd_project:
            projects.add(cmd_project)
            continue
        cmd_session = _extract_cli_option(command, "--session") or ""
        if cmd_session.startswith("runner-"):
            project = cmd_session.replace("runner-", "", 1).strip()
            if project:
                projects.add(project)

    if not sessions and not watchdogs:
        print("No active runner sessions or watchdogs found.")
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

    watchdog_pids = [pid for pid, _ in watchdogs]
    watchdog_terminated, watchdog_forced, watchdog_remaining = _terminate_pids(watchdog_pids)
    print(
        "Watchdog stop summary: "
        f"matched={len(watchdog_pids)} terminated={watchdog_terminated} "
        f"forced={watchdog_forced} remaining={watchdog_remaining}"
    )

    if watchdog_remaining == 0:
        for project in sorted(projects):
            paths = build_runner_paths(dev=dev, project=project, runner_id="main")
            paths.active_lock.unlink(missing_ok=True)
            paths.stop_file.unlink(missing_ok=True)
        print("Transient locks cleared for all discovered runner projects.")
    else:
        print("Some watchdogs are still alive; RUNNER_STOP.lock files were kept for safety.")


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
    stale_summary = _cleanup_stale_watchdogs(tmux=tmux)
    if stale_summary["stale"] > 0:
        print(
            "Global watchdog self-heal: "
            f"stale={stale_summary['stale']} zombies={stale_summary['zombies']} "
            f"orphans={stale_summary['orphans']} terminated={stale_summary['terminated']} "
            f"forced={stale_summary['forced']} remaining={stale_summary['remaining']}"
        )
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

        create_result = create_runner_state(
            dev=dev,
            project=project,
            runner_id=runner_id,
            approve_enable=None,
            project_root=project_root,
        )
        if create_result.get("ok") and create_result.get("enable_token"):
            print(f"{project} enable token: {create_result['enable_token']}")

        session_name, _, script = _prepare_loop_runner(
            dev=dev,
            project=project,
            runner_id=runner_id,
            model=model,
            reasoning_effort=reasoning_effort,
            hil_mode="setup-only",
            runner_mode="interactive-watchdog",
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
        spawn_runner_watchdog(
            session=session_name,
            project=project,
            runner_id=runner_id,
            dev=dev,
            project_root=str(project_root),
            model=model,
            reasoning_effort=reasoning_effort,
            socket=tmux.socket,
        )
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

    # Legacy deterministic worker command retained for compatibility only.
    # Default runner sessions now use interactive chat wrapper mode.
    if args and args[0] == "__runner-loop":
        raise SystemExit(run_loop_worker(args[1:]))

    if not args:
        list_sessions()
        return

    if args[0] == "ls":
        list_sessions()
        return

    if args[0] in ("loop", "runner", "run", "r"):
        loop_args = args[1:]
        if loop_args and loop_args[0] in ("-h", "--help", "help"):
            _print_loop_usage()
            return
        if loop_args and loop_args[0] in ("--all", "all"):
            spawn_all_loop_runners()
            return

        try:
            parsed = parse_loop_args(loop_args)
        except ValueError as e:
            print(f"Error: {e}")
            _print_loop_usage()
            return

        create_loop_session(
            project=parsed["project"],
            runner_id=parsed["runner_id"],
            model=parsed["model"],
            reasoning_effort=parsed["reasoning_effort"],
            hil_mode=parsed["hil_mode"],
            runner_mode=parsed["runner_mode"],
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
        print("  cl ls                 # list/attach sessions")
        print(
            "  cl loop <project> [--runner-id <id>] [--complexity <low|med|high|xhigh>] "
            "[--model <provider/model>] [--hil-mode <setup-only>] "
            "[--runner-mode <interactive-watchdog|exec>]"
        )
        print("  cl runner <project>   # alias of loop")
        print("  cl stop <project>     # stop runner (alias: cl k <project>)")
        print("  cl ka <project>       # stop runner alias")
        print("  cl kb <project>       # stop runner alias")
        print("  cl k*                 # stop all runner sessions")
        return

    escaped_args = [shlex.quote(a) for a in args]
    create_session(escaped_args)
