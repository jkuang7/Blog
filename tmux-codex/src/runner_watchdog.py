"""Interactive runner watchdog for tmux Codex sessions."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CL_SOCK
from .runctl import create_runner_state, resolve_target_project_root
from .runner_loop import build_runner_paths
from .runner_state import (
    build_phase_prompt_commands,
    coerce_runner_phase,
    compute_worktree_fingerprint,
    detect_git_context,
    read_json,
    utc_now,
    write_json,
)
from .runner_status import (
    detect_runner_state,
    has_explicit_codex_prompt,
    is_codex_runtime_process,
    strip_ansi,
)
from .tmux import LINES_HEALTH, TmuxClient

PREPARED_MARKER_NAME = "RUNNER_CYCLE_PREPARED.json"
PREPARED_MARKER_TTL_SECONDS = 5 * 60
PREPARED_EXIT_GRACE_SECONDS = 6.0
SETUP_REFRESH_MIN_INTERVAL_SECONDS = 45.0
INJECT_NO_PROGRESS_RETRY_SECONDS = 20.0
NO_PROGRESS_BACKOFF_SECONDS = 60.0
UNKNOWN_COMMAND_RE = re.compile(r"Unrecognized command '([^']+)'")
TRUST_PROMPT_RE = re.compile(r"Do you trust the contents of this directory\?", re.IGNORECASE)


@dataclass(frozen=True)
class RunnerWatchdogConfig:
    session: str
    project: str
    runner_id: str
    dev: str
    project_root: str | None
    socket: str
    model: str
    reasoning_effort: str
    poll_seconds: float
    idle_cooldown_seconds: float


@dataclass(frozen=True)
class CycleProgressSnapshot:
    phase: str | None
    phase_status: str | None
    next_task_id: str | None
    next_task: str | None
    status: str | None
    git_head: str | None
    worktree_fingerprint: str | None


def _repo_home() -> Path:
    override = os.environ.get("TMUX_CLI_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")


def _watchdog_heartbeat_path(dev: str, project: str, runner_id: str) -> Path:
    return build_runner_paths(dev=dev, project=project, runner_id=runner_id).state.runner_dir / "RUNNER_WATCHDOG.json"


def _write_heartbeat(
    path: Path,
    *,
    state: str,
    session: str,
    process_name: str | None,
    last_activity_ts: float | None,
    last_injected_ts: float | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now(),
        "session": session,
        "state": state,
        "process_name": process_name or "",
        "last_activity_ts": last_activity_ts,
        "last_injected_ts": last_injected_ts,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_interactive_codex_cmd(project_root: str, model: str, reasoning_effort: str) -> str:
    q_project_root = shlex.quote(project_root)
    q_model = shlex.quote(model)
    q_reasoning = shlex.quote(f'reasoning.effort="{reasoning_effort}"')
    return (
        f'PROJECT_ROOT={q_project_root}; '
        'TARGET_BRANCH=""; '
        'if [[ -f "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" ]]; then '
        'TARGET_BRANCH="$(sed -n \'s/.*\"git_branch\":[[:space:]]*\"\\\\([^\"]*\\\\)\".*/\\\\1/p\' "$PROJECT_ROOT/.memory/runner/RUNNER_STATE.json" | head -n 1)"; '
        "fi; "
        'if [[ -n "$TARGET_BRANCH" ]]; then '
        'CURRENT_BRANCH="$(git -C "$PROJECT_ROOT" branch --show-current 2>/dev/null || true)"; '
        'if [[ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]]; then git -C "$PROJECT_ROOT" checkout "$TARGET_BRANCH" >/dev/null 2>&1 || true; fi; '
        "fi; "
        'cd "$PROJECT_ROOT" && '
        f'codex --search --dangerously-bypass-approvals-and-sandbox -m {q_model} -c {q_reasoning}; '
        "clear; exec zsh -l"
    )


def _prepared_marker_path(paths) -> Path:
    return paths.state.runner_dir / PREPARED_MARKER_NAME


def _parse_utc_timestamp(value: str) -> float | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _load_fresh_prepared_marker(
    *,
    marker_path: Path,
    config: RunnerWatchdogConfig,
    project_root: Path,
    now_ts: float,
    log_path: Path,
    max_age_seconds: int = PREPARED_MARKER_TTL_SECONDS,
) -> dict[str, object] | None:
    if not marker_path.exists():
        return None
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        marker_path.unlink(missing_ok=True)
        _append_log(log_path, f"watchdog removed invalid prepared marker: {marker_path.name}")
        return None
    if not isinstance(payload, dict):
        marker_path.unlink(missing_ok=True)
        _append_log(log_path, f"watchdog removed malformed prepared marker: {marker_path.name}")
        return None

    project = str(payload.get("project", "")).strip()
    runner_id = str(payload.get("runner_id", "")).strip()
    git_worktree = str(payload.get("git_worktree", "")).strip()
    prepared_at = str(payload.get("prepared_at", "")).strip()
    prepared_ts = _parse_utc_timestamp(prepared_at)
    if (
        project != config.project
        or runner_id != config.runner_id
        or not git_worktree
        or Path(git_worktree).resolve() != project_root.resolve()
        or prepared_ts is None
    ):
        marker_path.unlink(missing_ok=True)
        _append_log(log_path, f"watchdog removed mismatched prepared marker: {marker_path.name}")
        return None

    age_seconds = now_ts - prepared_ts
    if age_seconds < -60 or age_seconds > max_age_seconds:
        marker_path.unlink(missing_ok=True)
        _append_log(
            log_path,
            f"watchdog removed stale prepared marker ({int(age_seconds)}s age): {marker_path.name}",
        )
        return None
    return payload


def _consume_prepared_marker(marker_path: Path, log_path: Path) -> None:
    if marker_path.exists():
        marker_path.unlink(missing_ok=True)
        _append_log(log_path, f"watchdog consumed prepared marker: {marker_path.name}")


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _capture_cycle_progress_snapshot(project_root: Path) -> CycleProgressSnapshot | None:
    state_file = project_root / ".memory" / "runner" / "RUNNER_STATE.json"
    state = read_json(state_file) or {}
    if not isinstance(state, dict):
        state = {}
    git_context = detect_git_context(project_root)
    return CycleProgressSnapshot(
        phase=_as_text(state.get("current_phase")),
        phase_status=_as_text(state.get("phase_status")),
        next_task_id=_as_text(state.get("next_task_id")),
        next_task=_as_text(state.get("next_task")),
        status=_as_text(state.get("status")),
        git_head=_as_text(git_context.get("git_head")),
        worktree_fingerprint=compute_worktree_fingerprint(project_root),
    )


def _snapshot_to_dict(snapshot: CycleProgressSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "phase": snapshot.phase,
        "phase_status": snapshot.phase_status,
        "next_task_id": snapshot.next_task_id,
        "next_task": snapshot.next_task,
        "status": snapshot.status,
        "git_head": snapshot.git_head,
        "worktree_fingerprint": snapshot.worktree_fingerprint,
    }


def _read_progress_snapshot_dict(project_root: Path) -> dict[str, Any]:
    state = read_json(project_root / ".memory" / "runner" / "RUNNER_STATE.json") or {}
    if not isinstance(state, dict):
        state = {}
    git_context = detect_git_context(project_root)
    return {
        "phase": _as_text(state.get("current_phase")) or "discover",
        "phase_status": _as_text(state.get("phase_status")) or None,
        "next_task_id": _as_text(state.get("next_task_id")) or None,
        "next_task": _as_text(state.get("next_task")) or None,
        "status": _as_text(state.get("status")) or None,
        "git_head": _as_text(git_context.get("git_head")) or None,
        "worktree_fingerprint": compute_worktree_fingerprint(project_root),
    }


def _cycle_has_advanced(
    before: CycleProgressSnapshot | None,
    after: CycleProgressSnapshot | None,
) -> bool:
    if before is None or after is None:
        return True
    return any(
        (
            before.phase != after.phase,
            before.phase_status != after.phase_status,
            before.next_task_id != after.next_task_id,
            before.next_task != after.next_task,
            before.status != after.status,
            before.git_head != after.git_head,
            before.worktree_fingerprint != after.worktree_fingerprint,
        )
    )


def _load_phase_prompt_commands(
    *,
    config: RunnerWatchdogConfig,
    project_root: Path,
) -> tuple[str, list[str]]:
    exec_context = read_json(project_root / ".memory" / "runner" / "RUNNER_EXEC_CONTEXT.json") or {}
    state = read_json(project_root / ".memory" / "runner" / "RUNNER_STATE.json") or {}
    phase = coerce_runner_phase(
        exec_context.get("phase") if isinstance(exec_context, dict) else state.get("current_phase"),
        default="discover",
    )
    commands = build_phase_prompt_commands(
        dev=config.dev,
        project=config.project,
        runner_id=config.runner_id,
        project_root=project_root,
        phase=phase,
    )
    return phase, commands


def _record_cycle_progress_baseline(project_root: Path) -> None:
    snapshot = _read_progress_snapshot_dict(project_root)
    exec_context_path = project_root / ".memory" / "runner" / "RUNNER_EXEC_CONTEXT.json"
    exec_context = read_json(exec_context_path) or {}
    if not isinstance(exec_context, dict):
        exec_context = {}
    exec_context["progress_baseline"] = snapshot
    exec_context["cycle_progress_baseline"] = snapshot
    exec_context["cycle_progress_recorded_at"] = utc_now()
    write_json(exec_context_path, exec_context)


def _load_phase_budget_status(*, project_root: Path, now_ts: float) -> tuple[str, bool]:
    state = read_json(project_root / ".memory" / "runner" / "RUNNER_STATE.json") or {}
    exec_context = read_json(project_root / ".memory" / "runner" / "RUNNER_EXEC_CONTEXT.json") or {}
    if not isinstance(state, dict):
        state = {}
    if not isinstance(exec_context, dict):
        exec_context = {}

    phase = coerce_runner_phase(state.get("current_phase") or exec_context.get("phase"), default="discover")
    started_at = _as_text(state.get("phase_started_at")) or _as_text(exec_context.get("phase_started_at"))
    if not started_at:
        return phase, False
    started_ts = _parse_utc_timestamp(started_at)
    if started_ts is None:
        return phase, False
    try:
        budget_minutes = int(state.get("phase_budget_minutes", exec_context.get("phase_budget_minutes", 20)))
    except (TypeError, ValueError):
        budget_minutes = 20
    return phase, (now_ts - started_ts) >= max(1, budget_minutes) * 60


def _reset_phase_budget_window(project_root: Path) -> bool:
    state_path = project_root / ".memory" / "runner" / "RUNNER_STATE.json"
    exec_context_path = project_root / ".memory" / "runner" / "RUNNER_EXEC_CONTEXT.json"
    state = read_json(state_path) or {}
    if not isinstance(state, dict):
        return False

    restarted_at = utc_now()
    state["phase_started_at"] = restarted_at
    if _as_text(state.get("status")) != "blocked":
        state["phase_status"] = "active"
    write_json(state_path, state)

    exec_context = read_json(exec_context_path) or {}
    if isinstance(exec_context, dict):
        exec_context["phase_started_at"] = restarted_at
        write_json(exec_context_path, exec_context)
    return True


def _prompt_has_pending_command(output: str, markers: tuple[str, ...]) -> bool:
    """Best-effort check for a command still sitting at prompt after injection."""
    cleaned = strip_ansi(output or "").replace("\r", "")
    non_empty = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not non_empty:
        return False
    last = non_empty[-1]
    return last.startswith(("❯", "›", ">")) and any(marker in last for marker in markers)


def _extract_unrecognized_command(output: str) -> str | None:
    cleaned = strip_ansi(output or "").replace("\r", "")
    match = UNKNOWN_COMMAND_RE.search(cleaned)
    if not match:
        return None
    command = match.group(1).strip()
    return command or None


def _has_directory_trust_prompt(output: str) -> bool:
    cleaned = strip_ansi(output or "").replace("\r", "")
    non_empty = [line.strip() for line in cleaned.splitlines() if line.strip()]
    recent = "\n".join(non_empty[-12:])
    return bool(TRUST_PROMPT_RE.search(recent)) and "press enter to continue" in recent.lower()


def _send_with_enter_retry(
    client: TmuxClient,
    pane_target: str,
    command: str,
    command_markers: tuple[str, ...],
    *,
    force_second_enter: bool,
    capture_output,
    sleep_fn,
) -> bool:
    """Send command and resend Enter once if prompt still shows the command."""
    if not client.send_keys(pane_target, command, enter=True, delay_ms=220):
        return False
    if force_second_enter:
        # Slash-command autocomplete can consume the first Enter without submit.
        sleep_fn(0.12)
        client.send_keys(pane_target, "", enter=True, delay_ms=0)
    sleep_fn(0.15)
    latest = capture_output()
    if _prompt_has_pending_command(latest, command_markers):
        # Retry Enter once for occasional dropped-submit behavior.
        client.send_keys(pane_target, "", enter=True, delay_ms=0)
    return True


def _refresh_runner_state(
    *,
    config: RunnerWatchdogConfig,
    project_root: Path,
    log_path: Path,
) -> bool:
    try:
        result = create_runner_state(
            dev=config.dev,
            project=config.project,
            runner_id=config.runner_id,
            approve_enable=None,
            project_root=project_root,
        )
    except Exception as exc:  # pragma: no cover - defensive logging path.
        _append_log(log_path, f"watchdog state refresh failed before handoff: {exc}")
        return False

    if result.get("ok"):
        _append_log(log_path, "watchdog refreshed runner state before session handoff")
        return True
    _append_log(log_path, f"watchdog state refresh returned error: {result.get('error', 'unknown error')}")
    return False


def _refresh_runner_state_if_due(
    *,
    config: RunnerWatchdogConfig,
    project_root: Path,
    log_path: Path,
    now_ts: float,
    last_refresh_ts: float,
    force: bool = False,
    min_interval_seconds: float = SETUP_REFRESH_MIN_INTERVAL_SECONDS,
) -> float:
    if not force and last_refresh_ts > 0 and (now_ts - last_refresh_ts) < min_interval_seconds:
        _append_log(
            log_path,
            f"watchdog skipped setup refresh (last refresh {int(now_ts - last_refresh_ts)}s ago)",
        )
        return last_refresh_ts
    if _refresh_runner_state(
        config=config,
        project_root=project_root,
        log_path=log_path,
    ):
        return now_ts
    return last_refresh_ts


def _respawn_with_watchexec_semantics(
    *,
    client: TmuxClient,
    pane_target: str,
    session: str,
    codex_cmd: str,
    log_path: Path,
    sleep_fn,
    stop_file: Path | None = None,
    done_file: Path | None = None,
    grace_attempts: int = 10,
    grace_sleep_seconds: float = 0.15,
) -> str:
    """
    Restart semantics inspired by watchexec:
    1) send interrupt
    2) wait briefly for graceful exit
    3) force-restart if still running
    """
    client.send_interrupt(pane_target)
    _append_log(log_path, "watchdog handoff: sent interrupt before respawn")

    graceful_exit = False
    for _ in range(max(1, grace_attempts)):
        output = client.capture_pane(session, lines=LINES_HEALTH) or ""
        process_name = client.get_pane_process(session)
        if not is_codex_runtime_process(process_name, output):
            graceful_exit = True
            break
        sleep_fn(grace_sleep_seconds)

    if graceful_exit:
        _append_log(log_path, "watchdog handoff: graceful exit observed before respawn")
    else:
        _append_log(log_path, "watchdog handoff: grace timeout; forcing respawn")

    if (stop_file and stop_file.exists()) or (done_file and done_file.exists()):
        _append_log(log_path, "watchdog handoff: lock detected after wait; skipping respawn")
        return "lock_abort"
    if client.respawn_pane(pane_target, codex_cmd, kill=True):
        return "respawned"
    return "respawn_failed"


def _extract_open_tasks(project_root: Path, max_items: int = 40) -> list[str]:
    tasks_file = project_root / ".memory" / "runner" / "TASKS.json"
    if not tasks_file.exists():
        return []
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    tasks = payload.get("tasks")
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


def run_runner_watchdog(
    config: RunnerWatchdogConfig,
    *,
    tmux: TmuxClient | None = None,
    sleep_fn=time.sleep,
    now_fn=time.time,
    max_polls: int | None = None,
) -> int:
    client = tmux or TmuxClient(socket=config.socket)
    paths = build_runner_paths(dev=config.dev, project=config.project, runner_id=config.runner_id)
    heartbeat_file = _watchdog_heartbeat_path(config.dev, config.project, config.runner_id)
    resolved_project_root = resolve_target_project_root(
        dev=config.dev,
        project=config.project,
        runner_id=config.runner_id,
        project_root_override=config.project_root,
    )
    project_root_str = str(resolved_project_root)
    prepared_marker_file = _prepared_marker_path(paths)
    current_phase, slash_commands = _load_phase_prompt_commands(
        config=config,
        project_root=resolved_project_root,
    )
    slash_cmd_index = 0
    last_slash_commands = tuple(slash_commands)
    codex_cmd = _build_interactive_codex_cmd(project_root_str, config.model, config.reasoning_effort)

    _append_log(
        paths.runner_log,
        (
            "watchdog start "
            f"session={config.session} project={config.project} "
            f"root={project_root_str} "
            f"poll={config.poll_seconds}s cooldown={config.idle_cooldown_seconds}s"
        ),
    )

    last_capture = ""
    last_activity_ts: float | None = None
    last_injected_ts = 0.0
    cycle_handoff_pending = False
    saw_working_since_injection = False
    prepared_exit_wait_since: float | None = None
    last_refresh_ts = 0.0
    last_state: str | None = None
    pending_cycle_snapshot: CycleProgressSnapshot | None = None
    no_progress_snapshot: CycleProgressSnapshot | None = None
    no_progress_backoff_until = 0.0
    budget_interrupt_ts: float | None = None
    last_trust_response_ts = 0.0
    polls = 0

    while True:
        polls += 1
        if max_polls is not None and polls > max_polls:
            return 0

        now = now_fn()
        if not client.has_session(config.session):
            _append_log(paths.runner_log, "watchdog exit: session not found")
            _write_heartbeat(
                heartbeat_file,
                state="exited",
                session=config.session,
                process_name="",
                last_activity_ts=last_activity_ts,
                last_injected_ts=last_injected_ts,
            )
            return 0

        panes = client.list_panes(config.session)
        pane_target = panes[0] if panes else f"{config.session}:0.0"

        stop_exists = paths.stop_file.exists()
        done_exists = paths.complete_lock.exists()
        if done_exists:
            open_tasks = _extract_open_tasks(resolved_project_root)
            if open_tasks:
                paths.complete_lock.unlink(missing_ok=True)
                done_exists = False
                _append_log(
                    paths.runner_log,
                    "watchdog removed stale done lock because TASKS has open entries "
                    f"({len(open_tasks)} remaining)",
                )
        if stop_exists or done_exists:
            client.send_interrupt(pane_target)
            if done_exists:
                last_refresh_ts = _refresh_runner_state_if_due(
                    config=config,
                    project_root=resolved_project_root,
                    log_path=paths.runner_log,
                    now_ts=now,
                    last_refresh_ts=last_refresh_ts,
                    force=True,
                )
            if stop_exists:
                paths.stop_file.unlink(missing_ok=True)
            reason = "stop" if stop_exists else "done"
            _append_log(paths.runner_log, f"watchdog exit: {reason} lock detected")
            _write_heartbeat(
                heartbeat_file,
                state="sleeping",
                session=config.session,
                process_name="",
                last_activity_ts=last_activity_ts,
                last_injected_ts=last_injected_ts,
            )
            return 0

        pane_output = client.capture_pane(config.session, lines=LINES_HEALTH) or ""
        if pane_output != last_capture:
            last_capture = pane_output
            last_activity_ts = now
        current_phase, slash_commands = _load_phase_prompt_commands(
            config=config,
            project_root=resolved_project_root,
        )
        current_slash_commands = tuple(slash_commands)
        if current_slash_commands != last_slash_commands:
            slash_cmd_index = 0
            last_slash_commands = current_slash_commands
        unrecognized_command = _extract_unrecognized_command(pane_output)
        if unrecognized_command:
            current_prompt_name = slash_commands[slash_cmd_index].split(" ", 1)[0]
            if (
                unrecognized_command == current_prompt_name
                and slash_cmd_index < (len(slash_commands) - 1)
            ):
                slash_cmd_index += 1
                _append_log(
                    paths.runner_log,
                    (
                        f"watchdog falling back from {unrecognized_command} "
                        f"to {slash_commands[slash_cmd_index].split(' ', 1)[0]} "
                        f"(phase={current_phase})"
                    ),
                )
        prepared_marker = _load_fresh_prepared_marker(
            marker_path=prepared_marker_file,
            config=config,
            project_root=resolved_project_root,
            now_ts=now,
            log_path=paths.runner_log,
        )
        if prepared_marker is not None:
            if prepared_exit_wait_since is None:
                prepared_exit_wait_since = now
                _append_log(paths.runner_log, "watchdog detected prepared marker; waiting for clean session exit")
        else:
            prepared_exit_wait_since = None
        process_name = client.get_pane_process(config.session)
        state = detect_runner_state(
            output=pane_output,
            process_name=process_name,
            last_activity_ts=last_activity_ts,
        )
        budget_phase, phase_budget_expired = _load_phase_budget_status(project_root=resolved_project_root, now_ts=now)
        if state != last_state:
            _append_log(
                paths.runner_log,
                f"watchdog state {last_state or 'init'} -> {state} (proc={process_name or 'none'})",
            )
            last_state = state
        if cycle_handoff_pending and state == "working":
            saw_working_since_injection = True
        if phase_budget_expired and state in {"working", "unknown"} and budget_interrupt_ts is None:
            client.send_interrupt(pane_target)
            budget_interrupt_ts = now
            _append_log(
                paths.runner_log,
                f"watchdog sent interrupt because phase budget expired (phase={budget_phase})",
            )
        elif not phase_budget_expired:
            budget_interrupt_ts = None

        if _has_directory_trust_prompt(pane_output):
            if (now - last_trust_response_ts) >= 2.0:
                if client.send_keys(pane_target, "", enter=True, delay_ms=120):
                    last_trust_response_ts = now
                    _append_log(paths.runner_log, "watchdog accepted Codex directory trust prompt")
                    sleep_fn(config.poll_seconds)
                    continue

        if state == "exited":
            if phase_budget_expired and not cycle_handoff_pending:
                if _reset_phase_budget_window(resolved_project_root):
                    _append_log(
                        paths.runner_log,
                        f"watchdog reset phase budget window before respawn (phase={budget_phase})",
                    )
            current_snapshot = _capture_cycle_progress_snapshot(resolved_project_root) if cycle_handoff_pending else None
            if cycle_handoff_pending and not _cycle_has_advanced(pending_cycle_snapshot, current_snapshot):
                suppressed_snapshot = current_snapshot or pending_cycle_snapshot
                _append_log(
                    paths.runner_log,
                    "watchdog suppressed respawn after no-progress cycle exit; backing off reinjection",
                )
                if prepared_marker is not None:
                    _consume_prepared_marker(prepared_marker_file, paths.runner_log)
                cycle_handoff_pending = False
                saw_working_since_injection = False
                prepared_exit_wait_since = None
                pending_cycle_snapshot = None
                no_progress_snapshot = suppressed_snapshot
                no_progress_backoff_until = max(no_progress_backoff_until, now + NO_PROGRESS_BACKOFF_SECONDS)
                _write_heartbeat(
                    heartbeat_file,
                    state="sleeping",
                    session=config.session,
                    process_name=process_name,
                    last_activity_ts=last_activity_ts,
                    last_injected_ts=last_injected_ts,
                )
                sleep_fn(config.poll_seconds)
                continue
            if prepared_marker is None:
                last_refresh_ts = _refresh_runner_state_if_due(
                    config=config,
                    project_root=resolved_project_root,
                    log_path=paths.runner_log,
                    now_ts=now,
                    last_refresh_ts=last_refresh_ts,
                )
            else:
                _append_log(
                    paths.runner_log,
                    "watchdog skipped setup refresh on exit because prepared marker is present",
                )
            if client.respawn_pane(pane_target, codex_cmd, kill=True):
                _append_log(paths.runner_log, "watchdog respawned interactive codex pane")
                last_capture = ""
                last_activity_ts = now
            else:
                _append_log(paths.runner_log, "watchdog failed to respawn interactive codex pane")
            if prepared_marker is not None:
                _consume_prepared_marker(prepared_marker_file, paths.runner_log)
            cycle_handoff_pending = False
            saw_working_since_injection = False
            prepared_exit_wait_since = None
            pending_cycle_snapshot = None
            no_progress_snapshot = None
            no_progress_backoff_until = 0.0
            _write_heartbeat(
                heartbeat_file,
                state="sleeping",
                session=config.session,
                process_name=process_name,
                last_activity_ts=last_activity_ts,
                last_injected_ts=last_injected_ts,
            )
            sleep_fn(config.poll_seconds)
            continue

        idle_for_cooldown = (now - last_injected_ts) >= config.idle_cooldown_seconds
        inject_reason = ""
        if state == "idle":
            inject_reason = "idle"
        elif state == "unknown" and has_explicit_codex_prompt(pane_output):
            # Safety path: process detection can be ambiguous under wrappers.
            inject_reason = "explicit_prompt"

        prepared_stalled = (
            prepared_marker is not None
            and prepared_exit_wait_since is not None
            and (now - prepared_exit_wait_since) >= PREPARED_EXIT_GRACE_SECONDS
        )
        budget_handoff_ready = phase_budget_expired and (
            state == "idle"
            or (
                budget_interrupt_ts is not None
                and (now - budget_interrupt_ts) >= PREPARED_EXIT_GRACE_SECONDS
            )
        )
        if (
            cycle_handoff_pending
            and not saw_working_since_injection
            and state == "idle"
            and last_injected_ts > 0.0
            and (now - last_injected_ts) >= INJECT_NO_PROGRESS_RETRY_SECONDS
        ):
                _append_log(
                    paths.runner_log,
                    f"watchdog no-progress after {current_phase} prompt; requeueing execute prompt injection",
                )
                cycle_handoff_pending = False
                last_injected_ts = 0.0
        idle_handoff_ready = (
            state == "idle"
            and idle_for_cooldown
            and last_injected_ts > 0.0
            and cycle_handoff_pending
            and saw_working_since_injection
        )
        handoff_ready = budget_handoff_ready or prepared_stalled or (idle_handoff_ready and prepared_marker is None)
        if handoff_ready:
            if not budget_handoff_ready:
                current_snapshot = _capture_cycle_progress_snapshot(resolved_project_root)
                if not _cycle_has_advanced(pending_cycle_snapshot, current_snapshot):
                    suppressed_snapshot = current_snapshot or pending_cycle_snapshot
                    _append_log(
                        paths.runner_log,
                        "watchdog suppressed fresh-session handoff because runner state/worktree did not advance",
                    )
                    if prepared_stalled and state in {"working", "unknown"}:
                        client.send_interrupt(pane_target)
                        _append_log(paths.runner_log, "watchdog sent interrupt to unwind no-progress prepared-marker cycle")
                    cycle_handoff_pending = False
                    saw_working_since_injection = False
                    prepared_exit_wait_since = None
                    pending_cycle_snapshot = None
                    no_progress_snapshot = suppressed_snapshot
                    no_progress_backoff_until = max(no_progress_backoff_until, now + NO_PROGRESS_BACKOFF_SECONDS)
                    last_injected_ts = now
                    if prepared_marker is not None:
                        _consume_prepared_marker(prepared_marker_file, paths.runner_log)
                    sleep_fn(config.poll_seconds)
                    continue
            if budget_handoff_ready:
                _append_log(
                    paths.runner_log,
                    f"watchdog rotating to fresh codex session because phase budget expired (phase={budget_phase})",
                )
                if _reset_phase_budget_window(resolved_project_root):
                    _append_log(
                        paths.runner_log,
                        f"watchdog reset phase budget window before fresh-session handoff (phase={budget_phase})",
                    )
                last_refresh_ts = _refresh_runner_state_if_due(
                    config=config,
                    project_root=resolved_project_root,
                    log_path=paths.runner_log,
                    now_ts=now,
                    last_refresh_ts=last_refresh_ts,
                    force=True,
                )
            elif prepared_stalled:
                _append_log(
                    paths.runner_log,
                    f"watchdog forcing respawn after prepared-marker grace timeout (phase={current_phase})",
                )
            else:
                reason = str(prepared_marker.get("handoff_reason", "")).strip() if isinstance(prepared_marker, dict) else ""
                _append_log(
                    paths.runner_log,
                    f"watchdog rotating to fresh codex session for next phase handoff (phase={current_phase} reason={reason or 'idle'})",
                )
                last_refresh_ts = _refresh_runner_state_if_due(
                    config=config,
                    project_root=resolved_project_root,
                    log_path=paths.runner_log,
                    now_ts=now,
                    last_refresh_ts=last_refresh_ts,
                )
            respawn_status = _respawn_with_watchexec_semantics(
                client=client,
                pane_target=pane_target,
                session=config.session,
                codex_cmd=codex_cmd,
                log_path=paths.runner_log,
                sleep_fn=sleep_fn,
                stop_file=paths.stop_file,
                done_file=paths.complete_lock,
            )
            if respawn_status == "respawned":
                _append_log(paths.runner_log, "watchdog respawned interactive codex pane for fresh-session handoff")
                _consume_prepared_marker(prepared_marker_file, paths.runner_log)
                cycle_handoff_pending = False
                saw_working_since_injection = False
                prepared_exit_wait_since = None
                pending_cycle_snapshot = None
                no_progress_snapshot = None
                no_progress_backoff_until = 0.0
                last_capture = ""
                last_activity_ts = now
                last_injected_ts = 0.0
                budget_interrupt_ts = None
                _write_heartbeat(
                    heartbeat_file,
                    state="working",
                    session=config.session,
                    process_name="respawn",
                    last_activity_ts=last_activity_ts,
                    last_injected_ts=last_injected_ts,
                )
                sleep_fn(config.poll_seconds)
                continue
            if respawn_status == "lock_abort":
                _append_log(paths.runner_log, "watchdog handoff aborted due to lock; no respawn")
                cycle_handoff_pending = False
                saw_working_since_injection = False
                prepared_exit_wait_since = None
                pending_cycle_snapshot = None
                if prepared_marker is not None:
                    _consume_prepared_marker(prepared_marker_file, paths.runner_log)
                sleep_fn(config.poll_seconds)
                continue
            _append_log(paths.runner_log, "watchdog failed fresh-session respawn; continuing in current pane")
            cycle_handoff_pending = False
            saw_working_since_injection = False
            pending_cycle_snapshot = None
            budget_interrupt_ts = None
            if prepared_marker is not None:
                _consume_prepared_marker(prepared_marker_file, paths.runner_log)

        if inject_reason and idle_for_cooldown:
            if now < no_progress_backoff_until:
                current_snapshot = _capture_cycle_progress_snapshot(resolved_project_root)
                if _cycle_has_advanced(no_progress_snapshot, current_snapshot):
                    _append_log(paths.runner_log, "watchdog cleared no-progress backoff because runner state/worktree advanced")
                    no_progress_snapshot = None
                    no_progress_backoff_until = 0.0
                else:
                    sleep_fn(config.poll_seconds)
                    continue
            # Clear any partially-typed slash command before injecting fresh input.
            if hasattr(client, "clear_prompt_line"):
                client.clear_prompt_line(pane_target)
            sleep_fn(0.05)
            slash_cmd = slash_commands[slash_cmd_index]
            prompt_marker = slash_cmd.split(" ", 1)[0]
            _record_cycle_progress_baseline(resolved_project_root)
            if _send_with_enter_retry(
                client,
                pane_target,
                slash_cmd,
                (prompt_marker,),
                force_second_enter=True,
                capture_output=lambda: client.capture_pane(config.session, lines=LINES_HEALTH) or "",
                sleep_fn=sleep_fn,
            ):
                _append_log(
                    paths.runner_log,
                    f"watchdog injected {slash_cmd} (phase={current_phase} reason={inject_reason})",
                )
                last_injected_ts = now
                cycle_handoff_pending = True
                saw_working_since_injection = False
                pending_cycle_snapshot = _capture_cycle_progress_snapshot(resolved_project_root)
                no_progress_snapshot = None
                no_progress_backoff_until = 0.0

        _write_heartbeat(
            heartbeat_file,
            state=state,
            session=config.session,
            process_name=process_name,
            last_activity_ts=last_activity_ts,
            last_injected_ts=last_injected_ts,
        )
        sleep_fn(config.poll_seconds)


def parse_runner_watchdog_args(argv: list[str]) -> RunnerWatchdogConfig:
    parser = argparse.ArgumentParser(description="Interactive runner watchdog")
    parser.add_argument("--session", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--runner-id", default="main")
    parser.add_argument("--dev", default=os.environ.get("DEV", "/Users/jian/Dev"))
    parser.add_argument("--project-root")
    parser.add_argument("--socket", default=CL_SOCK)
    parser.add_argument("--model", default="gpt-5.3-codex")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--idle-cooldown-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)
    return RunnerWatchdogConfig(
        session=args.session,
        project=args.project,
        runner_id=args.runner_id,
        dev=args.dev,
        project_root=args.project_root,
        socket=args.socket,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        poll_seconds=max(args.poll_seconds, 0.2),
        idle_cooldown_seconds=max(args.idle_cooldown_seconds, 1.0),
    )


def run_runner_watchdog_worker(argv: list[str]) -> int:
    return run_runner_watchdog(parse_runner_watchdog_args(argv))


def spawn_runner_watchdog(
    *,
    session: str,
    project: str,
    runner_id: str,
    dev: str,
    project_root: str | None,
    model: str,
    reasoning_effort: str,
    socket: str = CL_SOCK,
    poll_seconds: float = 2.0,
    idle_cooldown_seconds: float = 8.0,
) -> subprocess.Popen:
    """Spawn watchdog detached from the menu/CLI foreground process."""
    cmd = [
        sys.executable,
        "-m",
        "src.runner_watchdog",
        "--session",
        session,
        "--project",
        project,
        "--runner-id",
        runner_id,
        "--dev",
        dev,
        "--project-root",
        project_root or "",
        "--socket",
        socket,
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--poll-seconds",
        str(poll_seconds),
        "--idle-cooldown-seconds",
        str(idle_cooldown_seconds),
    ]
    env = os.environ.copy()
    env.pop("TMUX", None)
    return subprocess.Popen(
        cmd,
        cwd=str(_repo_home()),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


if __name__ == "__main__":
    raise SystemExit(run_runner_watchdog_worker(sys.argv[1:]))
