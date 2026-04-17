from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing

from orx.config import resolve_runtime_paths
from orx.storage import CURRENT_SCHEMA_VERSION, Storage


class StorageBootstrapTests(unittest.TestCase):
    def test_bootstrap_creates_schema_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_runtime_paths(temp_dir)
            storage = Storage(paths)

            first = storage.bootstrap()
            second = storage.bootstrap()

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.schema_version, CURRENT_SCHEMA_VERSION)
            self.assertEqual(second.schema_version, CURRENT_SCHEMA_VERSION)

            with closing(sqlite3.connect(paths.db_path)) as connection:
                versions = connection.execute(
                    "SELECT version FROM schema_versions ORDER BY version"
                ).fetchall()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                pragma_version = connection.execute("PRAGMA user_version").fetchone()[0]

            self.assertEqual(
                versions,
                [(version,) for version in range(1, CURRENT_SCHEMA_VERSION + 1)],
            )
            self.assertEqual(pragma_version, CURRENT_SCHEMA_VERSION)
            self.assertIn("schema_versions", tables)
            self.assertIn("runtime_state", tables)
            self.assertIn("runners", tables)
            self.assertIn("issue_leases", tables)
            self.assertIn("command_queue", tables)
            self.assertIn("linear_issues", tables)
            self.assertIn("executor_sessions", tables)
            self.assertIn("slice_requests", tables)
            self.assertIn("slice_results", tables)
            self.assertIn("continuity_state", tables)
            self.assertIn("continuity_proposals", tables)
            self.assertIn("operator_takeovers", tables)
            self.assertIn("validation_records", tables)
            self.assertIn("intake_requests", tables)

    def test_bootstrap_upgrades_existing_v1_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_runtime_paths(temp_dir)
            paths.ensure()

            with closing(sqlite3.connect(paths.db_path)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_versions (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );

                    CREATE TABLE runtime_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    INSERT INTO schema_versions(version, applied_at)
                    VALUES (1, '2026-04-15T00:00:00+00:00');

                    INSERT INTO schema_versions(version, applied_at)
                    VALUES (2, '2026-04-15T00:01:00+00:00');

                    CREATE TABLE runners (
                        runner_id TEXT PRIMARY KEY,
                        transport TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        state TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        last_seen_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE issue_leases (
                        lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        issue_key TEXT NOT NULL,
                        runner_id TEXT NOT NULL,
                        acquired_at TEXT NOT NULL,
                        released_at TEXT
                    );

                    CREATE UNIQUE INDEX idx_issue_leases_issue_active
                    ON issue_leases(issue_key)
                    WHERE released_at IS NULL;

                    CREATE TABLE command_queue (
                        command_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        issue_key TEXT,
                        runner_id TEXT,
                        command_kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        available_at TEXT NOT NULL,
                        consumed_at TEXT,
                        priority INTEGER NOT NULL DEFAULT 100
                    );

                    CREATE TABLE linear_issues (
                        linear_id TEXT PRIMARY KEY,
                        identifier TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        team_id TEXT NOT NULL,
                        team_name TEXT NOT NULL,
                        state_id TEXT,
                        state_name TEXT NOT NULL,
                        state_type TEXT,
                        priority INTEGER,
                        project_id TEXT,
                        project_name TEXT,
                        parent_linear_id TEXT,
                        parent_identifier TEXT,
                        assignee_id TEXT,
                        assignee_name TEXT,
                        labels_json TEXT NOT NULL DEFAULT '[]',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        source_updated_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        canceled_at TEXT,
                        last_synced_at TEXT NOT NULL
                    );

                    INSERT INTO schema_versions(version, applied_at)
                    VALUES (3, '2026-04-15T00:02:00+00:00');

                    PRAGMA user_version = 3;
                    """
                )

            storage = Storage(paths)
            result = storage.bootstrap()

            self.assertFalse(result.created)
            self.assertEqual(result.schema_version, CURRENT_SCHEMA_VERSION)

            with closing(sqlite3.connect(paths.db_path)) as connection:
                versions = connection.execute(
                    "SELECT version FROM schema_versions ORDER BY version"
                ).fetchall()
                pragma_version = connection.execute("PRAGMA user_version").fetchone()[0]

            self.assertEqual(
                versions,
                [(version,) for version in range(1, CURRENT_SCHEMA_VERSION + 1)],
            )
            self.assertEqual(pragma_version, CURRENT_SCHEMA_VERSION)

    def test_bootstrap_handles_partially_applied_intake_tier_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = resolve_runtime_paths(temp_dir)
            storage = Storage(paths)
            storage.bootstrap()

            with closing(sqlite3.connect(paths.db_path)) as connection, connection:
                connection.execute("PRAGMA user_version = 13")
                connection.execute("DELETE FROM schema_versions WHERE version = 14")

            repaired = storage.bootstrap()

            self.assertEqual(repaired.schema_version, CURRENT_SCHEMA_VERSION)

            with closing(sqlite3.connect(paths.db_path)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(intake_requests)").fetchall()
                }
                versions = connection.execute(
                    "SELECT version FROM schema_versions ORDER BY version"
                ).fetchall()

            self.assertIn("planning_stage", columns)
            self.assertIn("decomposition_model", columns)
            self.assertIn("requires_hil", columns)
            self.assertEqual(
                versions,
                [(version,) for version in range(1, CURRENT_SCHEMA_VERSION + 1)],
            )


if __name__ == "__main__":
    unittest.main()
