"""Interactive session menu for local Codex and Telecodex runner state."""

from __future__ import annotations

import curses
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tmux import TmuxClient

from .runner_state import codex_home
from .telecodex_status import TelecodexRunner, discover_telecodex_runners


def _delete_word(text: str) -> str:
    text = text.rstrip()
    if " " in text:
        return text.rsplit(" ", 1)[0]
    return ""


def _runner_letter_map(runners: list[TelecodexRunner]) -> dict[int, str]:
    letters: dict[int, str] = {}
    next_letter = ord("a")
    for idx, runner in enumerate(runners):
        if not runner.db_path:
            continue
        letters[idx] = chr(next_letter)
        next_letter += 1
        if next_letter > ord("z"):
            break
    return letters


class SessionMenu:
    """Manage tmux Codex sessions and show Telecodex runner sessions."""

    def __init__(self, tmux: "TmuxClient"):
        self.tmux = tmux
        self.sessions: list[str] = []
        self.pane_titles: list[str | None] = []
        self.telecodex_runners: list[TelecodexRunner] = []
        self._poll_count = 0
        self._tags_cache: dict[str, str] | None = None

    @property
    def _dev_path(self) -> Path:
        return Path(os.environ.get("DEV", "/Users/jian/Dev"))

    @property
    def _codex_home(self) -> Path:
        return codex_home(str(self._dev_path))

    def _load_sessions(self) -> None:
        self.sessions = self.tmux.list_sessions(prefix="codex")
        self.pane_titles = [self.tmux.get_pane_title(sess) for sess in self.sessions]
        self.telecodex_runners = discover_telecodex_runners(self._dev_path)

    def _get_tags_path(self) -> Path:
        return self._codex_home / "session-tags.json"

    def _load_tags(self, force: bool = False) -> dict[str, str]:
        if not force and self._tags_cache is not None:
            return self._tags_cache
        tags_path = self._get_tags_path()
        if tags_path.exists():
            try:
                data = json.loads(tags_path.read_text())
                if isinstance(data, dict):
                    self._tags_cache = {str(k): str(v) for k, v in data.items()}
                    return self._tags_cache
            except (OSError, json.JSONDecodeError):
                pass
        self._tags_cache = {}
        return self._tags_cache

    def _save_tags(self, tags: dict[str, str]) -> None:
        tags_path = self._get_tags_path()
        tags_path.parent.mkdir(parents=True, exist_ok=True)
        tags_path.write_text(json.dumps(tags, indent=2))
        self._tags_cache = tags

    def _create_new_session(self, extra_args: str = "") -> str | None:
        workdir = os.getcwd()
        sess_name = self.tmux.next_session_name(prefix="codex")
        cmd = (
            f'cd "{workdir}" && '
            f"codex --search --dangerously-bypass-approvals-and-sandbox {extra_args}; "
            "clear; exec zsh -l"
        )
        self.tmux.create_session(sess_name, cmd)
        self.sessions.append(sess_name)
        self.pane_titles.append(None)
        return sess_name

    def _create_runner_view_session(self, runner: TelecodexRunner) -> str | None:
        if not runner.db_path:
            return None
        workdir = str(self._dev_path / "workspace" / "tmux-codex")
        sess_name = self.tmux.next_session_name(prefix="codex")
        viewer_parts = [
            "python3",
            "-m",
            "src.telecodex_viewer",
            "--db",
            str(runner.db_path),
            "--chat-id",
            str(runner.chat_id),
            "--thread-id",
            str(runner.thread_id),
            "--title",
            runner.display_title,
        ]
        cmd = f"cd {shlex.quote(workdir)} && {shlex.join(viewer_parts)}; clear; exec zsh -l"
        self.tmux.create_session(sess_name, cmd)
        self.sessions.append(sess_name)
        self.pane_titles.append(runner.display_title)
        return sess_name

    def _get_session_descendants(self, sess_name: str) -> set[str]:
        result = self.tmux._run("list-panes", "-t", sess_name, "-F", "#{pane_pid}")
        if result.returncode != 0:
            return set()
        pane_pid = result.stdout.strip()
        if not pane_pid:
            return set()

        descendants: set[str] = set()
        to_check = [pane_pid]
        while to_check:
            pid = to_check.pop()
            result = subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True)
            if result.returncode == 0:
                for child in result.stdout.strip().split("\n"):
                    if child and child not in descendants:
                        descendants.add(child)
                        to_check.append(child)
        return descendants

    def _kill_orphaned_pids(self, pids: set[str]) -> None:
        for pid in pids:
            if subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0:
                subprocess.run(["kill", "-9", pid], capture_output=True)

    def _kill_sessions(self, indices: list[int]) -> None:
        all_descendants: set[str] = set()
        sessions_to_kill: list[tuple[int, str]] = []
        for idx in sorted(indices, reverse=True):
            if 1 <= idx <= len(self.sessions):
                i = idx - 1
                sess = self.sessions[i]
                all_descendants.update(self._get_session_descendants(sess))
                sessions_to_kill.append((i, sess))

        for i, sess in sessions_to_kill:
            if self.tmux.kill_session(sess):
                del self.sessions[i]
                del self.pane_titles[i]

        if all_descendants:
            self._kill_orphaned_pids(all_descendants)

    def _needs_attention(self, sess_name: str) -> bool:
        content = self.tmux.capture_pane(sess_name, lines=30)
        if not content:
            return False
        return any(
            pattern in content
            for pattern in ("permission_prompt", "⚠ Context preserved", "Press enter to continue")
        )

    def _get_display_title(self, idx: int, sess_name: str) -> str:
        title = self.pane_titles[idx] if idx < len(self.pane_titles) else None
        if title:
            title = title.lstrip("*✳ ")
        if not title or title in ("zsh", "bash", "codex", ""):
            title = "(no title)"

        tag = self._load_tags().get(sess_name)
        if tag:
            return f"{tag}: {title}"
        return title

    def _runner_summary(self) -> str | None:
        count = len(self.telecodex_runners)
        if count == 0:
            return None
        busy = sum(1 for runner in self.telecodex_runners if runner.busy)
        if busy:
            return f"Telecodex runners: {busy} busy / {count} tracked"
        return f"Telecodex runners: {count} tracked"

    def _categorize_sessions(self) -> tuple[list[tuple[int, str]], list[TelecodexRunner]]:
        return list(enumerate(self.sessions)), self.telecodex_runners

    def _safe_curses_setup(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def _safe_addstr(self, stdscr, row: int, col: int, text: str, *attrs) -> bool:
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
            stdscr.addstr(row, col, rendered, *attrs)
            return True
        except curses.error:
            return False

    def _draw_menu(
        self,
        stdscr,
        mode: str = "normal",
        kill_input: str = "",
        tag_session: str = "",
        tag_input: str = "",
    ) -> None:
        stdscr.clear()
        self._tags_cache = None
        self._load_sessions()
        regular_sessions, runner_sessions = self._categorize_sessions()

        poll_time = time.strftime("%I:%M%p").lstrip("0").lower()
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._poll_count += 1
        spinner = spinner_chars[self._poll_count % len(spinner_chars)]

        row = 1
        self._safe_addstr(stdscr, row, 2, "Codex Sessions", curses.A_BOLD)
        self._safe_addstr(stdscr, row, 22, f"{spinner} [{poll_time}]", curses.A_DIM)
        row += 2

        runner_summary = self._runner_summary()
        if runner_summary:
            self._safe_addstr(stdscr, row, 2, runner_summary, curses.A_DIM)
            row += 2

        if not self.sessions and not runner_sessions:
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
                    attrs = (curses.A_BOLD,) if attention else ()
                    self._safe_addstr(stdscr, row, 2, f"  {num}) {attention}{display}", *attrs)
                    row += 1

            if runner_sessions:
                row += 1
                self._safe_addstr(
                    stdscr,
                    row,
                    2,
                    "Runners (a-z=view when ready, Telecodex)",
                    curses.A_DIM,
                )
                row += 1
                runner_letters = _runner_letter_map(runner_sessions)
                for idx, runner in enumerate(runner_sessions[:26]):
                    letter = runner_letters.get(idx, "-")
                    cwd = f" [{runner.cwd}]" if runner.cwd else ""
                    self._safe_addstr(
                        stdscr,
                        row,
                        2,
                        f"  {letter}) {runner.status_icon} {runner.display_title}{cwd}",
                    )
                    row += 1

        row += 1
        self._regular_sessions = regular_sessions
        self._runner_sessions = runner_sessions

        if mode == "kill":
            text = (
                f"k{kill_input}_ (Enter=confirm, Esc=cancel)"
                if kill_input
                else "k_ (1-9 | Esc=cancel)"
            )
            self._safe_addstr(stdscr, row, 2, text, curses.A_DIM)
        elif mode == "tag_select":
            self._safe_addstr(stdscr, row, 2, "t_ (1-9 to select | Esc=cancel)", curses.A_DIM)
        elif mode == "tag_input":
            current = self._load_tags().get(tag_session, "")
            hint = f" [was: {current}]" if current else ""
            self._safe_addstr(
                stdscr,
                row,
                2,
                f"Tag: {tag_input}_{hint} (Enter=save, Esc=cancel)",
                curses.A_DIM,
            )
        else:
            self._safe_addstr(
                stdscr,
                row,
                2,
                "n=new | a-z=view runner | t=tag | k=kill | q=quit",
                curses.A_DIM,
            )
        stdscr.refresh()

    def run(self):
        try:
            result = curses.wrapper(self._run_curses)
            if result is not None:
                return result
        except curses.error as error:
            print(f"Unable to open interactive session menu: {error}")
        return None

    def _run_curses(self, stdscr):
        self._safe_curses_setup()
        stdscr.timeout(1000)
        mode = "normal"
        kill_input = ""
        tag_session = ""
        tag_input = ""

        while True:
            self._draw_menu(stdscr, mode, kill_input, tag_session, tag_input)
            key = stdscr.getch()
            if key == -1:
                continue
            ch = chr(key) if 0 <= key < 256 else ""

            if mode == "kill":
                if key == 27:
                    mode = "normal"
                    kill_input = ""
                elif ch in ("\n", "\r"):
                    indices = [int(c) for c in kill_input if c.isdigit()]
                    self._kill_sessions(indices)
                    mode = "normal"
                    kill_input = ""
                elif ch.isdigit():
                    kill_input += ch
                continue

            if mode == "tag_select":
                if key == 27:
                    mode = "normal"
                elif ch.isdigit() and ch != "0":
                    idx = int(ch) - 1
                    if 0 <= idx < len(self.sessions):
                        tag_session = self.sessions[idx]
                        tag_input = ""
                        mode = "tag_input"
                continue

            if mode == "tag_input":
                if key == 27:
                    mode = "normal"
                    tag_session = ""
                    tag_input = ""
                elif ch in ("\n", "\r"):
                    tags = self._load_tags(force=True)
                    if tag_input.strip():
                        tags[tag_session] = tag_input.strip()
                    else:
                        tags.pop(tag_session, None)
                    self._save_tags(tags)
                    mode = "normal"
                    tag_session = ""
                    tag_input = ""
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    tag_input = tag_input[:-1]
                elif key == 23:
                    tag_input = _delete_word(tag_input)
                elif ch and ch.isprintable():
                    tag_input += ch
                continue

            if ch == "q" or key == 27:
                return None
            if ch == "n":
                return ("attach", self._create_new_session())
            if ch == "k":
                mode = "kill"
                kill_input = ""
                continue
            if ch == "t":
                mode = "tag_select"
                continue
            if ch.isdigit() and ch != "0":
                idx = int(ch) - 1
                if 0 <= idx < len(self.sessions):
                    return ("attach", self.sessions[idx])
            if "a" <= ch <= "z":
                runner_letters = _runner_letter_map(self.telecodex_runners)
                for idx, letter in runner_letters.items():
                    if letter != ch:
                        continue
                    sess_name = self._create_runner_view_session(self.telecodex_runners[idx])
                    if sess_name:
                        return ("attach", sess_name)
                    break
