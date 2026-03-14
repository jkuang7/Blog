"""Interactive session menu using curses for robust terminal handling.

LESSONS LEARNED (for debugging tmux-codex):

1. PROCESS RUNNING != CONVERSATION ACTIVE
   - Codex process can exist but be idle at prompt (❯)
   - Must check PANE CONTENT to know actual state, not just process existence
   - ps aux | grep codex → only tells you process exists
   - tmux capture-pane -t <session> -p → tells you what Codex is doing

2. RUNNER STATES:
   - 🟢 Active: Codex is thinking/working ("esc to interrupt", "Thinking...")
   - ⏸ Idle: Codex at prompt, waiting for input (process running but conversation done)
   - ✓ Done: No more tasks, runner exited cleanly
   - 💤 Sleeping: Backing off or waiting

3. TIMER STATES:
   - "Running: Xh Ym" → runners active (🟢), shows live elapsed from earliest start
   - "Idle (N runners paused)" → runners exist but all at prompt
   - "Last run: Xh Ym (ended HH:MM)" → no runners, shows last completed batch

4. DEBUGGING COMMANDS:
   - tmux capture-pane -t runner-<project> -p | tail -20  # See runner state
   - cat ~/.codex/logs/runners/runners.log               # Timer data
"""

import curses
import json
import os
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tmux import TmuxClient

from .runner_loop import (
    build_runner_paths,
    ensure_gates_file,
    make_codex_exec_loop_script,
    resolve_runner_profile,
)
from .runner_status import detect_runner_state
from .runctl import inspect_runner_start_state, resolve_target_project_root
from .runner_state import read_json
from .runner_state import codex_home
from .tmux import LINES_STATUS


def _delete_word(text: str) -> str:
    """Delete last word from text (for Option+Backspace)."""
    text = text.rstrip()
    if ' ' in text:
        return text.rsplit(' ', 1)[0]
    return ""


