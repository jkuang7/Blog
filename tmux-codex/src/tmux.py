"""Tmux operations wrapper."""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .config import CL_SOCK

LINES_STATUS = 20
LINES_HEALTH = 50
LINES_FULL = 500


class TmuxClient:
    """Wrapper for tmux operations using dedicated socket."""

    def __init__(self, socket: str = CL_SOCK, config: Path | None = None):
        self.socket = socket
        self.config = config

    def _run(self, *args, capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
        """Run tmux command with socket."""
        # Unset TMUX to avoid nested session errors
        env = os.environ.copy()
        env.pop("TMUX", None)

        cmd = ["tmux", "-S", self.socket]
        if self.config and args and args[0] == "new-session":
            cmd.extend(["-f", str(self.config)])
        cmd.extend(args)

        return subprocess.run(
            cmd,
            env=env,
            capture_output=capture,
            text=True,
            check=check,
        )

    def list_sessions(self, prefix: str | None = None) -> list[str]:
        """List session names, optionally filtered by prefix.

        Args:
            prefix: If provided, only return sessions starting with this prefix.
                   Sessions are sorted numerically (e.g., codex-1, codex-2).
        """
        result = self._run("ls", "-F", "#{session_name}")
        if result.returncode != 0:
            return []

        sessions = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

        # Filter by prefix if provided
        if prefix:
            sessions = [s for s in sessions if s.startswith(f"{prefix}-") or s.startswith(f"runner-")]

        # Sort: prefix-N numerically first, then others alphabetically
        sort_prefix = prefix or "codex"
        def sort_key(name: str):
            match = re.match(rf"^{re.escape(sort_prefix)}-(\d+)$", name)
            if match:
                return (0, int(match.group(1)))
            return (1, name)

        return sorted(sessions, key=sort_key)

    def has_session(self, name: str) -> bool:
        """Check if session exists."""
        result = self._run("has-session", "-t", name)
        return result.returncode == 0

    def create_session(self, name: str, cmd: str) -> str:
        """Create detached session, return pane_id."""
        result = self._run("new-session", "-d", "-s", name, cmd)
        if result.returncode != 0:
            err = result.stderr.strip() or "unknown tmux error"
            raise RuntimeError(f"tmux new-session failed for {name}: {err}")

        panes = self._run("list-panes", "-t", name, "-F", "#{pane_id}")
        if panes.returncode != 0:
            err = panes.stderr.strip() or "unknown tmux error"
            raise RuntimeError(f"tmux list-panes failed for {name}: {err}")

        pane_id = panes.stdout.strip().split("\n")[0]
        if not pane_id:
            raise RuntimeError(f"tmux list-panes returned no pane id for {name}")
        return pane_id

    def attach(self, session: str):
        """Attach to session (returns when detached)."""
        env = os.environ.copy()
        env.pop("TMUX", None)
        subprocess.run(
            ["tmux", "-S", self.socket, "attach", "-t", session],
            env=env,
        )

    def kill_session(self, session: str) -> bool:
        """Kill session, return success."""
        result = self._run("kill-session", "-t", session)
        return result.returncode == 0

    def get_pane_title(self, session: str) -> str | None:
        """Get pane title (set by program via escape sequences)."""
        result = self._run("display-message", "-t", session, "-p", "#{pane_title}")
        if result.returncode != 0:
            return None
        title = result.stdout.strip()
        return title if title else None

    def next_session_name(self, prefix: str = "codex") -> str:
        """Get next available session name like codex-N."""
        n = 1
        while self.has_session(f"{prefix}-{n}"):
            n += 1
        return f"{prefix}-{n}"

    def capture_pane(self, session: str, lines: int = 50) -> str | None:
        """Capture last N lines of pane content."""
        result = self._run("capture-pane", "-t", session, "-p", "-S", f"-{lines}")
        if result.returncode != 0:
            return None
        return result.stdout

    def _resolve_target(self, session_or_target: str) -> str:
        """Resolve tmux target; session names map to first pane."""
        if session_or_target.startswith("%") or ":" in session_or_target:
            return session_or_target
        return f"{session_or_target}:0.0"

    def send_keys(self, session_or_target: str, text: str, enter: bool = True, delay_ms: int = 120) -> bool:
        """Send text to a pane with buffer-based paste for multiline/large payloads."""
        target = self._resolve_target(session_or_target)
        use_buffer = "\n" in text or len(text) > 512

        if use_buffer:
            buffer_name = f"cl-runner-{int(time.time() * 1000)}-{os.getpid()}"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                load = self._run("load-buffer", "-b", buffer_name, tmp_path)
                if load.returncode != 0:
                    return False
                paste = self._run("paste-buffer", "-d", "-b", buffer_name, "-t", target)
                if paste.returncode != 0:
                    return False
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            sent = self._run("send-keys", "-t", target, "-l", text)
            if sent.returncode != 0:
                return False

        if enter:
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)
            pressed = self._run("send-keys", "-t", target, "Enter")
            return pressed.returncode == 0
        return True

    def send_interrupt(self, session_or_target: str) -> bool:
        """Send Ctrl+C to pane."""
        target = self._resolve_target(session_or_target)
        result = self._run("send-keys", "-t", target, "C-c")
        return result.returncode == 0

    def clear_prompt_line(self, session_or_target: str) -> bool:
        """Clear current line in interactive prompt (Ctrl+U)."""
        target = self._resolve_target(session_or_target)
        result = self._run("send-keys", "-t", target, "C-u")
        return result.returncode == 0

    def list_panes(self, session: str) -> list[str]:
        """List pane ids in a session."""
        result = self._run("list-panes", "-t", session, "-F", "#{pane_id}")
        if result.returncode != 0:
            return []
        return [pane.strip() for pane in result.stdout.splitlines() if pane.strip()]

    def respawn_pane(self, target: str, cmd: str, kill: bool = True) -> bool:
        """Respawn pane with command."""
        resolved = self._resolve_target(target)
        args = ["respawn-pane"]
        if kill:
            args.append("-k")
        args.extend(["-t", resolved, cmd])
        result = self._run(*args)
        return result.returncode == 0

    def get_pane_process(self, session: str) -> str | None:
        """Get the current command running in the pane (checks child processes).

        Returns 'codex' if any child process is codex, otherwise returns
        the first non-shell child process name.
        """
        # Get pane PID
        result = self._run("list-panes", "-t", session, "-F", "#{pane_pid}")
        if result.returncode != 0:
            return None
        pane_pid = result.stdout.strip()
        if not pane_pid:
            return None

        # Check child processes of the pane shell
        import subprocess
        try:
            children = subprocess.run(
                ["pgrep", "-P", pane_pid],
                capture_output=True, text=True
            )
            if children.returncode == 0:
                child_procs = []
                for child_pid in children.stdout.strip().split('\n'):
                    if child_pid:
                        proc = subprocess.run(
                            ["ps", "-p", child_pid, "-o", "comm="],
                            capture_output=True, text=True
                        )
                        if proc.returncode == 0:
                            child_procs.append(proc.stdout.strip())

                # Priority: return 'codex' if present, else first non-shell
                for proc in child_procs:
                    if 'codex' in proc.lower():
                        return proc
                # Skip shells, return first real process
                for proc in child_procs:
                    if proc not in ('bash', 'sh', 'zsh', '/bin/bash', '/bin/sh', '/bin/zsh'):
                        return proc
                # Fallback to first child if all are shells
                if child_procs:
                    return child_procs[0]
        except Exception:
            pass

        # Fallback to pane_current_command
        result = self._run("list-panes", "-t", session, "-F", "#{pane_current_command}")
        if result.returncode != 0:
            return None
        return result.stdout.strip()
