from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing

from orx.config import resolve_runtime_paths
from orx.mirror import LinearMirrorRepository
from orx.storage import CURRENT_SCHEMA_VERSION, Storage


class LinearMirrorRepositoryTests(unittest.TestCase):
    def test_upsert_and_list_mirrored_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)

            repository.upsert_issue(
                linear_id="issue-linear-1",
                identifier="PRO-17",
                title="Mirror foundation",
                description="Add canonical mirror storage.",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                priority=2,
                labels=["orx", "mirror"],
                metadata={"source": "linear"},
                source_updated_at="2026-04-15T19:24:00+00:00",
                created_at="2026-04-15T18:00:00+00:00",
            )
            updated = repository.upsert_issue(
                linear_id="issue-linear-1",
                identifier="PRO-17",
                title="Mirror foundation updated",
                description="Add canonical mirror storage and queries.",
                team_id="team-1",
                team_name="Projects",
                state_name="Done",
                state_type="completed",
                priority=2,
                labels=["mirror"],
                metadata={"source": "linear", "phase": "3A"},
                source_updated_at="2026-04-15T19:25:00+00:00",
                created_at="2026-04-15T18:00:00+00:00",
                completed_at="2026-04-15T19:26:00+00:00",
            )
            repository.upsert_issue(
                linear_id="issue-linear-2",
                identifier="PRO-18",
                title="Metadata parser",
                description="Parse metadata block.",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                priority=3,
                source_updated_at="2026-04-15T19:20:00+00:00",
                created_at="2026-04-15T18:30:00+00:00",
            )

            fetched = repository.get_issue(identifier="PRO-17")
            listed = repository.list_issues()

            assert fetched is not None
            self.assertEqual(updated.title, "Mirror foundation updated")
            self.assertEqual(fetched.state_name, "Done")
            self.assertEqual(fetched.metadata["phase"], "3A")
            self.assertEqual([issue.identifier for issue in listed], ["PRO-18", "PRO-17"])

    def test_hierarchy_queries_resolve_children_and_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            repository = LinearMirrorRepository(storage)

            umbrella = repository.upsert_issue(
                linear_id="umbrella",
                identifier="PRO-5",
                title="Umbrella",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                source_updated_at="2026-04-15T20:00:00+00:00",
            )
            phase = repository.upsert_issue(
                linear_id="phase",
                identifier="PRO-8",
                title="Phase",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="In Progress",
                state_type="started",
                parent_linear_id=umbrella.linear_id,
                parent_identifier=umbrella.identifier,
                source_updated_at="2026-04-15T20:01:00+00:00",
            )
            leaf = repository.upsert_issue(
                linear_id="leaf",
                identifier="PRO-31",
                title="Leaf",
                description="",
                team_id="team-1",
                team_name="Projects",
                state_name="Todo",
                state_type="unstarted",
                parent_linear_id=phase.linear_id,
                parent_identifier=phase.identifier,
                source_updated_at="2026-04-15T20:02:00+00:00",
            )

            self.assertTrue(repository.has_children(umbrella))
            self.assertTrue(repository.has_children(phase))
            self.assertFalse(repository.has_children(leaf))
            self.assertEqual(
                [issue.identifier for issue in repository.list_child_issues(phase)],
                ["PRO-31"],
            )
            self.assertEqual(
                [issue.identifier for issue in repository.get_ancestor_chain(leaf)],
                ["PRO-8", "PRO-5"],
            )

    def test_storage_upgrades_to_v3_with_linear_issue_table(self) -> None:
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

                    INSERT INTO schema_versions(version, applied_at) VALUES (1, '2026-04-15T00:00:00+00:00');
                    INSERT INTO schema_versions(version, applied_at) VALUES (2, '2026-04-15T00:01:00+00:00');
                    PRAGMA user_version = 2;
                    """
                )

            storage = Storage(paths)
            result = storage.bootstrap()

            self.assertEqual(result.schema_version, CURRENT_SCHEMA_VERSION)
            with closing(sqlite3.connect(paths.db_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                versions = connection.execute(
                    "SELECT version FROM schema_versions ORDER BY version"
                ).fetchall()

            self.assertIn("linear_issues", tables)
            self.assertEqual(
                versions,
                [(version,) for version in range(1, CURRENT_SCHEMA_VERSION + 1)],
            )


if __name__ == "__main__":
    unittest.main()