class SessionMenu:
    """Interactive menu for managing sessions."""

    def __init__(self, tmux: "TmuxClient"):
        self.tmux = tmux

        # Session state (populated in run)
        self.sessions: list[str] = []
        self.pane_titles: list[str | None] = []
        self._poll_count = 0  # Increments each poll for spinner
        self._tags_cache: dict[str, str] | None = None  # Cleared each draw cycle
    @property
    def _dev_path(self) -> Path:
        """Get DEV path (cached)."""
        return Path(os.environ.get("DEV", "/Users/jian/Dev"))

    @property
    def _codex_home(self) -> Path:
        return codex_home(str(self._dev_path))

    def _load_sessions(self):
        """Load sessions from tmux."""
        self.sessions = self.tmux.list_sessions(prefix="codex")
        self.pane_titles = [self.tmux.get_pane_title(sess) for sess in self.sessions]

    def _get_runner_prefs_path(self) -> Path:
        """Get path to runner preferences file."""
        return self._codex_home / "config" / "runner-prefs.json"

    def _load_runner_prefs_data(self) -> dict:
        """Load raw runner preferences JSON."""
        prefs_path = self._get_runner_prefs_path()
        if prefs_path.exists():
            try:
                data = json.loads(prefs_path.read_text())
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, KeyError):
                pass
        return {}

    def _load_runner_prefs(self) -> set[str]:
        """Load enabled projects from prefs file."""
        data = self._load_runner_prefs_data()
        enabled = data.get("enabled_projects", [])
        if isinstance(enabled, list):
            return {str(item) for item in enabled}
        return set()

    def _load_runner_complexity(self) -> str:
        """Load persisted runner complexity preference."""
        data = self._load_runner_prefs_data()
        value = str(data.get("preferred_complexity", "high")).lower()
        if value in {"low", "med", "high", "xhigh"}:
            return value
        return "high"

    def _save_runner_prefs(self, enabled: set[str], preferred_complexity: str):
        """Save enabled projects and preferred complexity to prefs file."""
        prefs_path = self._get_runner_prefs_path()
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(
            json.dumps(
                {
                    "enabled_projects": sorted(enabled),
                    "preferred_complexity": preferred_complexity,
                },
                indent=2,
            )
        )

    def _persist_runner_picker_state(self, enabled: set[str], complexity: str) -> None:
        """Persist in-progress picker state so manual toggles survive reopening the menu."""
        self._save_runner_prefs(set(enabled), complexity)

    def _get_tags_path(self) -> Path:
        """Get path to session tags file."""
        return self._codex_home / "session-tags.json"

    def _load_tags(self, force: bool = False) -> dict[str, str]:
        """Load session tags from JSON file (cached per draw cycle)."""
        if not force and self._tags_cache is not None:
            return self._tags_cache
        tags_path = self._get_tags_path()
        if tags_path.exists():
            try:
                self._tags_cache = json.loads(tags_path.read_text())
                return self._tags_cache
            except (json.JSONDecodeError, KeyError):
                pass
        self._tags_cache = {}
        return self._tags_cache

    def _save_tags(self, tags: dict[str, str]):
        """Save session tags to JSON file."""
        tags_path = self._get_tags_path()
        tags_path.parent.mkdir(parents=True, exist_ok=True)
        tags_path.write_text(json.dumps(tags, indent=2))
        self._tags_cache = tags  # Update cache

    def _get_all_projects(self) -> list[tuple[str, int]]:
        """Get all project folders with their todo task counts.

        Returns list of (project_name, todo_count) tuples, sorted by name.
        Scans Repos/ for projects that have .memory/ directories.
        """
        repos_dir = self._dev_path / "Repos"
        projects = []

        if repos_dir.exists():
            for folder in sorted(repos_dir.iterdir()):
                if folder.is_dir() and not folder.name.startswith('.'):
                    memory_dir = folder / ".memory"
                    if memory_dir.exists():
                        todo_count = self._count_pending_tasks_for_memory_dir(memory_dir)
                        projects.append((folder.name, todo_count))

        return projects

    def _runner_session_name_for_project(self, project: str) -> str:
        return f"runner-{project}"

    def _runner_active_lock_for_project(self, project: str) -> Path:
        return self._dev_path / "Repos" / project / ".memory" / "runner" / "locks" / "RUNNER_ACTIVE.lock"

    def _project_has_running_runner(self, project: str, existing_sessions: set[str] | None = None) -> bool:
        session_name = self._runner_session_name_for_project(project)
        sessions = existing_sessions if existing_sessions is not None else set(self.tmux.list_sessions())
        active_lock = self._runner_active_lock_for_project(project)

        if session_name in sessions:
            status = self._get_runner_status(session_name)
            # Active thinking/background work counts as in-progress.
            if status in {"🔄", "💤"}:
                return True
            if active_lock.exists():
                # Lock exists but pane is idle/shell/done: unblock and recover.
                active_lock.unlink(missing_ok=True)
            return False

        if active_lock.exists():
            # Session is gone; remove stale lock to unblock future starts.
            active_lock.unlink(missing_ok=True)
            return False
        return False

    def _active_runner_projects(self, projects: list[str]) -> set[str]:
        sessions = set(self.tmux.list_sessions())
        return {project for project in projects if self._project_has_running_runner(project, sessions)}

    def _create_new_session(self, extra_args: str = "") -> str | None:
        """Create new session, return session name.

        LESSONS LEARNED:
        1. Always cd to workdir first - Codex needs .codex/commands/ for slash commands
           Without this, /run, /status, /spec get "Unknown slash command"
        2. Don't use -p flag - it runs non-interactively and exits after one prompt
        3. Never use `timeout` wrapper - it breaks TTY/PTY forwarding
        4. Let Codex use its normal full-screen TUI for interactive sessions
        5. Permissions controlled via codex.json ("permission": "allow")
        """
        dev = os.environ.get("DEV", "/Users/jian/Dev")
        workdir = os.getcwd()
        sess_name = self.tmux.next_session_name(prefix="codex")
        cmd = (
            f'cd "{workdir}" && '
            f'codex --search --dangerously-bypass-approvals-and-sandbox {extra_args}; '
            "clear; exec zsh -l"
        )

        self.tmux.create_session(sess_name, cmd)

        # Add to local state
        self.sessions.append(sess_name)
        self.pane_titles.append(None)

        return sess_name

    def _get_session_descendants(self, sess_name: str) -> set[str]:
        """Get all descendant PIDs of a session's pane process."""
        # Get pane PID
        result = self.tmux._run("list-panes", "-t", sess_name, "-F", "#{pane_pid}")
        if result.returncode != 0:
            return set()
        pane_pid = result.stdout.strip()
        if not pane_pid:
            return set()

        # Recursively get all descendants
        descendants = set()
        to_check = [pane_pid]
        while to_check:
            pid = to_check.pop()
            result = subprocess.run(
                ["pgrep", "-P", pid],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                for child in result.stdout.strip().split("\n"):
                    if child and child not in descendants:
                        descendants.add(child)
                        to_check.append(child)
        return descendants

    def _kill_orphaned_pids(self, pids: set[str]):
        """Kill PIDs that are still alive (orphaned after session death)."""
        for pid in pids:
            # Check if still alive
            result = subprocess.run(["kill", "-0", pid], capture_output=True)
            if result.returncode == 0:
                # Still alive - kill it
                subprocess.run(["kill", "-9", pid], capture_output=True)

    def _kill_sessions(self, indices: list[int]):
        """Kill sessions by indices (1-indexed)."""
        killed_runners = []

        # Collect descendants BEFORE killing sessions
        all_descendants: set[str] = set()
        sessions_to_kill = []
        for idx in sorted(indices, reverse=True):
            if 1 <= idx <= len(self.sessions):
                i = idx - 1
                sess = self.sessions[i]
                all_descendants.update(self._get_session_descendants(sess))
                sessions_to_kill.append((i, sess))

        # Kill sessions
        for i, sess in sessions_to_kill:
            if self.tmux.kill_session(sess):
                if sess.startswith("runner"):
                    killed_runners.append(sess)
                del self.sessions[i]
                del self.pane_titles[i]

        # Clean up orphaned processes (MCP servers, browsers, etc.)
        if all_descendants:
            self._kill_orphaned_pids(all_descendants)

        # Record end time for any killed runners
        if killed_runners:
            self._record_runner_end_times(killed_runners)

    def _kill_all_runners(self) -> int:
        """Kill all runner sessions. Returns count killed."""
        # Collect descendants BEFORE killing
        all_descendants: set[str] = set()
        runners_to_kill = []
        for i in range(len(self.sessions) - 1, -1, -1):
            if self.sessions[i].startswith("runner"):
                sess_name = self.sessions[i]
                all_descendants.update(self._get_session_descendants(sess_name))
                runners_to_kill.append((i, sess_name))

        # Kill sessions
        killed_names = []
        killed = 0
        for i, sess_name in runners_to_kill:
            if self.tmux.kill_session(sess_name):
                killed_names.append(sess_name)
                del self.sessions[i]
                del self.pane_titles[i]
                killed += 1

        # Clean up orphaned processes (MCP servers, browsers, etc.)
        if all_descendants:
            self._kill_orphaned_pids(all_descendants)

        # Record end time for killed runners in log
        if killed_names:
            self._record_runner_end_times(killed_names)

        return killed

    def _record_runner_end_times(self, runner_names: list[str]):
        """Record end times for killed runners in the log."""
        log_file = self._codex_home / "logs" / "runners" / "runners.log"

        if not log_file.exists():
            return

        try:
            end_time = str(int(time.time()))
            lines = log_file.read_text().strip().split("\n")
            updated = []

            for line in lines:
                parts = line.strip().split(",")
                # If this is a running entry (no end time) for a killed runner
                if (len(parts) >= 2 and parts[0] in runner_names and
                    (len(parts) < 3 or not parts[2])):
                    # Add end time
                    updated.append(f"{parts[0]},{parts[1]},{end_time}")
                else:
                    updated.append(line)

            log_file.write_text("\n".join(updated) + "\n")
        except Exception:
            pass

    def _needs_attention(self, sess_name: str) -> bool:
        """Check if session needs user attention."""
        content = self.tmux.capture_pane(sess_name, lines=30)
        if not content:
            return False

        attention_patterns = [
            "permission_prompt",
            "⚠ Context preserved",
            "Press enter to continue",
        ]
        for pattern in attention_patterns:
            if pattern in content:
                return True
        return False

    def _get_runner_status(self, sess_name: str) -> str:
        """Get runner status icon from normalized runner-state detection."""
        content = self.tmux.capture_pane(sess_name, lines=LINES_STATUS)
        if not isinstance(content, str):
            content = ""
        proc = self.tmux.get_pane_process(sess_name)

        if "No todo tasks remaining" in content:
            return "✓"
        if sess_name.startswith("runner-"):
            project = sess_name.replace("runner-", "", 1)
            done_lock = self._dev_path / "Repos" / project / ".memory" / "runner" / "locks" / "RUNNER_DONE.lock"
            if done_lock.exists():
                return "✓"

        state = detect_runner_state(output=content, process_name=proc, last_activity_ts=None)
        if state == "working":
            return "🔄"
        if state == "idle":
            return "⏸"
        if state == "sleeping":
            return "💤"
        if state == "exited":
            return "⏸"
        return "⚫"

    def _get_display_title(self, idx: int, sess_name: str) -> str:
        """Get display title for session.

        Format: "tag: pane_title" if tagged, else just "pane_title"
        Tags are persistent user labels, pane_title auto-updates from tmux.
        """
        # For runner sessions, show session name directly (no tags)
        if sess_name.startswith("runner"):
            return sess_name.replace("runner-", "") if sess_name != "runner" else "(all)"

        title = self.pane_titles[idx] if idx < len(self.pane_titles) else None
        if title:
            title = title.lstrip("*✳ ")
        if not title or title in ("zsh", "bash", "codex", ""):
            title = "(no title)"

        # Check for tag
        tags = self._load_tags()
        tag = tags.get(sess_name)
        if tag:
            return f"{tag}: {title}"
        return title

    def _count_todo_tasks(self) -> int:
        """Count pending TASKS.json items across all projects."""
        repos_dir = self._dev_path / "Repos"
        count = 0
        if repos_dir.exists():
            for tasks_file in repos_dir.glob("*/.memory/runner/TASKS.json"):
                try:
                    payload = json.loads(tasks_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                tasks = payload.get("tasks")
                if not isinstance(tasks, list):
                    continue
                for raw in tasks:
                    if not isinstance(raw, dict):
                        continue
                    status = str(raw.get("status", "")).strip().lower()
                    if status in {"open", "in_progress", "blocked"}:
                        count += 1
        return count

    def _count_pending_tasks_for_memory_dir(self, memory_dir: Path) -> int:
        tasks_file = memory_dir / "runner" / "TASKS.json"
        if not tasks_file.exists():
            return 0
        try:
            payload = json.loads(tasks_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return 0
        pending = 0
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            status = str(raw.get("status", "")).strip().lower()
            if status in {"open", "in_progress", "blocked"}:
                pending += 1
        return pending

    def _get_runner_elapsed(self) -> str | None:
        """Get elapsed time info for runners.

        Returns:
        - "Running: Xh Ym" if runners are active (live timer)
        - "Last run: Xh Ym (ended HH:MMam)" if all runners finished
        - None if no runner log exists
        """
        log_file = self._codex_home / "logs" / "runners" / "runners.log"

        if not log_file.exists():
            return None

        try:
            lines = log_file.read_text().strip().split("\n")
            if not lines or lines == [""]:
                return None

            # Parse entries - separate running (no end) from completed
            running = []  # start times of active runs
            completed = []  # (start, end) of finished runs

            for line in lines:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[1]:
                    try:
                        start = int(parts[1])
                        if len(parts) >= 3 and parts[2]:
                            end = int(parts[2])
                            completed.append((start, end))
                        else:
                            running.append(start)
                    except ValueError:
                        pass

            # Check if runners are ACTUALLY active (not just existing but idle)
            # A runner is active if Claude is thinking/working, not idle at prompt
            active_runners = []
            for s in self.sessions:
                if s.startswith("runner"):
                    status = self._get_runner_status(s)
                    if status == "🔄":  # Only count truly active runners
                        active_runners.append(s)

            # Count all runner sessions (active or idle)
            all_runners = [s for s in self.sessions if s.startswith("runner")]

            if active_runners and running:
                # Live timer - show elapsed from earliest running start
                earliest_start = min(running)
                elapsed_secs = int(time.time()) - earliest_start
                hours = elapsed_secs // 3600
                minutes = (elapsed_secs % 3600) // 60

                if hours > 0:
                    return f"Running: {hours}h {minutes}m"
                else:
                    return f"Running: {minutes}m"

            if all_runners and not active_runners:
                # Runners exist but all idle - show idle state
                return f"Idle ({len(all_runners)} runners paused)"

            # No active runners - show last completed batch
            if not completed:
                return None

            # Filter out 0-duration entries (start == end means instant kill)
            completed = [(s, e) for s, e in completed if e > s]
            if not completed:
                return None

            # Sort by start time
            completed.sort(key=lambda x: x[0])

            # Find batches - consecutive entries starting within 120s
            batches = []
            current_batch = [completed[0]]
            for entry in completed[1:]:
                if entry[0] - current_batch[-1][0] < 120:
                    current_batch.append(entry)
                else:
                    batches.append(current_batch)
                    current_batch = [entry]
            batches.append(current_batch)

            # Use the most recent batch
            last_batch = batches[-1]
            first_start = min(e[0] for e in last_batch)
            last_end = max(e[1] for e in last_batch)

            # Calculate elapsed
            elapsed_secs = last_end - first_start
            hours = elapsed_secs // 3600
            minutes = (elapsed_secs % 3600) // 60

            if hours > 0:
                elapsed_str = f"{hours}h {minutes}m"
            else:
                elapsed_str = f"{minutes}m"

            # Format end time
            end_time = time.localtime(last_end)
            hour_12 = end_time.tm_hour % 12 or 12
            am_pm = "am" if end_time.tm_hour < 12 else "pm"
            end_str = f"{hour_12}:{end_time.tm_min:02d}{am_pm}"

            return f"Elapsed: {elapsed_str} (ended {end_str})"

        except Exception:
            return None

    def _categorize_sessions(self):
        """Categorize sessions into regular and runners."""
        regular = []
        runners = []
        for i, sess in enumerate(self.sessions):
            if sess.startswith("runner-"):
                runners.append((i, sess))
            else:
                regular.append((i, sess))
        return regular, runners

    def _safe_curses_setup(self) -> None:
        """Best-effort curses setup for terminals with partial capability support."""
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def _safe_addstr(self, stdscr, row: int, col: int, text: str, *attrs) -> bool:
        """Write within screen bounds without throwing on narrow terminals."""
        try:
            max_y, max_x = stdscr.getmaxyx()
        except curses.error:
            return False

        if row < 0 or row >= max_y or col >= max_x:
            return False

        available = max_x - col
        if available <= 0:
            return False

        rendered = text if len(text) <= available else text[: max(0, available - 1)]
        try:
            if attrs:
                stdscr.addstr(row, col, rendered, *attrs)
            else:
                stdscr.addstr(row, col, rendered)
            return True
        except curses.error:
            return False

    def _draw_menu(self, stdscr, mode: str = "normal", kill_input: str = "",
                   tag_session: str = "", tag_input: str = ""):
        """Draw the menu on screen."""
        stdscr.clear()

        # Clear caches for this draw cycle
        self._tags_cache = None

        # Refresh session list and pane titles on every draw (1s polling)
        self._load_sessions()

        regular_sessions, runner_sessions = self._categorize_sessions()

        # Visual polling indicator - spinner + time shows refresh is happening
        poll_time = time.strftime("%I:%M%p").lstrip("0").lower()
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._poll_count += 1
        spinner = spinner_chars[self._poll_count % len(spinner_chars)]

        row = 1
        self._safe_addstr(stdscr, row, 2, "Codex Sessions", curses.A_BOLD)
        self._safe_addstr(stdscr, row, 22, f"{spinner} [{poll_time}]", curses.A_DIM)
        row += 2

        todo_count = self._count_todo_tasks()
        elapsed_info = self._get_runner_elapsed()

        if todo_count > 0:
            self._safe_addstr(stdscr, row, 2, f"{todo_count} tasks queued", curses.A_DIM)
            row += 1
        if elapsed_info:
            self._safe_addstr(stdscr, row, 2, elapsed_info, curses.A_DIM)
            row += 1
        if todo_count > 0 or elapsed_info:
            row += 1

        if not self.sessions:
            self._safe_addstr(stdscr, row, 2, "(no sessions)", curses.A_DIM)
            row += 1
        else:
            if regular_sessions:
                self._safe_addstr(stdscr, row, 2, "Sessions (1-9)", curses.A_DIM)
                row += 1
                for num, (orig_idx, sess) in enumerate(regular_sessions, 1):
                    if num > 9:
                        break
                    attention = "⚠ " if self._needs_attention(sess) else ""
                    display = self._get_display_title(orig_idx, sess)
                    line = f"  {num}) {attention}{display}"
                    if attention:
                        self._safe_addstr(stdscr, row, 2, line, curses.A_BOLD)
                    else:
                        self._safe_addstr(stdscr, row, 2, line)
                    row += 1

            if runner_sessions:
                row += 1
                self._safe_addstr(stdscr, row, 2, "Runners (a-z)", curses.A_DIM)
                row += 1
                for idx, (_orig_idx, sess) in enumerate(runner_sessions):
                    if idx >= 26:
                        break
                    letter = chr(ord("a") + idx)
                    status = self._get_runner_status(sess)
                    display = sess.replace("runner-", "") if sess != "runner" else "(all)"
                    self._safe_addstr(stdscr, row, 2, f"  {letter}) {status} {display}")
                    row += 1

        row += 1
        # Store for key handling
        self._regular_sessions = regular_sessions
        self._runner_sessions = runner_sessions

        # Help line
        if mode == "kill":
            if kill_input:
                self._safe_addstr(stdscr, row, 2, f"k{kill_input}_ (Enter=confirm, Esc=cancel)", curses.A_DIM)
            else:
                self._safe_addstr(stdscr, row, 2, "k_ (1-9 a-z mix | r=all runners | Esc=cancel)", curses.A_DIM)
        elif mode == "tag_select":
            self._safe_addstr(stdscr, row, 2, "t_ (1-9 to select | Esc=cancel)", curses.A_DIM)
        elif mode == "tag_input":
            # Show current tag for reference
            tags = self._load_tags()
            current = tags.get(tag_session, "")
            hint = f" [was: {current}]" if current else ""
            self._safe_addstr(stdscr, row, 2, f"Tag: {tag_input}_{hint} (Enter=save, Esc=cancel)", curses.A_DIM)
        else:
            self._safe_addstr(stdscr, row, 2, "n=new | r=runner | t=tag | k=kill | q=quit", curses.A_DIM)

        stdscr.refresh()

    def _run_project_selector(self, stdscr) -> tuple[list[str], str] | None:
        """Curses-based multi-select project picker.

        Returns (selected projects, complexity), or None if cancelled.
        """
        self._safe_curses_setup()
        stdscr.timeout(100)  # Fast refresh for responsive UI

        projects = self._get_all_projects()  # [(name, todo_count), ...]
        if not projects:
            return None

        # Load saved preferences
        saved_prefs = self._load_runner_prefs()
        # Initialize enabled state - use saved prefs, or all enabled if no prefs
        enabled = set()
        for name, _ in projects:
            if saved_prefs:
                if name in saved_prefs:
                    enabled.add(name)
            else:
                enabled.add(name)  # Default: all enabled

        cursor = 0  # Currently highlighted row
        complexity = self._load_runner_complexity()
        project_names = [name for name, _ in projects]
        active_projects = self._active_runner_projects(project_names)
        enabled = {name for name in enabled if name not in active_projects}

        while True:
            stdscr.clear()
            row = 1
            active_projects = self._active_runner_projects(project_names)
            enabled = {name for name in enabled if name not in active_projects}

            self._safe_addstr(stdscr, row, 2, "Start Runners - Select Projects", curses.A_BOLD)
            row += 2

            # Draw project list with checkboxes
            for idx, (name, todo_count) in enumerate(projects):
                is_active = name in active_projects
                # Checkbox
                checkbox = "[~]" if is_active else ("[x]" if name in enabled else "[ ]")
                # Highlight current row
                attr = curses.A_REVERSE if idx == cursor else 0
                if is_active:
                    attr |= curses.A_DIM
                # Task count suffix
                count_str = f"({todo_count} tasks)" if todo_count > 0 else "(0 tasks)"
                if is_active:
                    count_str += " [running]"
                # Number prefix (1-9)
                num = str(idx + 1) if idx < 9 else " "

                line = f"  {num}) {checkbox} {name:<20} {count_str}"
                self._safe_addstr(stdscr, row, 2, line, attr)
                row += 1

            row += 1
            # Help line
            self._safe_addstr(
                stdscr,
                row,
                2,
                "Space=toggle | a=all | n=none | Enter=start | Esc=cancel (locked=running)",
                curses.A_DIM,
            )

            stdscr.refresh()

            try:
                key = stdscr.getch()
            except:
                continue

            if key == -1:
                continue

            # Handle keys
            if key == 27:  # Escape
                return None
            elif key in (curses.KEY_ENTER, 10, 13):  # Enter
                if enabled:
                    self._save_runner_prefs(enabled, complexity)
                    return sorted(enabled), complexity
                return None
            elif key == ord(' '):  # Space - toggle current
                name = projects[cursor][0]
                if name in active_projects:
                    continue
                if name in enabled:
                    enabled.discard(name)
                else:
                    enabled.add(name)
                self._persist_runner_picker_state(enabled, complexity)
            elif key == ord('a') or key == ord('A'):  # Select all
                enabled = {name for name, _ in projects if name not in active_projects}
                self._persist_runner_picker_state(enabled, complexity)
            elif key == ord('n') or key == ord('N'):  # Select none
                enabled.clear()
                self._persist_runner_picker_state(enabled, complexity)
            elif key == curses.KEY_UP or key == ord('k'):
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_DOWN or key == ord('j'):
                cursor = min(len(projects) - 1, cursor + 1)
            elif 0 <= key < 256:
                ch = chr(key)
                if ch.isdigit() and ch != '0':
                    idx = int(ch) - 1
                    if 0 <= idx < len(projects):
                        name = projects[idx][0]
                        if name in active_projects:
                            continue
                        if name in enabled:
                            enabled.discard(name)
                        else:
                            enabled.add(name)
                        self._persist_runner_picker_state(enabled, complexity)

    def _fallback_project_selector(self) -> tuple[list[str], str] | None:
        """Text-based fallback project selector when curses fails."""
        projects = self._get_all_projects()
        if not projects:
            print("\n  No projects found in Repos/")
            return None

        saved_prefs = self._load_runner_prefs()
        enabled = set()
        for name, _ in projects:
            if saved_prefs:
                if name in saved_prefs:
                    enabled.add(name)
            else:
                enabled.add(name)

        print("\n🔄 Start Runners - Select Projects")
        print("  (Use arrows/space or numbers, Enter to start, q to cancel)\n")
        complexity = self._load_runner_complexity()
        project_names = [name for name, _ in projects]
        cursor = 0

        while True:
            active_projects = self._active_runner_projects(project_names)
            enabled = {name for name in enabled if name not in active_projects}
            for idx, (name, todo_count) in enumerate(projects):
                is_active = name in active_projects
                checkbox = "[~]" if is_active else ("[x]" if name in enabled else "[ ]")
                count_str = f"({todo_count} tasks)" if todo_count > 0 else "(0 tasks)"
                if is_active:
                    count_str += " [running]"
                marker = ">" if idx == cursor else " "
                print(f"{marker} {idx + 1}) {checkbox} {name:<20} {count_str}")

            print("\n  ↑/↓ move | Space=toggle | a=all | n=none | Enter=start | q=cancel (locked=running)")

            try:
                choice = self._read_fallback_project_selector_input()
            except (KeyboardInterrupt, EOFError):
                return None

            if choice == 'q':
                return None
            elif choice == '':
                if enabled:
                    self._save_runner_prefs(enabled, complexity)
                    return sorted(enabled), complexity
                return None
            elif choice == "__up__":
                cursor = max(0, cursor - 1)
            elif choice == "__down__":
                cursor = min(len(projects) - 1, cursor + 1)
            elif choice == "__toggle__":
                name = projects[cursor][0]
                if name in active_projects:
                    continue
                if name in enabled:
                    enabled.discard(name)
                else:
                    enabled.add(name)
                self._persist_runner_picker_state(enabled, complexity)
            elif choice == 'a':
                enabled = {name for name, _ in projects if name not in active_projects}
                self._persist_runner_picker_state(enabled, complexity)
            elif choice == 'n':
                enabled.clear()
                self._persist_runner_picker_state(enabled, complexity)
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(projects):
                    name = projects[idx][0]
                    if name in active_projects:
                        continue
                    if name in enabled:
                        enabled.discard(name)
                    else:
                        enabled.add(name)
                    self._persist_runner_picker_state(enabled, complexity)

            # Clear and redraw
            print("\033[2J\033[H")  # Clear screen

    def _read_fallback_project_selector_input(self) -> str:
        """Read one fallback picker action, supporting arrows when attached to a TTY."""
        print("\nToggle or action: ", end="", flush=True)
        fd: int | None = None
        using_tty_fd = False
        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
        else:
            try:
                fd = os.open("/dev/tty", os.O_RDWR)
                using_tty_fd = True
            except OSError:
                return input().strip().lower()

        old_settings = termios.tcgetattr(fd)

        def _read_char() -> str:
            if using_tty_fd:
                return os.read(fd, 1).decode("utf-8", errors="ignore")
            return sys.stdin.read(1)

        try:
            tty.setraw(fd)
            first = _read_char()
            if first in ("\r", "\n"):
                print()
                return ""
            if first in (" ",):
                print()
                return "__toggle__"
            if first == "\x1b":
                second = _read_char()
                third = _read_char()
                print()
                if second == "[" and third == "A":
                    return "__up__"
                if second == "[" and third == "B":
                    return "__down__"
                return "q"
            print(first)
            return first.strip().lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            if using_tty_fd:
                os.close(fd)

    def _start_runner_session(self) -> str | None:
        """Start loop runners for selected projects using project picker.

        Returns session name to attach when exactly one runner was started.
        """
        # Use curses project selector, fallback to text-based if curses fails
        try:
            picker_result = curses.wrapper(self._run_project_selector)
        except curses.error as e:
            print(f"[curses error: {e} - using fallback]")
            picker_result = self._fallback_project_selector()

        if not picker_result:
            return None
        targets, complexity = picker_result

        dev = os.environ.get("DEV", "/Users/jian/Dev")
        model, reasoning_effort = resolve_runner_profile(complexity, None)
        started_sessions: list[str] = []
        existing_sessions = set(self.tmux.list_sessions())

        for project in targets:
            if self._project_has_running_runner(project, existing_sessions):
                print(f"  Skipping {project}: runner already in progress")
                continue

            runner_id = "main"
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
            _ = gates_path
            _ = created_now

            ready_result = inspect_runner_start_state(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=project_root,
            )
            if not ready_result.get("ok"):
                print(
                    f"  Skipping {project}: {ready_result.get('error', 'runner not prepared')}"
                )
                continue

            sess_name = self._runner_session_name_for_project(project)
            if sess_name in existing_sessions:
                # Stale idle runner session; replace it with a fresh loop.
                self.tmux.kill_session(sess_name)
                existing_sessions.discard(sess_name)
            paths = build_runner_paths(
                dev=dev,
                project=project,
                runner_id=runner_id,
                project_root=project_root,
            )
            cmd = make_codex_exec_loop_script(
                dev=dev,
                project=project,
                runner_id=runner_id,
                model=model,
                reasoning_effort=reasoning_effort,
                paths=paths,
            )

            try:
                self.tmux.create_session(sess_name, cmd)
            except RuntimeError as e:
                print(f"  Failed to create runner {sess_name}: {e}")
                continue

            print(f"  Started {sess_name} ({complexity}, {model}, effort={reasoning_effort})")
            state_paths = getattr(paths, "state", None)
            state_file_path = getattr(state_paths, "state_file", None)
            state_data = read_json(state_file_path) if state_file_path is not None else {}
            print("    mode: interactive-cli")
            if isinstance(state_data, dict):
                print(f"    phase: {state_data.get('current_phase', 'discover')}")
            self.sessions.append(sess_name)
            self.pane_titles.append(None)
            started_sessions.append(sess_name)
            existing_sessions.add(sess_name)

        if not started_sessions:
            return None

        if len(started_sessions) > 1:
            print(f"  Multiple runners started; attaching to {started_sessions[0]}")
        return started_sessions[0]

    def _run_curses(self, stdscr):
        """Main curses loop."""
        self._safe_curses_setup()
        stdscr.timeout(1000)  # 1 second poll - each spinner change = one poll

        mode = "normal"
        kill_input = ""
        tag_session = ""  # Session name being tagged
        tag_input = ""    # Tag text being entered
        while True:
            self._draw_menu(stdscr, mode, kill_input, tag_session, tag_input)

            try:
                key = stdscr.getch()
            except:
                continue

            if key == -1:
                continue

            ch = chr(key) if 0 <= key < 256 else ""

            if mode == "kill":
                if ch.isdigit() or ch.isalpha():
                    kill_input += ch
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    kill_input = kill_input[:-1]
                elif key in (curses.KEY_ENTER, 10, 13):
                    if kill_input:
                        # Special: "r" alone = kill all runners
                        if kill_input.lower() == "r":
                            self._kill_all_runners()
                        else:
                            indices_to_kill = []
                            for c in kill_input:
                                if c.isdigit() and c != "0":
                                    num = int(c)
                                    if hasattr(self, '_regular_sessions') and num <= len(self._regular_sessions):
                                        orig_idx = self._regular_sessions[num - 1][0]
                                        indices_to_kill.append(orig_idx + 1)
                                elif c.isalpha():
                                    letter_idx = ord(c.lower()) - ord('a')
                                    if hasattr(self, '_runner_sessions') and letter_idx < len(self._runner_sessions):
                                        orig_idx = self._runner_sessions[letter_idx][0]
                                        indices_to_kill.append(orig_idx + 1)
                            self._kill_sessions(indices_to_kill)
                    mode = "normal"
                    kill_input = ""
                elif key == 27:
                    mode = "normal"
                    kill_input = ""

            elif mode == "tag_select":
                # Waiting for session number (1-9)
                if ch.isdigit() and ch != "0":
                    num = int(ch)
                    if hasattr(self, '_regular_sessions') and num <= len(self._regular_sessions):
                        orig_idx = self._regular_sessions[num - 1][0]
                        tag_session = self.sessions[orig_idx]
                        mode = "tag_input"
                        tag_input = ""  # Start empty - Enter immediately = remove tag
                elif key == 27:
                    mode = "normal"
                    tag_session = ""

            elif mode == "tag_input":
                # Typing tag text
                if key in (curses.KEY_ENTER, 10, 13):
                    # Save or remove tag
                    tags = self._load_tags()
                    if tag_input.strip():
                        tags[tag_session] = tag_input.strip()
                    elif tag_session in tags:
                        del tags[tag_session]  # Empty = remove tag
                    self._save_tags(tags)
                    mode = "normal"
                    tag_session = ""
                    tag_input = ""
                elif key == 27:
                    # Check for Alt+Backspace (ESC followed by Backspace)
                    # Use short timeout to catch escape sequences
                    curses.halfdelay(1)  # 100ms timeout
                    try:
                        next_key = stdscr.getch()
                    except:
                        next_key = -1
                    curses.cbreak()  # Restore normal mode
                    if next_key in (curses.KEY_BACKSPACE, 127, 8):
                        # Alt+Backspace: delete word
                        tag_input = _delete_word(tag_input)
                    elif next_key == -1:
                        # Plain Escape (timeout) - cancel
                        mode = "normal"
                        tag_session = ""
                        tag_input = ""
                    # else: ignore other escape sequences, continue editing
                elif key == 23:
                    # Option+Backspace on macOS sends key 23
                    tag_input = _delete_word(tag_input)
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    tag_input = tag_input[:-1]
                elif ch and ch.isprintable():
                    tag_input += ch

            else:
                if ch == "q" or key == 27:
                    return None
                elif ch == "n":
                    return ("attach", self._create_new_session())
                elif ch == "r":
                    return ("runner",)
                elif ch == "k":
                    mode = "kill"
                    kill_input = ""
                elif ch == "t":
                    mode = "tag_select"
                    tag_session = ""
                    tag_input = ""
                elif ch.isdigit() and ch != "0":
                    num = int(ch)
                    if hasattr(self, '_regular_sessions') and num <= len(self._regular_sessions):
                        orig_idx = self._regular_sessions[num - 1][0]
                        return ("attach", self.sessions[orig_idx])
                elif ch.isalpha() and ch.islower():
                    # a-z = runners
                    letter_idx = ord(ch) - ord('a')
                    if hasattr(self, '_runner_sessions') and letter_idx < len(self._runner_sessions):
                        orig_idx = self._runner_sessions[letter_idx][0]
                        return ("attach", self.sessions[orig_idx])

    def _fallback_menu(self) -> tuple | None:
        """Simple text-based fallback when curses fails."""
        print("\n=== Codex Sessions (fallback mode) ===\n")

        todo_count = self._count_todo_tasks()
        elapsed_info = self._get_runner_elapsed()
        if todo_count > 0:
            print(f"  {todo_count} tasks queued")
        if elapsed_info:
            print(f"  {elapsed_info}")
        if todo_count > 0 or elapsed_info:
            print()

        regular, runners = self._categorize_sessions()

        if not self.sessions:
            print("  No sessions found")
        else:
            if regular:
                print("Sessions:")
                for num, (orig_idx, sess) in enumerate(regular, 1):
                    title = self._get_display_title(orig_idx, sess)
                    print(f"  [{num}] {sess} - {title}")

            if runners:
                print("\nRunners:")
                for idx, (orig_idx, sess) in enumerate(runners):
                    letter = chr(ord('a') + idx)
                    status = self._get_runner_status(sess)
                    display = sess.replace("runner-", "")
                    print(f"  [{letter}] {status} {display}")

        print("\n  [n] New session")
        print("  [r] New runner")
        print("  [t] Tag session")
        print("  [q] Quit\n")

        try:
            choice = input("Choice: ").strip()  # Don't lowercase - need to distinguish a vs A
        except (EOFError, KeyboardInterrupt):
            return None

        if choice == 'q' or choice == 'Q' or choice == '':
            return None
        elif choice == 'n':
            sess_name = self._create_new_session()
            return ("attach", sess_name) if sess_name else None
        elif choice == 'r':
            return ("runner",)
        elif choice == 't':
            # Tag a session
            if not regular:
                print("  No sessions to tag")
                return ("continue",)
            try:
                sess_num = input("Session number to tag (1-9): ").strip()
                if sess_num.isdigit():
                    num = int(sess_num)
                    if 1 <= num <= len(regular):
                        orig_idx = regular[num - 1][0]
                        sess_name = self.sessions[orig_idx]
                        tags = self._load_tags()
                        current_tag = tags.get(sess_name, "")
                        tag_text = input(f"Tag [{current_tag}]: ").strip()
                        if tag_text:
                            tags[sess_name] = tag_text
                        elif sess_name in tags:
                            del tags[sess_name]
                        self._save_tags(tags)
                        print(f"  Tag saved.")
            except (EOFError, KeyboardInterrupt):
                pass
            return ("continue",)
        elif choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(regular):
                orig_idx = regular[num - 1][0]
                return ("attach", self.sessions[orig_idx])
        elif choice.isalpha() and choice.islower() and len(choice) == 1:
            letter_idx = ord(choice) - ord('a')
            if 0 <= letter_idx < len(runners):
                orig_idx = runners[letter_idx][0]
                return ("attach", self.sessions[orig_idx])
        return None

    def run(self):
        """Run interactive menu."""
        while True:
            self._load_sessions()

            try:
                result = curses.wrapper(self._run_curses)
            except curses.error as e:
                print(f"[curses error: {e} - using fallback menu]")
                result = self._fallback_menu()

            if not result:
                break

            if result[0] == "attach":
                sess_name = result[1]
                if sess_name:
                    if len(result) > 2:
                        print(result[2])
                    self.tmux.attach(sess_name)
                # After detach, loop back to menu (don't break)
            elif result[0] == "runner":
                attach_name = self._start_runner_session()
                if attach_name:
                    self.tmux.attach(attach_name)
                # Loop continues - back to menu
            elif result[0] == "continue":
                # Just loop back to menu (e.g., after tagging in fallback mode)
                pass
