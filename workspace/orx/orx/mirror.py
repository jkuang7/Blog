"""Canonical Linear issue mirror storage access."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class MirroredIssueRecord:
    linear_id: str
    identifier: str
    title: str
    description: str
    team_id: str
    team_name: str
    state_id: str | None
    state_name: str
    state_type: str | None
    priority: int | None
    project_id: str | None
    project_name: str | None
    parent_linear_id: str | None
    parent_identifier: str | None
    assignee_id: str | None
    assignee_name: str | None
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    source_updated_at: str
    created_at: str
    completed_at: str | None
    canceled_at: str | None
    last_synced_at: str


class LinearMirrorRepository:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_issue(
        self,
        *,
        linear_id: str,
        identifier: str,
        title: str,
        description: str,
        team_id: str,
        team_name: str,
        state_name: str,
        source_updated_at: str,
        state_id: str | None = None,
        state_type: str | None = None,
        priority: int | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        parent_linear_id: str | None = None,
        parent_identifier: str | None = None,
        assignee_id: str | None = None,
        assignee_name: str | None = None,
        labels: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
        completed_at: str | None = None,
        canceled_at: str | None = None,
    ) -> MirroredIssueRecord:
        labels_json = json.dumps(sorted(labels or []))
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        synced_at = _utc_now()
        created_value = created_at or synced_at

        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO linear_issues(
                    linear_id,
                    identifier,
                    title,
                    description,
                    team_id,
                    team_name,
                    state_id,
                    state_name,
                    state_type,
                    priority,
                    project_id,
                    project_name,
                    parent_linear_id,
                    parent_identifier,
                    assignee_id,
                    assignee_name,
                    labels_json,
                    metadata_json,
                    source_updated_at,
                    created_at,
                    completed_at,
                    canceled_at,
                    last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(linear_id) DO UPDATE SET
                    identifier = excluded.identifier,
                    title = excluded.title,
                    description = excluded.description,
                    team_id = excluded.team_id,
                    team_name = excluded.team_name,
                    state_id = excluded.state_id,
                    state_name = excluded.state_name,
                    state_type = excluded.state_type,
                    priority = excluded.priority,
                    project_id = excluded.project_id,
                    project_name = excluded.project_name,
                    parent_linear_id = excluded.parent_linear_id,
                    parent_identifier = excluded.parent_identifier,
                    assignee_id = excluded.assignee_id,
                    assignee_name = excluded.assignee_name,
                    labels_json = excluded.labels_json,
                    metadata_json = excluded.metadata_json,
                    source_updated_at = excluded.source_updated_at,
                    completed_at = excluded.completed_at,
                    canceled_at = excluded.canceled_at,
                    last_synced_at = excluded.last_synced_at
                """,
                (
                    linear_id,
                    identifier,
                    title,
                    description,
                    team_id,
                    team_name,
                    state_id,
                    state_name,
                    state_type,
                    priority,
                    project_id,
                    project_name,
                    parent_linear_id,
                    parent_identifier,
                    assignee_id,
                    assignee_name,
                    labels_json,
                    metadata_json,
                    source_updated_at,
                    created_value,
                    completed_at,
                    canceled_at,
                    synced_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM linear_issues WHERE linear_id = ?",
                (linear_id,),
            ).fetchone()

        assert row is not None
        return _row_to_mirrored_issue(row)

    def get_issue(self, *, linear_id: str | None = None, identifier: str | None = None) -> MirroredIssueRecord | None:
        if linear_id is None and identifier is None:
            raise ValueError("get_issue requires linear_id or identifier.")

        with self.storage.session() as connection:
            if linear_id is not None:
                row = connection.execute(
                    "SELECT * FROM linear_issues WHERE linear_id = ?",
                    (linear_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM linear_issues WHERE identifier = ?",
                    (identifier,),
                ).fetchone()
        return _row_to_mirrored_issue(row) if row is not None else None

    def delete_issue(self, *, linear_id: str | None = None, identifier: str | None = None) -> bool:
        if linear_id is None and identifier is None:
            raise ValueError("delete_issue requires linear_id or identifier.")

        with self.storage.session() as connection:
            if linear_id is not None:
                cursor = connection.execute(
                    "DELETE FROM linear_issues WHERE linear_id = ?",
                    (linear_id,),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM linear_issues WHERE identifier = ?",
                    (identifier,),
                )
        return cursor.rowcount > 0

    def list_issues(self) -> list[MirroredIssueRecord]:
        with self.storage.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM linear_issues
                ORDER BY
                    CASE WHEN completed_at IS NULL AND canceled_at IS NULL THEN 0 ELSE 1 END,
                    priority DESC,
                    source_updated_at DESC,
                    identifier ASC
                """
            ).fetchall()
        return [_row_to_mirrored_issue(row) for row in rows]

    def list_child_issues(self, issue: MirroredIssueRecord) -> list[MirroredIssueRecord]:
        with self.storage.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM linear_issues
                WHERE parent_linear_id = ?
                   OR (? IS NOT NULL AND parent_identifier = ?)
                ORDER BY source_updated_at DESC, identifier ASC
                """,
                (issue.linear_id, issue.identifier, issue.identifier),
            ).fetchall()
        return [_row_to_mirrored_issue(row) for row in rows]

    def has_children(self, issue: MirroredIssueRecord) -> bool:
        with self.storage.session() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM linear_issues
                WHERE parent_linear_id = ?
                   OR (? IS NOT NULL AND parent_identifier = ?)
                LIMIT 1
                """,
                (issue.linear_id, issue.identifier, issue.identifier),
            ).fetchone()
        return row is not None

    def mark_issue_completed(
        self,
        issue: MirroredIssueRecord,
        *,
        completed_at: str,
        state_name: str | None = None,
        state_type: str | None = "completed",
    ) -> MirroredIssueRecord:
        return self.upsert_issue(
            linear_id=issue.linear_id,
            identifier=issue.identifier,
            title=issue.title,
            description=issue.description,
            team_id=issue.team_id,
            team_name=issue.team_name,
            state_id=issue.state_id,
            state_name=state_name or issue.state_name or "Done",
            state_type=state_type or issue.state_type,
            priority=issue.priority,
            project_id=issue.project_id,
            project_name=issue.project_name,
            parent_linear_id=issue.parent_linear_id,
            parent_identifier=issue.parent_identifier,
            assignee_id=issue.assignee_id,
            assignee_name=issue.assignee_name,
            labels=issue.labels,
            metadata=issue.metadata,
            source_updated_at=completed_at,
            created_at=issue.created_at,
            completed_at=completed_at,
            canceled_at=issue.canceled_at,
        )

    def get_ancestor_chain(self, issue: MirroredIssueRecord) -> tuple[MirroredIssueRecord, ...]:
        ancestors: list[MirroredIssueRecord] = []
        seen_linear_ids: set[str] = set()
        current_parent_linear_id = issue.parent_linear_id
        current_parent_identifier = issue.parent_identifier

        while current_parent_linear_id is not None or current_parent_identifier is not None:
            parent = self.get_issue(
                linear_id=current_parent_linear_id,
                identifier=current_parent_identifier if current_parent_linear_id is None else None,
            )
            if parent is None or parent.linear_id in seen_linear_ids:
                break
            seen_linear_ids.add(parent.linear_id)
            ancestors.append(parent)
            current_parent_linear_id = parent.parent_linear_id
            current_parent_identifier = parent.parent_identifier

        return tuple(ancestors)

    def reconcile_missing_from_snapshot(
        self,
        active_linear_ids: set[str],
    ) -> list[MirroredIssueRecord]:
        updated_records: list[MirroredIssueRecord] = []
        for record in self.list_issues():
            metadata = dict(record.metadata)
            should_mark_missing = record.linear_id not in active_linear_ids
            current_flag = bool(metadata.get("orx_reconciliation_missing_from_snapshot"))

            if should_mark_missing == current_flag:
                continue

            if should_mark_missing:
                metadata["orx_reconciliation_missing_from_snapshot"] = True
            else:
                metadata.pop("orx_reconciliation_missing_from_snapshot", None)

            updated_records.append(
                self.upsert_issue(
                    linear_id=record.linear_id,
                    identifier=record.identifier,
                    title=record.title,
                    description=record.description,
                    team_id=record.team_id,
                    team_name=record.team_name,
                    state_id=record.state_id,
                    state_name=record.state_name,
                    state_type=record.state_type,
                    priority=record.priority,
                    project_id=record.project_id,
                    project_name=record.project_name,
                    parent_linear_id=record.parent_linear_id,
                    parent_identifier=record.parent_identifier,
                    assignee_id=record.assignee_id,
                    assignee_name=record.assignee_name,
                    labels=record.labels,
                    metadata=metadata,
                    source_updated_at=record.source_updated_at,
                    created_at=record.created_at,
                    completed_at=record.completed_at,
                    canceled_at=record.canceled_at,
                )
            )

        return updated_records


def _row_to_mirrored_issue(row: Any) -> MirroredIssueRecord:
    return MirroredIssueRecord(
        linear_id=row["linear_id"],
        identifier=row["identifier"],
        title=row["title"],
        description=row["description"],
        team_id=row["team_id"],
        team_name=row["team_name"],
        state_id=row["state_id"],
        state_name=row["state_name"],
        state_type=row["state_type"],
        priority=int(row["priority"]) if row["priority"] is not None else None,
        project_id=row["project_id"],
        project_name=row["project_name"],
        parent_linear_id=row["parent_linear_id"],
        parent_identifier=row["parent_identifier"],
        assignee_id=row["assignee_id"],
        assignee_name=row["assignee_name"],
        labels=tuple(json.loads(row["labels_json"])),
        metadata=json.loads(row["metadata_json"]),
        source_updated_at=row["source_updated_at"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        canceled_at=row["canceled_at"],
        last_synced_at=row["last_synced_at"],
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
