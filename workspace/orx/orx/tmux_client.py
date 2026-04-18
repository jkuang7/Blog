"""Minimal tmux transport wrapper for ORX."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path


DEFAULT_TMUX_CODEX_SOCKET = "/tmp/tmux-codex.sock"


class TmuxClient:
    def __init__(self, socket: str | None = None) -> None:
        self.socket = (
            socket
            or os.environ.get("ORX_TMUX_SOCKET")
            or os.environ.get("TMUX_CODEX_SOCKET")
            or DEFAULT_TMUX_CODEX_SOCKET
        )

    def _run(self, *args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("TMUX", None)
        return subprocess.run(
            ["tmux", "-S", self.socket, *args],
            env=env,
            capture_output=capture,
            text=True,
            check=False,
        )

    def has_session(self, name: str) -> bool:
        return self._run("has-session", "-t", name).returncode == 0

    def create_session(self, name: str, cmd: str) -> str:
        result = self._run("new-session", "-d", "-s", name, cmd)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "tmux new-session failed")
        panes = self._run("list-panes", "-t", name, "-F", "#{pane_id}")
        pane_id = panes.stdout.strip().splitlines()[0]
        if not pane_id:
            raise RuntimeError("tmux returned no pane id")
        return pane_id

    def kill_session(self, name: str) -> bool:
        return self._run("kill-session", "-t", name).returncode == 0

    def send_keys(self, session: str, text: str, *, enter: bool = True) -> bool:
        use_buffer = "\n" in text or len(text) > 512
        target = f"{session}:0.0"
        if use_buffer:
            buffer_name = f"orx-{int(time.time() * 1000)}"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                if self._run("load-buffer", "-b", buffer_name, tmp_path).returncode != 0:
                    return False
                if self._run("paste-buffer", "-d", "-b", buffer_name, "-t", target).returncode != 0:
                    return False
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            if self._run("send-keys", "-t", target, "-l", text).returncode != 0:
                return False
        if enter:
            return self._run("send-keys", "-t", target, "Enter").returncode == 0
        return True

    def capture_pane(self, session: str, *, lines: int = 50) -> str:
        result = self._run("capture-pane", "-t", f"{session}:0.0", "-p", "-S", f"-{lines}")
        return result.stdout if result.returncode == 0 else ""

    def list_panes(self, session: str) -> list[str]:
        result = self._run("list-panes", "-t", session, "-F", "#{pane_id}")
        if result.returncode != 0:
            return []
        return [pane for pane in result.stdout.splitlines() if pane.strip()]
