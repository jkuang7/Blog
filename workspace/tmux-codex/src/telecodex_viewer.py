"""Terminal viewer for Telecodex runner events."""

from __future__ import annotations

import argparse
import os
import select
import sqlite3
import sys
import termios
import time
import tty
from contextlib import closing
from pathlib import Path
from typing import Iterable


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_events(
    conn: sqlite3.Connection,
    chat_id: int,
    thread_id: int,
    after_id: int,
    limit: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, event_kind, text, created_at
        FROM runner_events
        WHERE chat_id = ?1 AND thread_id = ?2 AND id > ?3
        ORDER BY id ASC
        LIMIT ?4
        """,
        (chat_id, thread_id, after_id, limit),
    ).fetchall()


def _render_event(row: sqlite3.Row) -> str:
    created_at = str(row["created_at"] or "")
    timestamp = created_at[11:19] if len(created_at) >= 19 else created_at
    kind = str(row["event_kind"] or "event")
    text = str(row["text"] or "").rstrip()
    if "\n" in text:
        return f"[{timestamp}] {kind}\n{text}\n"
    return f"[{timestamp}] {kind}: {text}"


def _compact_events(rows: Iterable[sqlite3.Row]) -> list[sqlite3.Row]:
    compacted: list[sqlite3.Row] = []
    for row in rows:
        kind = str(row["event_kind"] or "")
        if kind == "assistant" and compacted:
            previous = compacted[-1]
            previous_kind = str(previous["event_kind"] or "")
            if previous_kind == "assistant":
                compacted[-1] = row
                continue
        compacted.append(row)
    return compacted


def _print_header(title: str, db_path: Path, chat_id: int, thread_id: int) -> None:
    print("Telecodex Runner Viewer")
    print(f"{title}")
    print(f"chat={chat_id} thread={thread_id} db={db_path}")
    print("q=quit")
    print()


def _run(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    _print_header(args.title, db_path, args.chat_id, args.thread_id)
    last_id = 0
    try:
        with closing(_connect_ro(db_path)) as conn:
            recent = _fetch_events(conn, args.chat_id, args.thread_id, 0, args.history)
            if recent:
                last_id = int(recent[-1]["id"])
                for row in _compact_events(recent):
                    print(_render_event(row), flush=True)
            else:
                print("(no runner events yet)", flush=True)
    except sqlite3.Error as error:
        print(f"Unable to read Telecodex events: {error}", flush=True)

    fd = sys.stdin.fileno()
    old_term = termios.tcgetattr(fd) if os.isatty(fd) else None
    try:
        if old_term is not None:
            tty.setcbreak(fd)
        while True:
            if old_term is not None and select.select([sys.stdin], [], [], 0)[0]:
                char = sys.stdin.read(1)
                if char == "q":
                    return 0
            try:
                with closing(_connect_ro(db_path)) as conn:
                    rows = _fetch_events(conn, args.chat_id, args.thread_id, last_id, 100)
            except sqlite3.Error:
                rows = []
            for row in _compact_events(rows):
                last_id = int(row["id"])
                print(_render_event(row), flush=True)
            time.sleep(args.interval)
    finally:
        if old_term is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--thread-id", required=True, type=int)
    parser.add_argument("--title", default="Telecodex runner")
    parser.add_argument("--history", type=int, default=80)
    parser.add_argument("--interval", type=float, default=1.0)
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
