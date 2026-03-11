#!/usr/bin/env python3
"""Integration smoke: two concurrent tmux runners on dummy projects."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runner_loop import build_runner_paths, make_codex_loop_script
from src.runner_state import default_runner_state, write_json
from src.tmux import TmuxClient


def _wait_for(label: str, predicate, timeout: float = 30.0, poll: float = 0.1) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise RuntimeError(f"Timed out waiting for: {label}")


def _write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, mode)


def _status(state_file: Path) -> str:
    if not state_file.exists():
        return ""
    try:
        return str(json.loads(state_file.read_text()).get("status", ""))
    except Exception:
        return ""


def _enable_state(paths, project: str, runner_id: str) -> None:
    state = default_runner_state(project=project, runner_id=runner_id)
    state["enabled"] = True
    state["status"] = "ready"
    write_json(paths.state_file, state)


def _disable_state(paths, project: str, runner_id: str) -> None:
    state = default_runner_state(project=project, runner_id=runner_id)
    state["enabled"] = False
    state["status"] = "init"
    write_json(paths.state_file, state)


def _latest_done_proposal(memory: Path, runner_id: str) -> Path | None:
    proposals = sorted(memory.glob(f"RUNNER_PROPOSAL.{runner_id}.done-*.json"))
    return proposals[-1] if proposals else None


def main() -> int:
    if shutil.which("tmux") is None:
        print("tmux not installed; skipping")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="tmux-codex-parallel-"))
    dev = tmp / "Dev"
    repos = dev / "Repos"
    proj_a = repos / "proj-a"
    proj_b = repos / "proj-b"
    bin_dir = tmp / "bin"
    socket = tmp / "tmux.sock"

    a_name = "runner-proj-a-a1"
    b_name = "runner-proj-b-b1"

    tmux = None
    try:
        # dummy projects + gates
        for proj in (proj_a, proj_b):
            memory = proj / ".memory"
            memory.mkdir(parents=True, exist_ok=True)
            _write(
                memory / "gates.sh",
                """#!/usr/bin/env bash
run_gates() {
  set -euo pipefail
  [[ -f ".memory/.gate-pass.${RUNNER_ID}" ]]
}
""",
                mode=0o755,
            )

        # codex stub: deterministic successful JSON stream.
        _write(
            bin_dir / "codex",
            """#!/usr/bin/env bash
set -euo pipefail
echo '{"type":"thread.started","thread_id":"stub-thread"}'
echo '{"type":"item.completed","item":{"type":"agent_message","text":"stub-ok"}}'
exit 0
""",
            mode=0o755,
        )

        a_paths = build_runner_paths(str(dev), "proj-a", "a1", a_name)
        b_paths = build_runner_paths(str(dev), "proj-b", "b1", b_name)

        _enable_state(a_paths.state, "proj-a", "a1")
        _disable_state(b_paths.state, "proj-b", "b1")
        (proj_a / ".memory" / ".gate-pass.a1").touch()
        a_paths.complete_lock.touch()

        a_script = make_codex_loop_script(
            dev=str(dev),
            project="proj-a",
            runner_id="a1",
            session_name=a_name,
            model="gpt-5.3-codex",
            reasoning_effort="xhigh",
            paths=a_paths,
        )
        b_script = make_codex_loop_script(
            dev=str(dev),
            project="proj-b",
            runner_id="b1",
            session_name=b_name,
            model="gpt-5.3-codex",
            reasoning_effort="xhigh",
            paths=b_paths,
        )

        env_prefix = f'export DEV="{dev}"\nexport PATH="{bin_dir}:$PATH"\nexport TMUX_CLI_HOME="{ROOT}"\n'
        a_cmd = env_prefix + a_script
        b_cmd = env_prefix + b_script

        tmux = TmuxClient(socket=str(socket), config=ROOT / "config" / "tmux" / "tmux.conf")
        tmux.create_session(a_name, a_cmd)
        tmux.create_session(b_name, b_cmd)

        _wait_for(
            "two sessions present",
            lambda: {a_name, b_name}.issubset(set(tmux.list_sessions())),
            timeout=15,
        )

        _wait_for(
            "proj-b blocked/retry log",
            lambda: b_paths.runner_log.exists()
            and "runner blocked awaiting enable; retrying in 3s" in b_paths.runner_log.read_text(),
            timeout=20,
        )
        _enable_state(b_paths.state, "proj-b", "b1")

        _wait_for(
            "proj-a done proposal",
            lambda: _latest_done_proposal(proj_a / ".memory", "a1") is not None,
            timeout=20,
        )
        proposal = _latest_done_proposal(proj_a / ".memory", "a1")
        assert proposal is not None
        payload = json.loads(proposal.read_text())
        token = payload["token"]
        approve = proj_a / ".memory" / f"RUNNER_HIL.a1.{payload['proposal_id']}.approve"
        _write(approve, token + "\n")

        _wait_for(
            "proj-a done status",
            lambda: _status(a_paths.state_file) == "done",
            timeout=20,
        )
        if a_paths.complete_lock.exists():
            raise RuntimeError("proj-a done lock not cleaned up")

        # proj-b should reject done lock when gates fail
        b_paths.complete_lock.touch()
        _wait_for(
            "proj-b done lock removed on gate failure",
            lambda: not b_paths.complete_lock.exists(),
            timeout=20,
        )
        if _status(b_paths.state_file) == "done":
            raise RuntimeError("proj-b incorrectly marked done")

        # stop proj-b
        b_paths.stop_file.touch()
        _wait_for(
            "proj-b manual_stop",
            lambda: _status(b_paths.state_file) == "manual_stop",
            timeout=20,
        )

        print("TMUX PARALLEL SMOKE PASS")
        print(f"  socket: {socket}")
        print("  runner proj-a: done lock + gate pass + HIL approve => done")
        print("  runner proj-b: blocked/retry observed, then gate fail lock reject, then manual stop")
        return 0
    finally:
        if tmux is not None:
            for sess in (a_name, b_name):
                tmux.kill_session(sess)
            subprocess.run(["tmux", "-S", str(socket), "kill-server"], capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
