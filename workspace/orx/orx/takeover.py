"""Protected manual takeover journal for local operator control."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .commands import normalize_command
from .repository import CommandRecord, OrxRepository
from .storage import Storage


class TakeoverConflictError(RuntimeError):
    """Raised when takeover ownership conflicts with the active operator."""


@dataclass(frozen=True)
class TakeoverRecord:
    takeover_id: int
    issue_key: str
    runner_id: str
    operator_id: str
    rationale: str
    status: str
    release_note: str | None
    metadata: dict[str, Any]
    acquired_at: str
    released_at: str | None


class TakeoverService:
    def __init__(self, storage: Storage, repository: OrxRepository) -> None:
        self.storage = storage
        self.repository = repository

    def begin(
        self,
        *,
        issue_key: str,
        runner_id: str,
        operator_id: str,
        rationale: str,
        metadata: dict[str, Any] | None = None,
    ) -> TakeoverRecord:
        active = self.get_active(issue_key=issue_key, runner_id=runner_id)
        if active is not None:
            if active.operator_id != operator_id:
                raise TakeoverConflictError(
                    f"Runner {runner_id} on {issue_key} is already controlled by {active.operator_id}."
                )
            return active

        acquired_at = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO operator_takeovers(
                    issue_key,
                    runner_id,
                    operator_id,
                    rationale,
                    status,
                    release_note,
                    metadata_json,
                    acquired_at,
                    released_at
                )
                VALUES (?, ?, ?, ?, 'active', NULL, ?, ?, NULL)
                """,
                (
                    issue_key,
                    runner_id,
                    operator_id,
                    rationale,
                    json.dumps(metadata or {}, sort_keys=True),
                    acquired_at,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM operator_takeovers
                WHERE issue_key = ? AND runner_id = ? AND released_at IS NULL
                """,
                (issue_key, runner_id),
            ).fetchone()
        assert row is not None
        return _row_to_takeover(row)

    def return_control(
        self,
        *,
        issue_key: str,
        runner_id: str,
        operator_id: str,
        note: str | None = None,
    ) -> TakeoverRecord:
        active = self.get_active(issue_key=issue_key, runner_id=runner_id)
        if active is None:
            raise ValueError(f"No active takeover for {issue_key}/{runner_id}.")
        if active.operator_id != operator_id:
            raise TakeoverConflictError(
                f"Active takeover for {issue_key}/{runner_id} belongs to {active.operator_id}."
            )
        released_at = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                UPDATE operator_takeovers
                SET status = 'released',
                    release_note = ?,
                    released_at = ?
                WHERE takeover_id = ?
                """,
                (note, released_at, active.takeover_id),
            )
            row = connection.execute(
                "SELECT * FROM operator_takeovers WHERE takeover_id = ?",
                (active.takeover_id,),
            ).fetchone()
        assert row is not None
        return _row_to_takeover(row)

    def get_active(self, *, issue_key: str, runner_id: str) -> TakeoverRecord | None:
        with self.storage.session() as connection:
            row = connection.execute(
                """
                SELECT * FROM operator_takeovers
                WHERE issue_key = ? AND runner_id = ? AND released_at IS NULL
                ORDER BY takeover_id DESC
                LIMIT 1
                """,
                (issue_key, runner_id),
            ).fetchone()
        return _row_to_takeover(row) if row is not None else None

    def list_active(self) -> list[TakeoverRecord]:
        with self.storage.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM operator_takeovers
                WHERE released_at IS NULL
                ORDER BY takeover_id ASC
                """
            ).fetchall()
        return [_row_to_takeover(row) for row in rows]

    def queue_control_command(
        self,
        *,
        operator_id: str,
        command_kind: str,
        issue_key: str,
        runner_id: str,
        payload: dict[str, Any] | None = None,
    ) -> CommandRecord:
        active = self.get_active(issue_key=issue_key, runner_id=runner_id)
        if active is None:
            raise TakeoverConflictError(
                f"Operator takeover is required before mutating {issue_key}/{runner_id}."
            )
        if active.operator_id != operator_id:
            raise TakeoverConflictError(
                f"Active takeover for {issue_key}/{runner_id} belongs to {active.operator_id}."
            )

        body = dict(payload or {})
        body["takeover"] = {"operator_id": operator_id, "takeover_id": active.takeover_id}
        normalized = normalize_command(
            command_kind,
            issue_key=issue_key,
            runner_id=runner_id,
            payload=body,
        )
        return self.repository.enqueue_normalized_command(normalized)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_takeover(row: Any) -> TakeoverRecord:
    return TakeoverRecord(
        takeover_id=int(row["takeover_id"]),
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        operator_id=row["operator_id"],
        rationale=row["rationale"],
        status=row["status"],
        release_note=row["release_note"],
        metadata=json.loads(row["metadata_json"]),
        acquired_at=row["acquired_at"],
        released_at=row["released_at"],
    )
