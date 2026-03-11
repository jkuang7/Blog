#!/usr/bin/env python3
"""Manual smoke for lock + gates enforcement with two concurrent runner IDs."""

from __future__ import annotations

import argparse
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


def _wait_for(label: str, predicate, timeout: float = 20.0, poll: float = 0.1) -> None:
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


def _load_status(state_file: Path) -> str:
    if not state_file.exists():
        return ""
    return json.loads(state_file.read_text()).get("status", "")


def _enable_state(paths, project: str, runner_id: str) -> None:
    state = default_runner_state(project=project, runner_id=runner_id)
    state["enabled"] = True
    state["status"] = "ready"
    write_json(paths.state_file, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="runner-smoke", help="Temporary project name")
    parser.add_argument("--timeout", type=float, default=25.0, help="Seconds to wait for each phase")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temp sandbox for inspection")
    args = parser.parse_args()

    tmp_root = Path(tempfile.mkdtemp(prefix="tmux-codex-smoke-"))
    dev_root = tmp_root / "Dev"
    project_root = dev_root / "Repos" / args.project
    memory_dir = project_root / ".memory"
    bin_dir = tmp_root / "bin"

    alpha_proc = None
    beta_proc = None
    alpha_out = None
    beta_out = None
    exit_code = 1

    try:
        memory_dir.mkdir(parents=True, exist_ok=True)

        _write(
            memory_dir / "gates.sh",
            """#!/usr/bin/env bash
run_gates() {
    set -euo pipefail
    if [[ -f ".memory/.gate-pass.${RUNNER_ID}" ]]; then
        return 0
    fi
    echo "run_gates: forced fail for ${RUNNER_ID}"
    return 1
}
""",
            mode=0o755,
        )

        _write(
            bin_dir / "codex",
            """#!/usr/bin/env bash
set -euo pipefail
printf '{"type":"thread.started","thread_id":"smoke-thread"}\n'
printf '{"type":"item.completed","item":{"type":"message","text":"smoke iteration"}}\n'
exit 0
""",
            mode=0o755,
        )

        alpha_paths = build_runner_paths(
            dev=str(dev_root),
            project=args.project,
            runner_id="alpha",
            session_name=f"runner-{args.project}-alpha",
        )
        beta_paths = build_runner_paths(
            dev=str(dev_root),
            project=args.project,
            runner_id="beta",
            session_name=f"runner-{args.project}-beta",
        )

        _enable_state(alpha_paths.state, args.project, "alpha")
        _enable_state(beta_paths.state, args.project, "beta")

        alpha_script = make_codex_loop_script(
            dev=str(dev_root),
            project=args.project,
            runner_id="alpha",
            session_name=f"runner-{args.project}-alpha",
            model="gpt-5.3-codex",
            reasoning_effort="xhigh",
            paths=alpha_paths,
        ).replace("exec zsh -l", "exit 0")
        beta_script = make_codex_loop_script(
            dev=str(dev_root),
            project=args.project,
            runner_id="beta",
            session_name=f"runner-{args.project}-beta",
            model="gpt-5.3-codex",
            reasoning_effort="xhigh",
            paths=beta_paths,
        ).replace("exec zsh -l", "exit 0")

        alpha_script_path = tmp_root / "runner-alpha.sh"
        beta_script_path = tmp_root / "runner-beta.sh"
        _write(alpha_script_path, alpha_script, mode=0o755)
        _write(beta_script_path, beta_script, mode=0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["DEV"] = str(dev_root)
        env["TMUX_CLI_HOME"] = str(ROOT)

        alpha_out = (tmp_root / "runner-alpha.log").open("w")
        beta_out = (tmp_root / "runner-beta.log").open("w")

        # Prepare done candidates.
        (memory_dir / ".gate-pass.alpha").touch()
        alpha_paths.complete_lock.touch()
        beta_paths.complete_lock.touch()

        alpha_proc = subprocess.Popen(["/bin/zsh", str(alpha_script_path)], env=env, stdout=alpha_out, stderr=subprocess.STDOUT)
        beta_proc = subprocess.Popen(["/bin/zsh", str(beta_script_path)], env=env, stdout=beta_out, stderr=subprocess.STDOUT)

        done_proposal = memory_dir / "RUNNER_PROPOSAL.alpha.done-0000.json"
        _wait_for("alpha done proposal", done_proposal.exists, timeout=args.timeout)
        done_payload = json.loads(done_proposal.read_text())
        token = done_payload["token"]
        _write(memory_dir / "RUNNER_HIL.alpha.done-0000.approve", token + "\n")

        _wait_for("runner alpha exit", lambda: alpha_proc.poll() is not None, timeout=args.timeout)
        if alpha_proc.returncode != 0:
            raise RuntimeError(f"runner alpha exited non-zero: {alpha_proc.returncode}")
        if alpha_paths.complete_lock.exists():
            raise RuntimeError("runner alpha done lock was not cleaned up")
        if _load_status(alpha_paths.state_file) != "done":
            raise RuntimeError(f"runner alpha final status was not done: {_load_status(alpha_paths.state_file)!r}")

        _wait_for("runner beta done lock removed", lambda: not beta_paths.complete_lock.exists(), timeout=args.timeout)
        if beta_proc.poll() is not None:
            raise RuntimeError("runner beta exited unexpectedly after failing gates")

        beta_paths.stop_file.touch()
        _wait_for("runner beta stop exit", lambda: beta_proc.poll() is not None, timeout=args.timeout)
        if beta_proc.returncode != 0:
            raise RuntimeError(f"runner beta exited non-zero: {beta_proc.returncode}")
        if _load_status(beta_paths.state_file) != "manual_stop":
            raise RuntimeError(f"runner beta final status was not manual_stop: {_load_status(beta_paths.state_file)!r}")

        print("SMOKE PASS")
        print(f"  sandbox: {tmp_root}")
        print("  runner alpha: done lock + gates pass + HIL approve => done")
        print("  runner beta: done lock + gates fail => lock removed, continues until stop")
        exit_code = 0
        return 0
    finally:
        if alpha_out:
            alpha_out.close()
        if beta_out:
            beta_out.close()

        for proc in (alpha_proc, beta_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

        if not args.keep_temp and exit_code == 0:
            shutil.rmtree(tmp_root, ignore_errors=True)
        elif args.keep_temp or exit_code != 0:
            print(f"Kept sandbox for inspection: {tmp_root}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
