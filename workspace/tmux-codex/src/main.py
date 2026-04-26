"""Command dispatch for cl/clls session manager."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .menu import SessionMenu
from .tmux import TmuxClient


def _repo_home() -> Path:
    override = os.environ.get("TMUX_CLI_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def get_tmux_config() -> Path | None:
    config = _repo_home() / "config" / "tmux" / "tmux.conf"
    return config if config.exists() else None


def detect_session_type(args: list[str]) -> tuple[str, str]:
    if not args:
        return "codex", "session"
    joined = " ".join(arg.lower() for arg in args)
    if "/integrate" in joined:
        return "int", "integrator"
    if "/spec" in joined:
        return "add", "add session"
    return "codex", "session"


def session_profile_overrides(args: list[str]) -> list[str]:
    if not args:
        return []

    joined = " ".join(arg.lower() for arg in args)
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
    config = get_tmux_config()
    tmux = TmuxClient(config=config)
    prefix, display_name = detect_session_type(args)
    sess_name = tmux.next_session_name(prefix=prefix)
    workdir = os.getcwd()
    codex_parts = [
        "codex",
        "--search",
        "--dangerously-bypass-approvals-and-sandbox",
        *session_profile_overrides(args),
        *args,
    ]
    cmd = f'cd "{workdir}" && {shlex.join(codex_parts)}; clear; exec zsh -l'

    print(f"Creating {display_name}...", end="", flush=True)
    try:
        tmux.create_session(sess_name, cmd)
    except RuntimeError as exc:
        print(" failed")
        print(f"Error: {exc}")
        return
    print(" ready")
    tmux.attach(sess_name)


def _resolve_list_selector(tmux: TmuxClient, selector: str) -> str | None:
    sessions = tmux.list_sessions(prefix="codex")
    normalized = selector.strip()
    if normalized.isdigit() and normalized != "0":
        index = int(normalized) - 1
        if 0 <= index < len(sessions):
            return sessions[index]
    return None


def list_sessions(selector: str | None = None) -> None:
    dev = os.environ.get("DEV", "/Users/jian/Dev")
    os.chdir(dev)
    tmux = TmuxClient(config=get_tmux_config())

    if selector is not None:
        session_name = _resolve_list_selector(tmux, selector)
        if session_name is None:
            print(f"Session selector not found: {selector}")
            return
        tmux.attach(session_name)
        return

    result = SessionMenu(tmux).run()
    if result and result[0] == "attach" and result[1]:
        tmux.attach(result[1])


def _print_help() -> None:
    print("Usage:")
    print("  cl                    # interactive session menu")
    print("  cl ls [selector]      # list sessions or attach directly, e.g. cl ls 1")
    print("  cl <prompt...>        # create a new Codex session with prompt args")
    print()
    print("Telecodex Telegram work is shown in cl/clls as read-only runner status.")
    print("The old tmux-codex background runner commands have been removed.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        list_sessions()
        return

    if args[0] == "ls":
        if len(args) > 2:
            raise SystemExit("Usage: cl ls [selector]")
        list_sessions(args[1] if len(args) == 2 else None)
        return

    if args[0] in ("-h", "--help", "help"):
        _print_help()
        return

    removed = {"loop", "loop-bg", "runner", "run", "r", "stop", "k", "ka", "kb", "k*"}
    if args[0] in removed:
        raise SystemExit(
            "tmux-codex background runner commands were removed. "
            "Use Telegram/Telecodex for /add, /run, and /review; use cl ls to view status."
        )

    create_session(args)


if __name__ == "__main__":
    main()
