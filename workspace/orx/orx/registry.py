"""Global project registry, dispatch lease, bot pool, and notification storage."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import normalize_project_key
from .storage import Storage


UNASSIGNED_BOT = "unassigned"
BOT_HEARTBEAT_STALE_SECONDS = 180


@dataclass(frozen=True)
class ProjectRegistration:
    project_key: str
    display_name: str
    repo_root: str
    runtime_home: str
    owning_bot: str
    owner_chat_id: int | None
    owner_thread_id: int | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    @property
    def assigned_bot(self) -> str | None:
        value = self.owning_bot.strip()
        if not value or value == UNASSIGNED_BOT:
            return None
        return value


@dataclass(frozen=True)
class BotRegistration:
    bot_identity: str
    telegram_chat_id: int | None
    telegram_thread_id: int | None
    default_display_name: str
    current_display_name: str | None
    desired_display_name: str
    name_sync_state: str
    name_sync_retry_at: str | None
    availability_state: str
    assigned_project_key: str | None
    assignment_id: str | None
    metadata: dict[str, Any]
    last_heartbeat_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BotAssignment:
    action: str
    project: ProjectRegistration
    bot: BotRegistration


@dataclass(frozen=True)
class DispatchLease:
    lease_key: str
    owner_id: str
    acquired_at: str


@dataclass(frozen=True)
class DispatchNotification:
    notification_id: int
    project_key: str
    target_bot: str
    assignment_id: str | None
    ingress_bot: str | None
    ingress_chat_id: str | None
    ingress_thread_id: str | None
    issue_key: str | None
    kind: str
    payload: dict[str, Any]
    created_at: str
    delivered_at: str | None


class DispatchLeaseConflictError(RuntimeError):
    """Raised when a competing global dispatch lease is already active."""


class ProjectRegistry:
    GLOBAL_DISPATCH_LEASE = "global-dispatch"
    DISPATCH_LEASE_STALE_SECONDS = 30

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_project(
        self,
        *,
        project_key: str,
        display_name: str,
        repo_root: str,
        runtime_home: str,
        owning_bot: str | None = None,
        owner_chat_id: int | None = None,
        owner_thread_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRegistration:
        normalized = normalize_project_key(project_key)
        now = _utc_now()
        requested_bot = _normalize_bot_identity(owning_bot)
        with self.storage.session() as connection:
            existing = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
            existing_bot = _normalize_bot_identity(existing["owning_bot"]) if existing is not None else None
            resolved_bot = existing_bot or requested_bot or UNASSIGNED_BOT
            resolved_chat_id = (
                existing["owner_chat_id"]
                if existing is not None and existing_bot is not None
                else owner_chat_id
            )
            resolved_thread_id = (
                existing["owner_thread_id"]
                if existing is not None and existing_bot is not None
                else owner_thread_id
            )
            created_at = existing["created_at"] if existing is not None else now
            connection.execute(
                """
                INSERT INTO project_registry(
                    project_key,
                    display_name,
                    repo_root,
                    runtime_home,
                    owning_bot,
                    owner_chat_id,
                    owner_thread_id,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    repo_root = excluded.repo_root,
                    runtime_home = excluded.runtime_home,
                    owning_bot = excluded.owning_bot,
                    owner_chat_id = excluded.owner_chat_id,
                    owner_thread_id = excluded.owner_thread_id,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    display_name.strip() or normalized,
                    repo_root,
                    runtime_home,
                    resolved_bot,
                    resolved_chat_id,
                    resolved_thread_id,
                    json.dumps(metadata or {}, sort_keys=True),
                    created_at,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
        assert row is not None
        return _row_to_project(row)

    def set_project_execution_thread(
        self,
        *,
        project_key: str,
        execution_thread_id: int | None,
        execution_chat_id: int | None = None,
    ) -> ProjectRegistration:
        normalized = normalize_project_key(project_key)
        return self.update_project_metadata(
            project_key=normalized,
            transform=lambda metadata: _with_execution_thread_binding(
                metadata,
                execution_thread_id=execution_thread_id,
                execution_chat_id=execution_chat_id,
            ),
        )

    def update_project_metadata(
        self,
        *,
        project_key: str,
        transform: Any,
    ) -> ProjectRegistration:
        normalized = normalize_project_key(project_key)
        now = _utc_now()
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown project {project_key}.")
            current_metadata = json.loads(row["metadata_json"])
            next_metadata = transform(dict(current_metadata))
            if not isinstance(next_metadata, dict):
                raise ValueError("Project metadata transform must return a JSON object.")
            connection.execute(
                """
                UPDATE project_registry
                SET metadata_json = ?, updated_at = ?
                WHERE project_key = ?
                """,
                (json.dumps(next_metadata, sort_keys=True), now, normalized),
            )
            updated = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
        assert updated is not None
        return _row_to_project(updated)

    def get_project_feature_lane(self, project_key: str) -> dict[str, Any] | None:
        project = self.get_project(project_key)
        if project is None:
            return None
        lane = project.metadata.get("feature_lane") if isinstance(project.metadata, dict) else None
        return _normalize_feature_lane(lane)

    def set_project_feature_lane(
        self,
        *,
        project_key: str,
        lane: dict[str, Any] | None,
    ) -> ProjectRegistration:
        normalized_lane = _normalize_feature_lane(lane)

        def transform(metadata: dict[str, Any]) -> dict[str, Any]:
            payload = dict(metadata)
            if normalized_lane is None:
                payload.pop("feature_lane", None)
                return payload
            payload["feature_lane"] = normalized_lane
            return payload

        return self.update_project_metadata(project_key=project_key, transform=transform)

    def get_project_reconciliation(self, project_key: str) -> dict[str, Any] | None:
        project = self.get_project(project_key)
        if project is None:
            return None
        record = project.metadata.get("reconciliation") if isinstance(project.metadata, dict) else None
        return _normalize_reconciliation(record)

    def set_project_reconciliation(
        self,
        *,
        project_key: str,
        reconciliation: dict[str, Any] | None,
    ) -> ProjectRegistration:
        normalized = _normalize_reconciliation(reconciliation)

        def transform(metadata: dict[str, Any]) -> dict[str, Any]:
            payload = dict(metadata)
            if normalized is None:
                payload.pop("reconciliation", None)
                return payload
            payload["reconciliation"] = normalized
            return payload

        return self.update_project_metadata(project_key=project_key, transform=transform)

    def get_project(self, project_key: str) -> ProjectRegistration | None:
        normalized = normalize_project_key(project_key)
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
        return _row_to_project(row) if row is not None else None

    def list_projects(self) -> list[ProjectRegistration]:
        with self.storage.session() as connection:
            rows = connection.execute(
                "SELECT * FROM project_registry ORDER BY project_key ASC"
            ).fetchall()
        return [_row_to_project(row) for row in rows]

    def delete_project(self, project_key: str) -> ProjectRegistration | None:
        normalized = normalize_project_key(project_key)
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            current_bot = _normalize_bot_identity(row["owning_bot"])
            if current_bot is not None:
                bot = connection.execute(
                    "SELECT * FROM bot_registry WHERE bot_identity = ?",
                    (current_bot,),
                ).fetchone()
                if bot is not None and bot["assigned_project_key"] == normalized:
                    connection.execute(
                        """
                        UPDATE bot_registry
                        SET assigned_project_key = NULL,
                            assignment_id = NULL,
                            availability_state = 'available',
                            desired_display_name = default_display_name,
                            name_sync_state = CASE
                                WHEN current_display_name = default_display_name THEN 'synced'
                                ELSE 'pending'
                            END,
                            updated_at = ?
                        WHERE bot_identity = ?
                        """,
                        ( _utc_now(), current_bot),
                    )
            connection.execute(
                "DELETE FROM dispatch_notifications WHERE project_key = ?",
                (normalized,),
            )
            connection.execute(
                "DELETE FROM project_registry WHERE project_key = ?",
                (normalized,),
            )
        return _row_to_project(row)

    def get_project_for_bot(self, owning_bot: str) -> ProjectRegistration | None:
        normalized = _normalize_bot_identity(owning_bot)
        if normalized is None:
            return None
        with self.storage.session() as connection:
            bot_row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
            assigned_project_key = (
                _normalize_optional(bot_row["assigned_project_key"]) if bot_row is not None else None
            )
            if assigned_project_key is not None:
                project_row = connection.execute(
                    "SELECT * FROM project_registry WHERE project_key = ?",
                    (assigned_project_key,),
                ).fetchone()
                if project_row is not None:
                    return _row_to_project(project_row)
            row = connection.execute(
                """
                SELECT * FROM project_registry
                WHERE owning_bot = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return _row_to_project(row) if row is not None else None

    def upsert_bot(
        self,
        *,
        bot_identity: str,
        default_display_name: str,
        telegram_chat_id: int | None = None,
        telegram_thread_id: int | None = None,
        availability_state: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BotRegistration:
        normalized = _required_bot_identity(bot_identity)
        now = _utc_now()
        with self.storage.session() as connection:
            existing = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
            current_display_name = (
                existing["current_display_name"]
                if existing is not None
                else None
            )
            desired_display_name = (
                existing["desired_display_name"]
                if existing is not None
                else default_display_name.strip() or normalized
            )
            name_sync_state = existing["name_sync_state"] if existing is not None else "idle"
            name_sync_retry_at = existing["name_sync_retry_at"] if existing is not None else None
            assigned_project_key = existing["assigned_project_key"] if existing is not None else None
            assignment_id = existing["assignment_id"] if existing is not None else None
            created_at = existing["created_at"] if existing is not None else now
            resolved_availability = (
                availability_state
                or (existing["availability_state"] if existing is not None else "available")
            )
            connection.execute(
                """
                INSERT INTO bot_registry(
                    bot_identity,
                    telegram_chat_id,
                    telegram_thread_id,
                    default_display_name,
                    current_display_name,
                    desired_display_name,
                    name_sync_state,
                    name_sync_retry_at,
                    availability_state,
                    assigned_project_key,
                    assignment_id,
                    metadata_json,
                    last_heartbeat_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_identity) DO UPDATE SET
                    telegram_chat_id = excluded.telegram_chat_id,
                    telegram_thread_id = excluded.telegram_thread_id,
                    default_display_name = excluded.default_display_name,
                    current_display_name = excluded.current_display_name,
                    desired_display_name = excluded.desired_display_name,
                    name_sync_state = excluded.name_sync_state,
                    name_sync_retry_at = excluded.name_sync_retry_at,
                    availability_state = excluded.availability_state,
                    assigned_project_key = excluded.assigned_project_key,
                    assignment_id = excluded.assignment_id,
                    metadata_json = excluded.metadata_json,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    telegram_chat_id if telegram_chat_id is not None else (existing["telegram_chat_id"] if existing is not None else None),
                    telegram_thread_id if telegram_thread_id is not None else (existing["telegram_thread_id"] if existing is not None else None),
                    default_display_name.strip() or normalized,
                    current_display_name,
                    desired_display_name,
                    name_sync_state,
                    name_sync_retry_at,
                    resolved_availability,
                    assigned_project_key,
                    assignment_id,
                    json.dumps(metadata or (json.loads(existing["metadata_json"]) if existing is not None else {}), sort_keys=True),
                    now,
                    created_at,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
        assert row is not None
        return _row_to_bot(row)

    def get_bot(self, bot_identity: str) -> BotRegistration | None:
        normalized = _normalize_bot_identity(bot_identity)
        if normalized is None:
            return None
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
        return _row_to_bot(row) if row is not None else None

    def list_bots(self) -> list[BotRegistration]:
        with self.storage.session() as connection:
            rows = connection.execute(
                "SELECT * FROM bot_registry ORDER BY bot_identity ASC"
            ).fetchall()
        return [_row_to_bot(row) for row in rows]

    def assign_project_bot(
        self,
        *,
        project_key: str,
        preferred_bot: str | None = None,
    ) -> BotAssignment | None:
        normalized_project = normalize_project_key(project_key)
        preferred = _normalize_bot_identity(preferred_bot)
        now = _utc_now()
        with self.storage.session() as connection:
            project_row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized_project,),
            ).fetchone()
            if project_row is None:
                raise ValueError(f"Unknown project {project_key}.")

            current_bot = _normalize_bot_identity(project_row["owning_bot"])
            if current_bot is not None:
                bot_row = connection.execute(
                    "SELECT * FROM bot_registry WHERE bot_identity = ?",
                    (current_bot,),
                ).fetchone()
                bot_assigned_project = (
                    _normalize_optional(bot_row["assigned_project_key"]) if bot_row is not None else None
                )
                if (
                    bot_row is not None
                    and str(bot_row["availability_state"]) in {"available", "assigned"}
                    and _bot_row_is_fresh(bot_row, now=now)
                    and (bot_assigned_project is None or bot_assigned_project == normalized_project)
                ):
                    assignment_id = bot_row["assignment_id"] or project_row["updated_at"] or uuid.uuid4().hex
                    connection.execute(
                        """
                        UPDATE bot_registry
                        SET assigned_project_key = ?,
                            assignment_id = ?,
                            availability_state = 'assigned',
                            updated_at = ?
                        WHERE bot_identity = ?
                        """,
                        (normalized_project, assignment_id, now, current_bot),
                    )
                    connection.execute(
                        """
                        UPDATE project_registry
                        SET owning_bot = ?,
                            owner_chat_id = ?,
                            owner_thread_id = ?,
                            metadata_json = ?,
                            updated_at = ?
                        WHERE project_key = ?
                        """,
                        (
                            current_bot,
                            bot_row["telegram_chat_id"],
                            bot_row["telegram_thread_id"],
                            json.dumps(
                                _with_execution_thread_binding(
                                    json.loads(project_row["metadata_json"]),
                                    execution_thread_id=bot_row["telegram_thread_id"],
                                    execution_chat_id=bot_row["telegram_chat_id"],
                                    preserve_existing=True,
                                ),
                                sort_keys=True,
                            ),
                            now,
                            normalized_project,
                        ),
                    )
                    _clear_other_project_assignments(
                        connection,
                        bot_identity=current_bot,
                        keep_project_key=normalized_project,
                        updated_at=now,
                    )
                    project = connection.execute(
                        "SELECT * FROM project_registry WHERE project_key = ?",
                        (normalized_project,),
                    ).fetchone()
                    bot = connection.execute(
                        "SELECT * FROM bot_registry WHERE bot_identity = ?",
                        (current_bot,),
                    ).fetchone()
                    assert project is not None and bot is not None
                    return BotAssignment(
                        action="reused",
                        project=_row_to_project(project),
                        bot=_row_to_bot(bot),
                    )

            candidate = None
            project_display_name = _normalize_project_display_name(project_row["display_name"])
            project_key_alias = _normalize_project_display_name(normalized_project)
            if project_display_name is not None or project_key_alias is not None:
                candidates = connection.execute(
                    """
                    SELECT *
                    FROM bot_registry
                    WHERE availability_state = 'available'
                      AND (assigned_project_key IS NULL OR assigned_project_key = '')
                    ORDER BY
                      CASE
                        WHEN LOWER(TRIM(default_display_name)) = ? THEN 0
                        WHEN LOWER(TRIM(default_display_name)) = ? THEN 1
                        ELSE 2
                      END,
                      COALESCE(last_heartbeat_at, updated_at) DESC,
                      bot_identity ASC
                    LIMIT 1
                    """,
                    (
                        project_display_name or "",
                        project_key_alias or "",
                    ),
                ).fetchall()
                for row in candidates:
                    if not _bot_row_is_fresh(row, now=now):
                        continue
                    candidate_name = _normalize_project_display_name(row["default_display_name"])
                    if candidate_name in {project_display_name, project_key_alias}:
                        candidate = row
                        break
            if candidate is None and preferred is not None:
                candidate = connection.execute(
                    """
                    SELECT * FROM bot_registry
                    WHERE bot_identity = ?
                      AND availability_state = 'available'
                      AND (assigned_project_key IS NULL OR assigned_project_key = '')
                    LIMIT 1
                    """,
                    (preferred,),
                ).fetchone()
                if candidate is not None and not _bot_row_is_fresh(candidate, now=now):
                    candidate = None
            if candidate is None:
                candidates = connection.execute(
                    """
                    SELECT * FROM bot_registry
                    WHERE availability_state = 'available'
                      AND (assigned_project_key IS NULL OR assigned_project_key = '')
                    ORDER BY COALESCE(last_heartbeat_at, updated_at) DESC, bot_identity ASC
                    LIMIT 20
                    """
                ).fetchall()
                for row in candidates:
                    if _bot_row_is_fresh(row, now=now):
                        candidate = row
                        break
            if candidate is None:
                return None

            assignment_id = uuid.uuid4().hex
            bot_identity = candidate["bot_identity"]
            connection.execute(
                """
                UPDATE bot_registry
                SET assigned_project_key = ?,
                    assignment_id = ?,
                    availability_state = 'assigned',
                    updated_at = ?
                WHERE bot_identity = ?
                  AND availability_state = 'available'
                  AND (assigned_project_key IS NULL OR assigned_project_key = '')
                """,
                (normalized_project, assignment_id, now, bot_identity),
            )
            if connection.total_changes <= 0:
                return None
            connection.execute(
                """
                UPDATE project_registry
                SET owning_bot = ?,
                    owner_chat_id = ?,
                    owner_thread_id = ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE project_key = ?
                """,
                (
                    bot_identity,
                    candidate["telegram_chat_id"],
                    candidate["telegram_thread_id"],
                    json.dumps(
                        _with_execution_thread_binding(
                            json.loads(project_row["metadata_json"]),
                            execution_thread_id=candidate["telegram_thread_id"],
                            execution_chat_id=candidate["telegram_chat_id"],
                            preserve_existing=True,
                        ),
                        sort_keys=True,
                    ),
                    now,
                    normalized_project,
                ),
            )
            _clear_other_project_assignments(
                connection,
                bot_identity=str(bot_identity),
                keep_project_key=normalized_project,
                updated_at=now,
            )
            project = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized_project,),
            ).fetchone()
            bot = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (bot_identity,),
            ).fetchone()
        assert project is not None and bot is not None
        return BotAssignment(
            action="assigned",
            project=_row_to_project(project),
            bot=_row_to_bot(bot),
        )

    def release_project_bot(self, *, project_key: str) -> BotAssignment | None:
        normalized_project = normalize_project_key(project_key)
        now = _utc_now()
        with self.storage.session() as connection:
            project_row = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized_project,),
            ).fetchone()
            if project_row is None:
                raise ValueError(f"Unknown project {project_key}.")
            current_bot = _normalize_bot_identity(project_row["owning_bot"])
            if current_bot is None:
                return None
            bot_row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (current_bot,),
            ).fetchone()
            if bot_row is None:
                connection.execute(
                    """
                    UPDATE project_registry
                    SET owning_bot = ?, owner_chat_id = NULL, owner_thread_id = NULL, updated_at = ?
                    WHERE project_key = ?
                    """,
                    (UNASSIGNED_BOT, now, normalized_project),
                )
                return None
            connection.execute(
                """
                UPDATE bot_registry
                SET assigned_project_key = NULL,
                    assignment_id = NULL,
                    availability_state = 'available',
                    desired_display_name = default_display_name,
                    name_sync_state = CASE
                        WHEN current_display_name = default_display_name THEN 'synced'
                        ELSE 'pending'
                    END,
                    name_sync_retry_at = NULL,
                    updated_at = ?
                WHERE bot_identity = ?
                """,
                (now, current_bot),
            )
            connection.execute(
                """
                UPDATE project_registry
                SET owning_bot = ?, owner_chat_id = NULL, owner_thread_id = NULL, updated_at = ?
                WHERE project_key = ?
                """,
                (UNASSIGNED_BOT, now, normalized_project),
            )
            project = connection.execute(
                "SELECT * FROM project_registry WHERE project_key = ?",
                (normalized_project,),
            ).fetchone()
            bot = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (current_bot,),
            ).fetchone()
        assert project is not None and bot is not None
        return BotAssignment(
            action="released",
            project=_row_to_project(project),
            bot=_row_to_bot(bot),
        )

    def set_bot_display_target(
        self,
        *,
        bot_identity: str,
        desired_display_name: str,
        assignment_id: str | None = None,
    ) -> BotRegistration | None:
        normalized = _normalize_bot_identity(bot_identity)
        if normalized is None:
            return None
        desired = desired_display_name.strip()
        now = _utc_now()
        with self.storage.session() as connection:
            row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                return None
            if assignment_id is not None and row["assignment_id"] not in {None, "", assignment_id}:
                return _row_to_bot(row)
            connection.execute(
                """
                UPDATE bot_registry
                SET desired_display_name = ?,
                    name_sync_state = CASE
                        WHEN current_display_name = ? THEN 'synced'
                        ELSE 'pending'
                    END,
                    name_sync_retry_at = NULL,
                    updated_at = ?
                WHERE bot_identity = ?
                """,
                (desired, desired, now, normalized),
            )
            updated = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
        assert updated is not None
        return _row_to_bot(updated)

    def record_bot_name_sync(
        self,
        *,
        bot_identity: str,
        current_display_name: str | None,
        desired_display_name: str | None = None,
        sync_state: str,
        retry_at: str | None = None,
    ) -> BotRegistration | None:
        normalized = _normalize_bot_identity(bot_identity)
        if normalized is None:
            return None
        now = _utc_now()
        with self.storage.session() as connection:
            existing = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
            if existing is None:
                return None
            resolved_display_name = existing["current_display_name"]
            if current_display_name is not None:
                resolved_display_name = current_display_name.strip() or None
            resolved_desired_display_name = existing["desired_display_name"]
            if desired_display_name is not None:
                normalized_desired_display_name = desired_display_name.strip()
                if normalized_desired_display_name:
                    resolved_desired_display_name = normalized_desired_display_name
            connection.execute(
                """
                UPDATE bot_registry
                SET current_display_name = ?,
                    desired_display_name = ?,
                    name_sync_state = ?,
                    name_sync_retry_at = ?,
                    updated_at = ?
                WHERE bot_identity = ?
                """,
                (
                    resolved_display_name,
                    resolved_desired_display_name,
                    sync_state,
                    retry_at,
                    now,
                    normalized,
                ),
            )
            row = connection.execute(
                "SELECT * FROM bot_registry WHERE bot_identity = ?",
                (normalized,),
            ).fetchone()
        return _row_to_bot(row) if row is not None else None

    def acquire_dispatch_lease(self, owner_id: str) -> DispatchLease:
        now = _utc_now()
        with self.storage.session() as connection:
            active = connection.execute(
                "SELECT * FROM dispatch_leases WHERE lease_key = ?",
                (self.GLOBAL_DISPATCH_LEASE,),
            ).fetchone()
            if active is not None and active["owner_id"] != owner_id:
                lease = _row_to_dispatch_lease(active)
                if not _dispatch_lease_is_stale(
                    lease,
                    now=now,
                    stale_seconds=self.DISPATCH_LEASE_STALE_SECONDS,
                ):
                    raise DispatchLeaseConflictError(
                        f"Global dispatch is already leased by {lease.owner_id}."
                    )
            connection.execute(
                """
                INSERT INTO dispatch_leases(lease_key, owner_id, acquired_at)
                VALUES (?, ?, ?)
                ON CONFLICT(lease_key) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    acquired_at = excluded.acquired_at
                """,
                (self.GLOBAL_DISPATCH_LEASE, owner_id, now),
            )
            row = connection.execute(
                "SELECT * FROM dispatch_leases WHERE lease_key = ?",
                (self.GLOBAL_DISPATCH_LEASE,),
            ).fetchone()
        assert row is not None
        return _row_to_dispatch_lease(row)

    def release_dispatch_lease(self, owner_id: str) -> None:
        with self.storage.session() as connection:
            connection.execute(
                """
                DELETE FROM dispatch_leases
                WHERE lease_key = ? AND owner_id = ?
                """,
                (self.GLOBAL_DISPATCH_LEASE, owner_id),
            )

    def create_notification(
        self,
        *,
        project_key: str,
        target_bot: str | None = None,
        owning_bot: str | None = None,
        kind: str,
        payload: dict[str, Any],
        assignment_id: str | None = None,
        ingress_bot: str | None = None,
        ingress_chat_id: str | None = None,
        ingress_thread_id: str | None = None,
        issue_key: str | None = None,
    ) -> DispatchNotification:
        normalized_bot = _required_bot_identity(target_bot or owning_bot or "")
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO dispatch_notifications(
                    project_key,
                    owning_bot,
                    target_bot,
                    assignment_id,
                    ingress_bot,
                    ingress_chat_id,
                    ingress_thread_id,
                    issue_key,
                    kind,
                    payload_json,
                    created_at,
                    delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    normalize_project_key(project_key),
                    normalized_bot,
                    normalized_bot,
                    assignment_id,
                    ingress_bot,
                    ingress_chat_id,
                    ingress_thread_id,
                    issue_key,
                    kind,
                    json.dumps(payload, sort_keys=True),
                    _utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM dispatch_notifications ORDER BY notification_id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        return _row_to_notification(row)

    def list_pending_notifications(
        self,
        *,
        target_bot: str | None = None,
        owning_bot: str | None = None,
        project_key: str | None = None,
        limit: int = 20,
    ) -> list[DispatchNotification]:
        normalized = _normalize_bot_identity(target_bot) or _normalize_bot_identity(owning_bot)
        if not normalized:
            raise ValueError("target_bot is required")
        normalized_project = normalize_project_key(project_key) if project_key else None
        with self.storage.session() as connection:
            if normalized_project:
                rows = connection.execute(
                    """
                    SELECT * FROM dispatch_notifications
                    WHERE target_bot = ? AND project_key = ? AND delivered_at IS NULL
                    ORDER BY notification_id ASC
                    LIMIT ?
                    """,
                    (normalized, normalized_project, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM dispatch_notifications
                    WHERE target_bot = ? AND delivered_at IS NULL
                    ORDER BY notification_id ASC
                    LIMIT ?
                    """,
                    (normalized, limit),
                ).fetchall()
        return [_row_to_notification(row) for row in rows]

    def acknowledge_notifications(self, notification_ids: list[int]) -> None:
        if not notification_ids:
            return
        placeholders = ", ".join("?" for _ in notification_ids)
        with self.storage.session() as connection:
            connection.execute(
                f"""
                UPDATE dispatch_notifications
                SET delivered_at = ?
                WHERE notification_id IN ({placeholders})
                """,
                (_utc_now(), *notification_ids),
            )


def _row_to_project(row: Any) -> ProjectRegistration:
    return ProjectRegistration(
        project_key=row["project_key"],
        display_name=row["display_name"],
        repo_root=row["repo_root"],
        runtime_home=row["runtime_home"],
        owning_bot=row["owning_bot"],
        owner_chat_id=row["owner_chat_id"],
        owner_thread_id=row["owner_thread_id"],
        metadata=json.loads(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_bot(row: Any) -> BotRegistration:
    return BotRegistration(
        bot_identity=row["bot_identity"],
        telegram_chat_id=row["telegram_chat_id"],
        telegram_thread_id=row["telegram_thread_id"],
        default_display_name=row["default_display_name"],
        current_display_name=row["current_display_name"],
        desired_display_name=row["desired_display_name"],
        name_sync_state=row["name_sync_state"],
        name_sync_retry_at=row["name_sync_retry_at"],
        availability_state=row["availability_state"],
        assigned_project_key=_normalize_optional(row["assigned_project_key"]),
        assignment_id=_normalize_optional(row["assignment_id"]),
        metadata=json.loads(row["metadata_json"]),
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_dispatch_lease(row: Any) -> DispatchLease:
    return DispatchLease(
        lease_key=row["lease_key"],
        owner_id=row["owner_id"],
        acquired_at=row["acquired_at"],
    )


def _dispatch_lease_is_stale(
    lease: DispatchLease,
    *,
    now: str,
    stale_seconds: int,
) -> bool:
    acquired_at = datetime.fromisoformat(lease.acquired_at)
    current = datetime.fromisoformat(now)
    return (current - acquired_at).total_seconds() > stale_seconds


def _row_to_notification(row: Any) -> DispatchNotification:
    return DispatchNotification(
        notification_id=int(row["notification_id"]),
        project_key=row["project_key"],
        target_bot=row["target_bot"] or row["owning_bot"],
        assignment_id=_normalize_optional(row["assignment_id"]),
        ingress_bot=row["ingress_bot"],
        ingress_chat_id=row["ingress_chat_id"],
        ingress_thread_id=row["ingress_thread_id"],
        issue_key=row["issue_key"],
        kind=row["kind"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
    )


def _clear_other_project_assignments(
    connection: Any,
    *,
    bot_identity: str,
    keep_project_key: str,
    updated_at: str,
) -> None:
    connection.execute(
        """
        UPDATE project_registry
        SET owning_bot = ?,
            owner_chat_id = NULL,
            owner_thread_id = NULL,
            updated_at = ?
        WHERE owning_bot = ?
          AND project_key != ?
        """,
        (
            UNASSIGNED_BOT,
            updated_at,
            bot_identity,
            keep_project_key,
        ),
    )


def _required_bot_identity(value: str) -> str:
    normalized = _normalize_bot_identity(value)
    if normalized is None:
        raise ValueError("bot_identity must not be empty")
    return normalized


def _normalize_bot_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized == UNASSIGNED_BOT:
        return None
    return normalized


def _normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_project_display_name(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().lower().split())
    return normalized or None


def _with_execution_thread_binding(
    metadata: dict[str, Any] | None,
    *,
    execution_thread_id: int | None,
    execution_chat_id: int | None,
    preserve_existing: bool = False,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    if preserve_existing and payload.get("execution_thread_id") is not None:
        return payload
    if execution_thread_id is None:
        payload.pop("execution_thread_id", None)
        payload.pop("execution_chat_id", None)
        return payload
    payload["execution_thread_id"] = execution_thread_id
    if execution_chat_id is not None:
        payload["execution_chat_id"] = execution_chat_id
    return payload


def _normalize_feature_lane(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    feature_key = _normalize_optional(value.get("feature_key"))
    packet_key = _normalize_optional(value.get("packet_key")) or feature_key
    lane_state = _normalize_optional(value.get("lane_state")) or "idle"
    last_issue_key = _normalize_optional(value.get("last_issue_key"))
    last_issue_title = _normalize_optional(value.get("last_issue_title"))
    merge_target = _normalize_optional(value.get("merge_target")) or "main"
    merge_strategy = _normalize_optional(value.get("merge_strategy")) or "hil_merge_to_main"
    release_action = _normalize_optional(value.get("release_action"))
    release_note = _normalize_optional(value.get("release_note"))
    updated_at = _normalize_optional(value.get("updated_at"))
    if feature_key is None:
        return None
    return {
        "feature_key": feature_key,
        "packet_key": packet_key,
        "lane_state": lane_state,
        "release_required": bool(value.get("release_required")),
        "last_issue_key": last_issue_key,
        "last_issue_title": last_issue_title,
        "merge_target": merge_target,
        "merge_strategy": merge_strategy,
        "release_action": release_action,
        "release_note": release_note,
        "updated_at": updated_at,
    }


def _normalize_reconciliation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = _normalize_optional(value.get("status"))
    event_kind = _normalize_optional(value.get("event_kind"))
    action = _normalize_optional(value.get("action"))
    reason = _normalize_optional(value.get("reason"))
    final_summary = _normalize_optional(value.get("final_summary"))
    raw_status = _normalize_optional(value.get("raw_status"))
    issue_key = _normalize_optional(value.get("issue_key"))
    checkpoint_commit = _normalize_optional(value.get("checkpoint_commit"))
    transcript_excerpt = _normalize_optional(value.get("transcript_excerpt"))
    updated_at = _normalize_optional(value.get("updated_at"))
    ui_mode = _normalize_optional(value.get("ui_mode"))
    design_state = _normalize_optional(value.get("design_state"))
    contract_state = _normalize_optional(value.get("contract_state"))
    design_reference = _normalize_optional(value.get("design_reference"))
    contract_reference = _normalize_optional(value.get("contract_reference"))
    review_kind = _normalize_optional(value.get("review_kind"))
    verification_surface = _normalize_optional(value.get("verification_surface"))
    changed_files_raw = value.get("changed_files")
    changed_files = []
    if isinstance(changed_files_raw, list):
        changed_files = [item for item in (_normalize_optional(entry) for entry in changed_files_raw) if item]
    verification_ran_raw = value.get("verification_ran")
    verification_ran = []
    if isinstance(verification_ran_raw, list):
        verification_ran = [item for item in (_normalize_optional(entry) for entry in verification_ran_raw) if item]
    verification_failed_raw = value.get("verification_failed")
    verification_failed = []
    if isinstance(verification_failed_raw, list):
        verification_failed = [
            item for item in (_normalize_optional(entry) for entry in verification_failed_raw) if item
        ]
    artifacts_raw = value.get("artifacts")
    artifacts = []
    if isinstance(artifacts_raw, list):
        artifacts = [item for item in (_normalize_optional(entry) for entry in artifacts_raw) if item]
    design_artifacts_raw = value.get("design_artifacts")
    design_artifacts = []
    if isinstance(design_artifacts_raw, list):
        design_artifacts = [item for item in (_normalize_optional(entry) for entry in design_artifacts_raw) if item]
    contract_artifacts_raw = value.get("contract_artifacts")
    contract_artifacts = []
    if isinstance(contract_artifacts_raw, list):
        contract_artifacts = [item for item in (_normalize_optional(entry) for entry in contract_artifacts_raw) if item]
    if not any(
        (
            status,
            event_kind,
            action,
            reason,
            final_summary,
            raw_status,
            issue_key,
            checkpoint_commit,
            transcript_excerpt,
            changed_files,
            verification_ran,
            verification_failed,
            artifacts,
            ui_mode,
            design_state,
            contract_state,
            design_reference,
            contract_reference,
            review_kind,
            verification_surface,
            design_artifacts,
            contract_artifacts,
        )
    ):
        return None
    return {
        "status": status,
        "event_kind": event_kind,
        "action": action,
        "reason": reason,
        "final_summary": final_summary,
        "raw_status": raw_status,
        "issue_key": issue_key,
        "checkpoint_commit": checkpoint_commit,
        "checkpoint_created": bool(value.get("checkpoint_created")),
        "transcript_excerpt": transcript_excerpt,
        "changed_files": changed_files,
        "verification_ran": verification_ran,
        "verification_failed": verification_failed,
        "artifacts": artifacts,
        "ui_mode": ui_mode,
        "design_state": design_state,
        "contract_state": contract_state,
        "design_reference": design_reference,
        "contract_reference": contract_reference,
        "review_kind": review_kind,
        "verification_surface": verification_surface,
        "design_artifacts": design_artifacts,
        "contract_artifacts": contract_artifacts,
        "updated_at": updated_at,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bot_row_is_fresh(row: Any, *, now: str) -> bool:
    heartbeat = _normalize_optional(row["last_heartbeat_at"])
    if heartbeat is None:
        return False
    heartbeat_at = _parse_timestamp(heartbeat)
    now_at = _parse_timestamp(now)
    if heartbeat_at is None or now_at is None:
        return False
    return (now_at - heartbeat_at).total_seconds() <= BOT_HEARTBEAT_STALE_SECONDS


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
