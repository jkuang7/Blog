"""Runner pane status detection helpers."""

from __future__ import annotations

import re
import time
from pathlib import PurePosixPath
from typing import Literal

RunnerState = Literal["working", "idle", "sleeping", "exited", "unknown"]

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_WORKING_MARKERS = (
    "to interrupt",
    "working (",
    "thinking",
    "background task",
    "background tasks",
)
_SLEEPING_MARKERS = (
    "backing off",
    "restarting runner",
)
_PROMPT_PREFIXES = ("❯", "›", ">")
_CODEX_BANNER_MARKERS = ("openai codex",)
_CODEX_FOOTER_MARKERS = ("gpt-", "/model to change", "100% left")
_CODEX_PROCESS_NAMES = ("codex",)
_CODEX_WRAPPER_PROCESS_NAMES = ("node", "nodejs")


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def _normalize_lines(output: str) -> list[str]:
    cleaned = strip_ansi(output).replace("\r", "")
    return [line.rstrip() for line in cleaned.splitlines()]


def _has_prompt(lines: list[str], sample_size: int = 10) -> bool:
    non_empty = [line.strip() for line in lines if line.strip()]
    for line in non_empty[-sample_size:]:
        if line.startswith(_PROMPT_PREFIXES):
            return True
    return False


def _process_basename(process_name: str | None) -> str:
    if not process_name:
        return ""
    normalized = process_name.strip().lower()
    if not normalized:
        return ""
    return PurePosixPath(normalized).name


def _contains_marker(lines: list[str], markers: tuple[str, ...]) -> bool:
    haystack = "\n".join(lines).lower()
    return any(marker in haystack for marker in markers)


def _contains_marker_recent(lines: list[str], markers: tuple[str, ...], sample_size: int = 16) -> bool:
    """Check markers in recent lines only to avoid stale scrollback latching state."""
    non_empty = [line.strip().lower() for line in lines if line.strip()]
    if not non_empty:
        return False
    haystack = "\n".join(non_empty[-sample_size:])
    return any(marker in haystack for marker in markers)


def _looks_like_codex_ui(lines: list[str]) -> bool:
    if not lines:
        return False
    return (
        _contains_marker(lines, _CODEX_BANNER_MARKERS)
        or _contains_marker(lines, _CODEX_FOOTER_MARKERS)
        or _has_prompt(lines)
    )


def has_explicit_codex_prompt(output: str) -> bool:
    """True when pane content strongly indicates active Codex UI prompt."""
    lines = _normalize_lines(output or "")
    return _looks_like_codex_ui(lines) and _has_prompt(lines)


def is_codex_runtime_process(proc_name: str | None, output: str) -> bool:
    """Detect whether current process likely represents Codex interactive runtime."""
    proc = _process_basename(proc_name)
    if any(name in proc for name in _CODEX_PROCESS_NAMES):
        return True
    if proc in _CODEX_WRAPPER_PROCESS_NAMES:
        return has_explicit_codex_prompt(output)
    return False


def _low_activity(last_activity_ts: float | None, min_idle_seconds: float = 1.5) -> bool:
    if last_activity_ts is None:
        return True
    return (time.time() - last_activity_ts) >= min_idle_seconds


def detect_runner_state(
    output: str,
    process_name: str | None,
    last_activity_ts: float | None,
) -> RunnerState:
    """Detect runner state from pane output + running process metadata."""
    lines = _normalize_lines(output or "")
    has_prompt = _has_prompt(lines)
    is_working = _contains_marker_recent(lines, _WORKING_MARKERS)
    is_sleeping = _contains_marker_recent(lines, _SLEEPING_MARKERS)
    low_activity = _low_activity(last_activity_ts)

    proc = _process_basename(process_name)
    codex_running = is_codex_runtime_process(process_name, output or "")

    if is_working:
        return "working"
    if is_sleeping:
        return "sleeping"

    # In real Codex panes, pane_current_command or child-process inspection can
    # surface an MCP helper (npm/node/python) instead of the Codex runtime.
    # If the visible pane clearly looks like Codex UI and has a prompt, trust
    # the rendered UI over the current child process name.
    if _looks_like_codex_ui(lines) and has_prompt:
        return "idle"

    if not codex_running:
        if proc in {"", "zsh", "bash", "sh", "/bin/zsh", "/bin/bash", "/bin/sh"}:
            return "exited"
        # Keep explicit prompts as unknown so callers can decide whether to resume work.
        if has_prompt:
            return "unknown"
        # Non-codex processes that go quiet should not pin the runner forever.
        if low_activity:
            return "exited"
        # If unknown process is running, prefer unknown over exited.
        return "unknown"

    if has_prompt:
        # Prompt visibility is the strongest idle signal for interactive Codex panes.
        return "idle"
    if lines and codex_running:
        return "working"
    if low_activity and codex_running:
        return "idle"
    return "unknown"
