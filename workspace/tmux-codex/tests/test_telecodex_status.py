import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telecodex_status import discover_telecodex_runners


def _make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                session_title TEXT,
                codex_thread_id TEXT,
                cwd TEXT,
                busy INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE runner_state (
                id INTEGER PRIMARY KEY,
                state TEXT,
                controller_chat_id INTEGER,
                controller_thread_id INTEGER,
                active_codex_thread_id TEXT,
                current_step TEXT,
                runner_scope_issue TEXT,
                updated_at TEXT
            )
            """
        )
        conn.commit()


class TelecodexStatusTests(unittest.TestCase):
    def test_discovers_busy_sessions_from_profile_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workspace" / "telecodex" / ".telecodex" / "profiles" / "main" / "data" / "telecodex.sqlite3"
            _make_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions
                    (chat_id, thread_id, session_title, codex_thread_id, cwd, busy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (100, 20, "Build plan", "thread-1", "/repo", 1, "2026-04-26T12:00:00Z"),
                )
                conn.commit()

            runners = discover_telecodex_runners(root)

        self.assertEqual(len(runners), 1)
        self.assertEqual(runners[0].profile, "main")
        self.assertEqual(runners[0].title, "Build plan")
        self.assertTrue(runners[0].busy)

    def test_discovers_active_controller_even_when_session_is_not_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workspace" / "telecodex" / ".telecodex" / "profiles" / "main" / "data" / "telecodex.sqlite3"
            _make_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions
                    (chat_id, thread_id, session_title, codex_thread_id, cwd, busy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (100, 20, "Controller", "thread-1", "/repo", 0, "2026-04-26T12:00:00Z"),
                )
                conn.execute(
                    """
                    INSERT INTO runner_state
                    (id, state, controller_chat_id, controller_thread_id, active_codex_thread_id,
                     current_step, runner_scope_issue, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("running", 100, 20, "thread-1", "phase-01", "PRO-1", "2026-04-26T12:01:00Z"),
                )
                conn.commit()

            runners = discover_telecodex_runners(root)

        self.assertEqual(len(runners), 1)
        self.assertEqual(runners[0].runner_state, "running")
        self.assertEqual(runners[0].current_step, "phase-01")
        self.assertEqual(runners[0].scope_issue, "PRO-1")


if __name__ == "__main__":
    unittest.main()
