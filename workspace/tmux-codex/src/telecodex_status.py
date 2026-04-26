"""Read Telecodex session state for the tmux-codex runner view."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import sqlite3


ACTIVE_RUNNER_STATES = {"running", "reviewing", "stopping"}


@dataclass(frozen=True)
class TelecodexRunner:
    profile: str
    db_path: Path
    chat_id: int
    thread_id: int
    title: str
    cwd: str
    codex_thread_id: str | None
    busy: bool
    updated_at: str
    runner_state: str | None = None
    current_step: str | None = None
    scope_issue: str | None = None

    @property
    def status_icon(self) -> str:
        if self.busy or self.runner_state in ACTIVE_RUNNER_STATES:
            return "🔄"
        if self.runner_state == "paused":
            return "⏸"
        if self.runner_state == "blocked":
            return "⚠"
        return "⏸"

    @property
    def display_title(self) -> str:
        parts = [self.profile]
        if self.title:
            parts.append(self.title)
        else:
            parts.append(f"{self.chat_id}/{self.thread_id}")
        if self.current_step:
            parts.append(self.current_step)
        if self.scope_issue:
            parts.append(self.scope_issue)
        return " | ".join(parts)


def discover_telecodex_runners(dev_root: Path | str = "/Users/jian/Dev") -> list[TelecodexRunner]:
    """Return busy or automation-active Telecodex sessions from local profiles."""
    root = Path(dev_root).expanduser()
    db_paths = _candidate_db_paths(root)
    runners: list[TelecodexRunner] = []
    seen: set[tuple[str, int, int]] = set()
    for db_path in db_paths:
        for runner in _read_profile_runners(db_path):
            key = (runner.profile, runner.chat_id, runner.thread_id)
            if key not in seen:
                runners.append(runner)
                seen.add(key)
    return sorted(runners, key=lambda item: item.updated_at, reverse=True)


def _candidate_db_paths(root: Path) -> list[Path]:
    telecodex_root = root / "workspace" / "telecodex" / ".telecodex"
    paths = list(telecodex_root.glob("profiles/*/data/telecodex.sqlite3"))
    paths.extend(telecodex_root.glob("*.sqlite3"))
    return [path for path in paths if path.exists()]


def _read_profile_runners(db_path: Path) -> list[TelecodexRunner]:
    profile = _profile_name(db_path)
    try:
        uri = f"file:{db_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as conn:
            conn.row_factory = sqlite3.Row
            runner_state = _read_runner_state(conn)
            rows = conn.execute(
                """
                SELECT chat_id, thread_id, session_title, codex_thread_id, cwd, busy, updated_at
                FROM sessions
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
    except sqlite3.Error:
        return []

    active_key: tuple[int, int] | None = None
    if runner_state and str(runner_state.get("state") or "") in ACTIVE_RUNNER_STATES:
        chat_id = runner_state.get("controller_chat_id")
        thread_id = runner_state.get("controller_thread_id")
        if chat_id is not None:
            active_key = (int(chat_id), int(thread_id or 0))

    runners: list[TelecodexRunner] = []
    found_active = False
    for row in rows:
        key = (int(row["chat_id"]), int(row["thread_id"]))
        is_busy = bool(row["busy"])
        is_active_controller = active_key == key
        if not is_busy and not is_active_controller:
            continue
        if is_active_controller:
            found_active = True
        runners.append(
            TelecodexRunner(
                profile=profile,
                db_path=db_path,
                chat_id=key[0],
                thread_id=key[1],
                title=str(row["session_title"] or ""),
                cwd=str(row["cwd"] or ""),
                codex_thread_id=row["codex_thread_id"],
                busy=is_busy,
                updated_at=str(row["updated_at"] or ""),
                runner_state=str(runner_state.get("state") or "") if is_active_controller and runner_state else None,
                current_step=str(runner_state.get("current_step") or "") if is_active_controller and runner_state else None,
                scope_issue=str(runner_state.get("runner_scope_issue") or "") if is_active_controller and runner_state else None,
            )
        )

    if active_key and runner_state and not found_active:
        runners.append(
            TelecodexRunner(
                profile=profile,
                db_path=db_path,
                chat_id=active_key[0],
                thread_id=active_key[1],
                title="Telecodex automation",
                cwd="",
                codex_thread_id=runner_state.get("active_codex_thread_id"),
                busy=True,
                updated_at=str(runner_state.get("updated_at") or ""),
                runner_state=str(runner_state.get("state") or ""),
                current_step=str(runner_state.get("current_step") or ""),
                scope_issue=str(runner_state.get("runner_scope_issue") or ""),
            )
        )
    return runners


def _read_runner_state(conn: sqlite3.Connection) -> dict[str, object] | None:
    try:
        row = conn.execute(
            """
            SELECT state, controller_chat_id, controller_thread_id, active_codex_thread_id,
                   current_step, runner_scope_issue, updated_at
            FROM runner_state
            WHERE id = 1
            """
        ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def _profile_name(db_path: Path) -> str:
    parts = db_path.parts
    if "profiles" in parts:
        idx = parts.index("profiles")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "default"
