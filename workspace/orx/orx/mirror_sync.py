"""Linear mirror ingest and reconciliation flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .metadata import parse_orx_metadata
from .mirror import LinearMirrorRepository, MirroredIssueRecord


@dataclass(frozen=True)
class ReconciliationResult:
    upserted: tuple[MirroredIssueRecord, ...]
    marked_missing: tuple[MirroredIssueRecord, ...]


class MirrorSyncService:
    def __init__(self, repository: LinearMirrorRepository) -> None:
        self.repository = repository

    def ingest_issue_payload(self, payload: dict[str, Any]) -> MirroredIssueRecord:
        description = _string(payload.get("description", ""))
        parsed = parse_orx_metadata(description)

        return self.repository.upsert_issue(
            linear_id=_first_present(payload, "linear_id", "issue_id", "uuid", "id"),
            identifier=_first_present(payload, "identifier", "issue_key", "key", "id"),
            title=_first_present(payload, "title"),
            description=description,
            team_id=_first_present(payload, "team_id", "teamId"),
            team_name=_team_name(payload),
            state_id=_optional(payload, "state_id", "stateId"),
            state_name=_first_present(payload, "state_name", "status"),
            state_type=_optional(payload, "state_type"),
            priority=_optional_int(payload, "priority"),
            project_id=_optional(payload, "project_id", "projectId"),
            project_name=_optional(payload, "project_name", "project"),
            parent_linear_id=_optional(payload, "parent_linear_id", "parentId"),
            parent_identifier=_optional(payload, "parent_identifier"),
            assignee_id=_optional(payload, "assignee_id", "assigneeId"),
            assignee_name=_optional(payload, "assignee_name"),
            labels=_labels(payload),
            metadata=parsed.metadata,
            source_updated_at=_first_present(payload, "source_updated_at", "updatedAt"),
            created_at=_optional(payload, "created_at", "createdAt"),
            completed_at=_optional(payload, "completed_at", "completedAt"),
            canceled_at=_optional(payload, "canceled_at", "canceledAt"),
        )

    def reconcile_snapshot(self, payloads: list[dict[str, Any]]) -> ReconciliationResult:
        upserted = [self.ingest_issue_payload(payload) for payload in payloads]
        active_linear_ids = {record.linear_id for record in upserted}
        marked_missing = self.repository.reconcile_missing_from_snapshot(active_linear_ids)
        return ReconciliationResult(
            upserted=tuple(upserted),
            marked_missing=tuple(marked_missing),
        )


def _first_present(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Missing required payload keys: {', '.join(keys)}")


def _optional(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
        elif value is not None:
            return str(value)
    return None


def _optional_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        return int(value)
    return None


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _team_name(payload: dict[str, Any]) -> str:
    team = payload.get("team")
    if isinstance(team, dict):
        name = team.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return _first_present(payload, "team_name", "team")


def _labels(payload: dict[str, Any]) -> list[str]:
    labels = payload.get("labels")
    if labels is None:
        return []
    if not isinstance(labels, list):
        raise ValueError("labels must be a list when provided.")

    resolved: list[str] = []
    for item in labels:
        if isinstance(item, str):
            if item.strip():
                resolved.append(item.strip())
            continue
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                resolved.append(name.strip())
                continue
        raise ValueError("labels entries must be strings or {name: ...} objects.")
    return resolved
