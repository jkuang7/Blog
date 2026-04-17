"""SQLite bootstrap and schema migrations for ORX."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import RuntimePaths

CURRENT_SCHEMA_VERSION = 15


class StorageError(RuntimeError):
    """Raised when ORX storage bootstrap cannot proceed safely."""


@dataclass(frozen=True)
class BootstrapResult:
    db_path: Path
    created: bool
    schema_version: int


class Storage:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def bootstrap(self) -> BootstrapResult:
        self.paths.ensure()
        created = not self.paths.db_path.exists()

        with self.session() as connection:
            version = self._read_user_version(connection)
            if version > CURRENT_SCHEMA_VERSION:
                raise StorageError(
                    f"Database schema version {version} is newer than supported "
                    f"version {CURRENT_SCHEMA_VERSION}."
                )

            for migration in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
                self._apply_migration(connection, migration)

            final_version = self._read_user_version(connection)

        return BootstrapResult(
            db_path=self.paths.db_path,
            created=created,
            schema_version=final_version,
        )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def current_version(self) -> int:
        if not self.paths.db_path.exists():
            return 0
        with self.session() as connection:
            return self._read_user_version(connection)

    def _table_columns(self, connection: sqlite3.Connection, table_name: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def _apply_migration(self, connection: sqlite3.Connection, version: int) -> None:
        if version == 1:
            self._apply_migration_1(connection)
            return
        if version == 2:
            self._apply_migration_2(connection)
            return
        if version == 3:
            self._apply_migration_3(connection)
            return
        if version == 4:
            self._apply_migration_4(connection)
            return
        if version == 5:
            self._apply_migration_5(connection)
            return
        if version == 6:
            self._apply_migration_6(connection)
            return
        if version == 7:
            self._apply_migration_7(connection)
            return
        if version == 8:
            self._apply_migration_8(connection)
            return
        if version == 9:
            self._apply_migration_9(connection)
            return
        if version == 10:
            self._apply_migration_10(connection)
            return
        if version == 11:
            self._apply_migration_11(connection)
            return
        if version == 12:
            self._apply_migration_12(connection)
            return
        if version == 13:
            self._apply_migration_13(connection)
            return
        if version == 14:
            self._apply_migration_14(connection)
            return
        if version == 15:
            self._apply_migration_15(connection)
            return
        raise StorageError(f"No migration registered for schema version {version}.")

    def _apply_migration_1(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (1, applied_at),
        )
        connection.execute("PRAGMA user_version = 1")

    def _apply_migration_2(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runners (
                runner_id TEXT PRIMARY KEY,
                transport TEXT NOT NULL,
                display_name TEXT NOT NULL,
                state TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS issue_leases (
                lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                released_at TEXT,
                FOREIGN KEY(runner_id) REFERENCES runners(runner_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_issue_leases_issue_active
            ON issue_leases(issue_key)
            WHERE released_at IS NULL;

            CREATE INDEX IF NOT EXISTS idx_issue_leases_runner_active
            ON issue_leases(runner_id, issue_key)
            WHERE released_at IS NULL;

            CREATE TABLE IF NOT EXISTS command_queue (
                command_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_key TEXT,
                runner_id TEXT,
                command_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                consumed_at TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                FOREIGN KEY(runner_id) REFERENCES runners(runner_id)
            );

            CREATE INDEX IF NOT EXISTS idx_command_queue_pending_order
            ON command_queue(status, priority, command_id);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (2, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_4(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS executor_sessions (
                runner_id TEXT PRIMARY KEY,
                issue_key TEXT NOT NULL,
                session_name TEXT NOT NULL,
                pane_target TEXT NOT NULL,
                transport TEXT NOT NULL,
                heartbeat_at TEXT,
                last_result_at TEXT,
                state TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_executor_sessions_issue
            ON executor_sessions(issue_key, updated_at);

            CREATE TABLE IF NOT EXISTS slice_requests (
                slice_id TEXT PRIMARY KEY,
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                command_id INTEGER,
                session_name TEXT NOT NULL,
                request_json TEXT NOT NULL,
                dispatched_at TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_slice_requests_runner
            ON slice_requests(runner_id, dispatched_at);

            CREATE TABLE IF NOT EXISTS slice_results (
                result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                slice_id TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                issue_key TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                verified INTEGER NOT NULL,
                next_slice TEXT,
                artifacts_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                submitted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_slice_results_slice
            ON slice_results(slice_id, submitted_at);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (4, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_5(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS continuity_state (
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                objective TEXT NOT NULL,
                slice_goal TEXT NOT NULL,
                acceptance_json TEXT NOT NULL,
                validation_plan_json TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                discovered_gaps_json TEXT NOT NULL,
                verified_delta TEXT NOT NULL DEFAULT '',
                next_slice TEXT,
                failure_signatures_json TEXT NOT NULL,
                artifact_pointers_json TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                resume_context_json TEXT NOT NULL,
                active_slice_id TEXT,
                active_command_id INTEGER,
                last_result_status TEXT,
                last_result_summary TEXT,
                last_result_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(issue_key, runner_id),
                FOREIGN KEY(runner_id) REFERENCES runners(runner_id)
            );

            CREATE INDEX IF NOT EXISTS idx_continuity_state_active
            ON continuity_state(active_slice_id, updated_at);

            CREATE INDEX IF NOT EXISTS idx_continuity_state_runner
            ON continuity_state(runner_id, updated_at);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (5, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_6(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS continuity_proposals (
                proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_key TEXT NOT NULL UNIQUE,
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                proposal_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                rationale TEXT NOT NULL,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(runner_id) REFERENCES runners(runner_id)
            );

            CREATE INDEX IF NOT EXISTS idx_continuity_proposals_issue
            ON continuity_proposals(issue_key, status, updated_at);

            CREATE INDEX IF NOT EXISTS idx_continuity_proposals_runner
            ON continuity_proposals(runner_id, status, updated_at);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (6, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_7(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            ALTER TABLE continuity_state
            ADD COLUMN no_delta_count INTEGER NOT NULL DEFAULT 0;

            ALTER TABLE continuity_state
            ADD COLUMN consecutive_failure_count INTEGER NOT NULL DEFAULT 0;
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (7, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_8(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS operator_takeovers (
                takeover_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL,
                release_note TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                acquired_at TEXT NOT NULL,
                released_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_takeovers_active
            ON operator_takeovers(issue_key, runner_id)
            WHERE released_at IS NULL;
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (8, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_9(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS validation_records (
                validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_key TEXT NOT NULL,
                runner_id TEXT NOT NULL,
                surface TEXT NOT NULL,
                tool TEXT NOT NULL,
                result TEXT NOT NULL,
                confidence TEXT NOT NULL,
                summary TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                blockers_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(runner_id) REFERENCES runners(runner_id)
            );

            CREATE INDEX IF NOT EXISTS idx_validation_records_scope
            ON validation_records(issue_key, runner_id, validation_id DESC);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (9, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_10(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_registry (
                project_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                repo_root TEXT NOT NULL,
                runtime_home TEXT NOT NULL,
                owning_bot TEXT NOT NULL,
                owner_chat_id INTEGER,
                owner_thread_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatch_leases (
                lease_key TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatch_notifications (
                notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_key TEXT NOT NULL,
                owning_bot TEXT NOT NULL,
                ingress_bot TEXT,
                ingress_chat_id TEXT,
                ingress_thread_id TEXT,
                issue_key TEXT,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_dispatch_notifications_pending
            ON dispatch_notifications(project_key, owning_bot, notification_id)
            WHERE delivered_at IS NULL;
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (10, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_11(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS intake_requests (
                intake_id INTEGER PRIMARY KEY AUTOINCREMENT,
                intake_key TEXT NOT NULL UNIQUE,
                ingress_bot TEXT NOT NULL,
                ingress_chat_id INTEGER,
                ingress_thread_id INTEGER,
                explicit_project_key TEXT,
                default_project_key TEXT,
                request_text TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_intake_requests_status
            ON intake_requests(status, intake_id DESC);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (11, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_12(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS bot_registry (
                bot_identity TEXT PRIMARY KEY,
                telegram_chat_id INTEGER,
                telegram_thread_id INTEGER,
                default_display_name TEXT NOT NULL,
                current_display_name TEXT,
                desired_display_name TEXT NOT NULL,
                name_sync_state TEXT NOT NULL DEFAULT 'idle',
                availability_state TEXT NOT NULL DEFAULT 'available',
                assigned_project_key TEXT,
                assignment_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_heartbeat_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            ALTER TABLE dispatch_notifications
            ADD COLUMN target_bot TEXT;

            ALTER TABLE dispatch_notifications
            ADD COLUMN assignment_id TEXT;

            CREATE INDEX IF NOT EXISTS idx_dispatch_notifications_target_pending
            ON dispatch_notifications(target_bot, notification_id)
            WHERE delivered_at IS NULL;
            """
        )
        connection.execute(
            """
            UPDATE project_registry
            SET owning_bot = COALESCE(NULLIF(TRIM(owning_bot), ''), 'unassigned')
            """
        )
        connection.execute(
            """
            UPDATE dispatch_notifications
            SET target_bot = COALESCE(target_bot, owning_bot)
            WHERE target_bot IS NULL
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (12, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_13(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            ALTER TABLE bot_registry
            ADD COLUMN name_sync_retry_at TEXT;
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (13, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_14(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        existing_columns = self._table_columns(connection, "intake_requests")
        planned_columns = {
            "planning_stage": "TEXT",
            "planning_model": "TEXT",
            "planning_reasoning_effort": "TEXT",
            "decomposition_model": "TEXT",
            "decomposition_reasoning_effort": "TEXT",
            "execution_model": "TEXT",
            "execution_reasoning_effort": "TEXT",
            "confidence": "TEXT",
            "requires_hil": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in planned_columns.items():
            if column_name in existing_columns:
                continue
            connection.execute(
                f"ALTER TABLE intake_requests ADD COLUMN {column_name} {column_type}"
            )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (14, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_15(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        slice_result_columns = self._table_columns(connection, "slice_results")
        if "apply_status" not in slice_result_columns:
            connection.execute(
                "ALTER TABLE slice_results ADD COLUMN apply_status TEXT NOT NULL DEFAULT 'applied'"
            )
        if "payload_hash" not in slice_result_columns:
            connection.execute("ALTER TABLE slice_results ADD COLUMN payload_hash TEXT")
        if "stale_reason" not in slice_result_columns:
            connection.execute("ALTER TABLE slice_results ADD COLUMN stale_reason TEXT")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS follow_up_operations (
                operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                origin_issue_key TEXT NOT NULL,
                project_key TEXT,
                follow_up_title TEXT NOT NULL,
                follow_up_issue_key TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_follow_up_operations_origin
            ON follow_up_operations(origin_issue_key, updated_at);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (15, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _apply_migration_3(self, connection: sqlite3.Connection) -> None:
        applied_at = _utc_now()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS linear_issues (
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

            CREATE INDEX IF NOT EXISTS idx_linear_issues_identifier
            ON linear_issues(identifier);

            CREATE INDEX IF NOT EXISTS idx_linear_issues_state_priority
            ON linear_issues(state_name, priority, source_updated_at);
            """
        )
        connection.execute(
            """
            INSERT INTO schema_versions(version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (3, applied_at),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    @staticmethod
    def _read_user_version(connection: sqlite3.Connection) -> int:
        row = connection.execute("PRAGMA user_version").fetchone()
        if row is None:
            return 0
        return int(row[0])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
