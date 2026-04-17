"""Repository layer for runner, lease, and queue storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .commands import NormalizedCommand
from .storage import Storage


class RepositoryError(RuntimeError):
    """Base error for ORX repository failures."""


class UnknownRunnerError(RepositoryError):
    """Raised when a runner-scoped operation targets a missing runner."""


class LeaseConflictError(RepositoryError):
    """Raised when an issue already has an active lease held by another runner."""

    def __init__(self, issue_key: str, active_runner_id: str) -> None:
        super().__init__(
            f"Issue {issue_key} is already leased by runner {active_runner_id}."
        )
        self.issue_key = issue_key
        self.active_runner_id = active_runner_id


@dataclass(frozen=True)
class RunnerRecord:
    runner_id: str
    transport: str
    display_name: str
    state: str
    metadata: dict[str, Any]
    last_seen_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: int
    issue_key: str
    runner_id: str
    acquired_at: str
    released_at: str | None


@dataclass(frozen=True)
class CommandRecord:
    command_id: int
    issue_key: str | None
    runner_id: str | None
    command_kind: str
    payload: dict[str, Any]
    status: str
    created_at: str
    available_at: str
    consumed_at: str | None
    priority: int


class OrxRepository:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_runner(
        self,
        runner_id: str,
        *,
        transport: str,
        display_name: str,
        state: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunnerRecord:
        payload = json.dumps(metadata or {}, sort_keys=True)
        timestamp = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO runners(
                    runner_id,
                    transport,
                    display_name,
                    state,
                    metadata_json,
                    last_seen_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(runner_id) DO UPDATE SET
                    transport = excluded.transport,
                    display_name = excluded.display_name,
                    state = excluded.state,
                    metadata_json = excluded.metadata_json,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    runner_id,
                    transport,
                    display_name,
                    state,
                    payload,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM runners WHERE runner_id = ?",
                (runner_id,),
            ).fetchone()

        assert row is not None
        return _row_to_runner(row)

    def get_runner(self, runner_id: str) -> RunnerRecord | None:
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM runners WHERE runner_id = ?",
                (runner_id,),
            ).fetchone()
        return _row_to_runner(row) if row is not None else None

    def list_runners(self, *, runner_id: str | None = None) -> list[RunnerRecord]:
        query = "SELECT * FROM runners"
        params: tuple[Any, ...] = ()
        if runner_id is not None:
            query += " WHERE runner_id = ?"
            params = (runner_id,)
        query += " ORDER BY runner_id ASC"
        with self.storage.session() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_runner(row) for row in rows]

    def acquire_issue_lease(self, issue_key: str, runner_id: str) -> LeaseRecord:
        if self.get_runner(runner_id) is None:
            raise UnknownRunnerError(f"Runner {runner_id} is not registered.")

        with self.storage.session() as connection:
            active = connection.execute(
                """
                SELECT * FROM issue_leases
                WHERE issue_key = ? AND released_at IS NULL
                """,
                (issue_key,),
            ).fetchone()
            if active is not None:
                lease = _row_to_lease(active)
                if lease.runner_id != runner_id:
                    raise LeaseConflictError(issue_key, lease.runner_id)
                return lease

            timestamp = _utc_now()
            connection.execute(
                """
                INSERT INTO issue_leases(issue_key, runner_id, acquired_at, released_at)
                VALUES (?, ?, ?, NULL)
                """,
                (issue_key, runner_id, timestamp),
            )
            row = connection.execute(
                """
                SELECT * FROM issue_leases
                WHERE issue_key = ? AND released_at IS NULL
                """,
                (issue_key,),
            ).fetchone()

        assert row is not None
        return _row_to_lease(row)

    def release_issue_lease(self, issue_key: str, runner_id: str) -> LeaseRecord | None:
        with self.storage.session() as connection:
            connection.execute(
                """
                UPDATE issue_leases
                SET released_at = ?
                WHERE issue_key = ?
                  AND runner_id = ?
                  AND released_at IS NULL
                """,
                (_utc_now(), issue_key, runner_id),
            )
            row = connection.execute(
                """
                SELECT * FROM issue_leases
                WHERE issue_key = ? AND runner_id = ?
                ORDER BY lease_id DESC
                LIMIT 1
                """,
                (issue_key, runner_id),
            ).fetchone()

        return _row_to_lease(row) if row is not None else None

    def list_active_leases(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
    ) -> list[LeaseRecord]:
        clauses = ["released_at IS NULL"]
        params: list[Any] = []
        if issue_key is not None:
            clauses.append("issue_key = ?")
            params.append(issue_key)
        if runner_id is not None:
            clauses.append("runner_id = ?")
            params.append(runner_id)
        with self.storage.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM issue_leases
                WHERE {' AND '.join(clauses)}
                ORDER BY lease_id ASC
                """,
                tuple(params),
            ).fetchall()
        return [_row_to_lease(row) for row in rows]

    def enqueue_command(
        self,
        command_kind: str,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = 100,
    ) -> CommandRecord:
        if runner_id is not None and self.get_runner(runner_id) is None:
            raise UnknownRunnerError(f"Runner {runner_id} is not registered.")

        timestamp = _utc_now()
        encoded_payload = json.dumps(payload or {}, sort_keys=True)
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO command_queue(
                    issue_key,
                    runner_id,
                    command_kind,
                    payload_json,
                    status,
                    created_at,
                    available_at,
                    consumed_at,
                    priority
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?, NULL, ?)
                """,
                (
                    issue_key,
                    runner_id,
                    command_kind,
                    encoded_payload,
                    timestamp,
                    timestamp,
                    priority,
                ),
            )
            row = connection.execute(
                "SELECT * FROM command_queue ORDER BY command_id DESC LIMIT 1"
            ).fetchone()

        assert row is not None
        return _row_to_command(row)

    def enqueue_normalized_command(self, command: NormalizedCommand) -> CommandRecord:
        self._ensure_runner_exists(command.runner_id)
        if command.replacement_key is not None:
            self.supersede_pending_commands(
                issue_key=command.issue_key,
                runner_id=command.runner_id,
                replacement_key=command.replacement_key,
            )
        return self.enqueue_command(
            command.command_kind,
            issue_key=command.issue_key,
            runner_id=command.runner_id,
            payload=command.payload,
            priority=command.priority,
        )

    def supersede_pending_commands(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
        replacement_key: str,
    ) -> list[CommandRecord]:
        if issue_key is None and runner_id is None:
            raise RepositoryError(
                "supersede_pending_commands requires issue_key or runner_id scope."
            )

        with self.storage.session() as connection:
            clauses = ["status = 'pending'"]
            params: list[Any] = []
            if issue_key is not None:
                clauses.append("issue_key = ?")
                params.append(issue_key)
            if runner_id is not None:
                clauses.append("runner_id = ?")
                params.append(runner_id)

            rows = connection.execute(
                f"""
                SELECT * FROM command_queue
                WHERE {' AND '.join(clauses)}
                ORDER BY priority ASC, command_id ASC
                """,
                tuple(params),
            ).fetchall()

            matched_ids: list[int] = []
            replaced_rows: list[Any] = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                if payload.get("replacement_key") == replacement_key:
                    matched_ids.append(int(row["command_id"]))
                    replaced_rows.append(row)

            if matched_ids:
                placeholders = ", ".join("?" for _ in matched_ids)
                connection.execute(
                    f"""
                    UPDATE command_queue
                    SET status = 'superseded',
                        consumed_at = ?
                    WHERE command_id IN ({placeholders})
                    """,
                    (_utc_now(), *matched_ids),
                )

        return [_row_to_command(row) for row in replaced_rows]

    def list_commands(
        self,
        *,
        status: str = "pending",
        issue_key: str | None = None,
        runner_id: str | None = None,
    ) -> list[CommandRecord]:
        clauses = ["status = ?"]
        params: list[Any] = [status]
        if issue_key is not None:
            clauses.append("issue_key = ?")
            params.append(issue_key)
        if runner_id is not None:
            clauses.append("runner_id = ?")
            params.append(runner_id)
        with self.storage.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM command_queue
                WHERE {' AND '.join(clauses)}
                ORDER BY priority ASC, command_id ASC
                """,
                tuple(params),
            ).fetchall()
        return [_row_to_command(row) for row in rows]

    def _ensure_runner_exists(self, runner_id: str | None) -> None:
        if runner_id is not None and self.get_runner(runner_id) is None:
            raise UnknownRunnerError(f"Runner {runner_id} is not registered.")


def _row_to_runner(row: Any) -> RunnerRecord:
    return RunnerRecord(
        runner_id=row["runner_id"],
        transport=row["transport"],
        display_name=row["display_name"],
        state=row["state"],
        metadata=json.loads(row["metadata_json"]),
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_lease(row: Any) -> LeaseRecord:
    return LeaseRecord(
        lease_id=int(row["lease_id"]),
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        acquired_at=row["acquired_at"],
        released_at=row["released_at"],
    )


def _row_to_command(row: Any) -> CommandRecord:
    return CommandRecord(
        command_id=int(row["command_id"]),
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        command_kind=row["command_kind"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        created_at=row["created_at"],
        available_at=row["available_at"],
        consumed_at=row["consumed_at"],
        priority=int(row["priority"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
