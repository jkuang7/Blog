"""Durable continuity state and restart replay helpers for ORX."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .storage import Storage


@dataclass(frozen=True)
class ContinuityRecord:
    issue_key: str
    runner_id: str
    objective: str
    slice_goal: str
    acceptance: tuple[str, ...]
    validation_plan: tuple[str, ...]
    blockers: tuple[str, ...]
    discovered_gaps: tuple[str, ...]
    verified_delta: str | None
    next_slice: str | None
    failure_signatures: tuple[str, ...]
    artifact_pointers: tuple[str, ...]
    idempotency_key: str
    resume_context: dict[str, Any]
    active_slice_id: str | None
    active_command_id: int | None
    last_result_status: str | None
    last_result_summary: str | None
    last_result_at: str | None
    no_delta_count: int
    consecutive_failure_count: int
    created_at: str
    updated_at: str


class ContinuityStore:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def upsert_state(
        self,
        *,
        issue_key: str,
        runner_id: str,
        objective: str,
        slice_goal: str,
        acceptance: tuple[str, ...],
        validation_plan: tuple[str, ...],
        blockers: tuple[str, ...],
        discovered_gaps: tuple[str, ...],
        verified_delta: str | None,
        next_slice: str | None,
        failure_signatures: tuple[str, ...],
        artifact_pointers: tuple[str, ...],
        idempotency_key: str,
        resume_context: dict[str, Any],
        active_slice_id: str | None,
        active_command_id: int | None,
        last_result_status: str | None,
        last_result_summary: str | None,
        last_result_at: str | None,
        no_delta_count: int,
        consecutive_failure_count: int,
    ) -> ContinuityRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO continuity_state(
                    issue_key,
                    runner_id,
                    objective,
                    slice_goal,
                    acceptance_json,
                    validation_plan_json,
                    blockers_json,
                    discovered_gaps_json,
                    verified_delta,
                    next_slice,
                    failure_signatures_json,
                    artifact_pointers_json,
                    idempotency_key,
                    resume_context_json,
                    active_slice_id,
                    active_command_id,
                    last_result_status,
                    last_result_summary,
                    last_result_at,
                    no_delta_count,
                    consecutive_failure_count,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_key, runner_id) DO UPDATE SET
                    objective = excluded.objective,
                    slice_goal = excluded.slice_goal,
                    acceptance_json = excluded.acceptance_json,
                    validation_plan_json = excluded.validation_plan_json,
                    blockers_json = excluded.blockers_json,
                    discovered_gaps_json = excluded.discovered_gaps_json,
                    verified_delta = excluded.verified_delta,
                    next_slice = excluded.next_slice,
                    failure_signatures_json = excluded.failure_signatures_json,
                    artifact_pointers_json = excluded.artifact_pointers_json,
                    idempotency_key = excluded.idempotency_key,
                    resume_context_json = excluded.resume_context_json,
                    active_slice_id = excluded.active_slice_id,
                    active_command_id = excluded.active_command_id,
                    last_result_status = excluded.last_result_status,
                    last_result_summary = excluded.last_result_summary,
                    last_result_at = excluded.last_result_at,
                    no_delta_count = excluded.no_delta_count,
                    consecutive_failure_count = excluded.consecutive_failure_count,
                    updated_at = excluded.updated_at
                """,
                (
                    issue_key,
                    runner_id,
                    objective,
                    slice_goal,
                    json.dumps(list(acceptance)),
                    json.dumps(list(validation_plan)),
                    json.dumps(list(blockers)),
                    json.dumps(list(discovered_gaps)),
                    verified_delta or "",
                    next_slice,
                    json.dumps(list(failure_signatures)),
                    json.dumps(list(artifact_pointers)),
                    idempotency_key,
                    json.dumps(resume_context, sort_keys=True),
                    active_slice_id,
                    active_command_id,
                    last_result_status,
                    last_result_summary,
                    last_result_at,
                    no_delta_count,
                    consecutive_failure_count,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM continuity_state
                WHERE issue_key = ? AND runner_id = ?
                """,
                (issue_key, runner_id),
            ).fetchone()
        assert row is not None
        return _row_to_continuity(row)

    def get_state(self, issue_key: str, runner_id: str) -> ContinuityRecord | None:
        with self.storage.session() as connection:
            row = connection.execute(
                """
                SELECT * FROM continuity_state
                WHERE issue_key = ? AND runner_id = ?
                """,
                (issue_key, runner_id),
            ).fetchone()
        return _row_to_continuity(row) if row is not None else None

    def list_incomplete_states(self) -> list[ContinuityRecord]:
        with self.storage.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM continuity_state
                WHERE active_slice_id IS NOT NULL
                ORDER BY updated_at ASC, issue_key ASC, runner_id ASC
                """
            ).fetchall()
        return [_row_to_continuity(row) for row in rows]


class ContinuityService:
    def __init__(self, storage: Storage) -> None:
        self.store = ContinuityStore(storage)

    def begin_slice(
        self,
        *,
        issue_key: str,
        runner_id: str,
        objective: str,
        slice_goal: str | None,
        acceptance: list[str],
        validation_plan: list[str] | None,
        blockers: list[str] | None,
        discovered_gaps: list[str] | None,
        idempotency_key: str | None,
        resume_context: dict[str, Any] | None,
        active_slice_id: str,
        active_command_id: int | None,
        session_name: str,
        pane_target: str,
        transport: str,
    ) -> ContinuityRecord:
        current = self.store.get_state(issue_key, runner_id)
        normalized_goal = _normalize_required(slice_goal or objective, field_name="slice_goal")
        merged_context = _merge_resume_context(
            current.resume_context if current is not None else {},
            resume_context or {},
            {
                "issue_key": issue_key,
                "runner_id": runner_id,
                "session_name": session_name,
                "pane_target": pane_target,
                "transport": transport,
                "active_slice_id": active_slice_id,
                "active_command_id": active_command_id,
                "decision_epoch": active_slice_id,
            },
        )
        return self.store.upsert_state(
            issue_key=issue_key,
            runner_id=runner_id,
            objective=_normalize_required(objective, field_name="objective"),
            slice_goal=normalized_goal,
            acceptance=_normalize_list(acceptance, field_name="acceptance"),
            validation_plan=_normalize_list(
                validation_plan or acceptance,
                field_name="validation_plan",
            ),
            blockers=_normalize_list(blockers or [], field_name="blockers"),
            discovered_gaps=_normalize_list(
                discovered_gaps or [],
                field_name="discovered_gaps",
            ),
            verified_delta=current.verified_delta if current is not None else None,
            next_slice=normalized_goal,
            failure_signatures=current.failure_signatures if current is not None else (),
            artifact_pointers=current.artifact_pointers if current is not None else (),
            idempotency_key=_normalize_required(
                idempotency_key or active_slice_id,
                field_name="idempotency_key",
            ),
            resume_context=merged_context,
            active_slice_id=active_slice_id,
            active_command_id=active_command_id,
            last_result_status=current.last_result_status if current is not None else None,
            last_result_summary=current.last_result_summary if current is not None else None,
            last_result_at=current.last_result_at if current is not None else None,
            no_delta_count=current.no_delta_count if current is not None else 0,
            consecutive_failure_count=current.consecutive_failure_count
            if current is not None
            else 0,
        )

    def complete_slice(
        self,
        *,
        issue_key: str,
        runner_id: str,
        status: str,
        summary: str,
        verified: bool,
        next_slice: str | None,
        artifacts: tuple[str, ...],
        submitted_at: str,
        session_name: str,
        pane_target: str,
        transport: str,
    ) -> ContinuityRecord:
        current = self.store.get_state(issue_key, runner_id)
        if current is None:
            raise ValueError(
                f"No continuity state exists for issue {issue_key} and runner {runner_id}."
            )

        failure_signatures = current.failure_signatures
        if status != "success":
            failure_signatures = _append_unique(failure_signatures, summary)

        updated_artifacts = _append_unique(current.artifact_pointers, *artifacts)
        updated_verified_delta = summary if verified else current.verified_delta
        no_delta_count = (
            0
            if verified and current.verified_delta != summary
            else current.no_delta_count + 1
        )
        consecutive_failure_count = (
            0 if status == "success" else current.consecutive_failure_count + 1
        )
        merged_context = _merge_resume_context(
            current.resume_context,
            {},
            {
                "issue_key": issue_key,
                "runner_id": runner_id,
                "session_name": session_name,
                "pane_target": pane_target,
                "transport": transport,
                "active_slice_id": None,
                "active_command_id": None,
                "last_result_status": status,
                "last_result_at": submitted_at,
            },
        )
        return self.store.upsert_state(
            issue_key=issue_key,
            runner_id=runner_id,
            objective=current.objective,
            slice_goal=current.slice_goal,
            acceptance=current.acceptance,
            validation_plan=current.validation_plan,
            blockers=current.blockers,
            discovered_gaps=current.discovered_gaps,
            verified_delta=updated_verified_delta,
            next_slice=next_slice or current.next_slice,
            failure_signatures=failure_signatures,
            artifact_pointers=updated_artifacts,
            idempotency_key=current.idempotency_key,
            resume_context=merged_context,
            active_slice_id=None,
            active_command_id=None,
            last_result_status=status,
            last_result_summary=summary,
            last_result_at=submitted_at,
            no_delta_count=no_delta_count,
            consecutive_failure_count=consecutive_failure_count,
        )

    def get_state(self, issue_key: str, runner_id: str) -> ContinuityRecord | None:
        return self.store.get_state(issue_key, runner_id)

    def get_next_slice(self, issue_key: str, runner_id: str) -> str | None:
        state = self.get_state(issue_key, runner_id)
        return state.next_slice if state is not None else None

    def list_recovery_candidates(self) -> list[ContinuityRecord]:
        return self.store.list_incomplete_states()

    def apply_handoff_interpretation(
        self,
        *,
        issue_key: str,
        runner_id: str,
        next_slice: str | None,
        blockers: list[str] | None = None,
        discovered_gaps: list[str] | None = None,
        resume_context_updates: dict[str, Any] | None = None,
    ) -> ContinuityRecord:
        current = self.store.get_state(issue_key, runner_id)
        if current is None:
            raise ValueError(
                f"No continuity state exists for issue {issue_key} and runner {runner_id}."
            )

        merged_context = _merge_resume_context(
            current.resume_context,
            resume_context_updates or {},
            {
                "continuity_revision": current.updated_at,
            },
        )
        return self.store.upsert_state(
            issue_key=issue_key,
            runner_id=runner_id,
            objective=current.objective,
            slice_goal=current.slice_goal,
            acceptance=current.acceptance,
            validation_plan=current.validation_plan,
            blockers=_normalize_list(
                blockers if blockers is not None else list(current.blockers),
                field_name="blockers",
            ),
            discovered_gaps=_normalize_list(
                discovered_gaps if discovered_gaps is not None else list(current.discovered_gaps),
                field_name="discovered_gaps",
            ),
            verified_delta=current.verified_delta,
            next_slice=next_slice,
            failure_signatures=current.failure_signatures,
            artifact_pointers=current.artifact_pointers,
            idempotency_key=current.idempotency_key,
            resume_context=merged_context,
            active_slice_id=current.active_slice_id,
            active_command_id=current.active_command_id,
            last_result_status=current.last_result_status,
            last_result_summary=current.last_result_summary,
            last_result_at=current.last_result_at,
            no_delta_count=current.no_delta_count,
            consecutive_failure_count=current.consecutive_failure_count,
        )


def _normalize_required(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty.")
    return normalized


def _normalize_list(values: list[str], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in values if item.strip())
    if field_name in {"acceptance", "validation_plan"} and not normalized:
        raise ValueError(f"{field_name} cannot be empty.")
    return normalized


def _append_unique(existing: tuple[str, ...], *values: str) -> tuple[str, ...]:
    items = list(existing)
    seen = set(existing)
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            items.append(normalized)
            seen.add(normalized)
    return tuple(items)


def _merge_resume_context(
    current: dict[str, Any],
    incoming: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    merged.update(incoming)
    merged.update(updates)
    return merged


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_continuity(row: Any) -> ContinuityRecord:
    verified_delta = row["verified_delta"] or None
    return ContinuityRecord(
        issue_key=row["issue_key"],
        runner_id=row["runner_id"],
        objective=row["objective"],
        slice_goal=row["slice_goal"],
        acceptance=tuple(json.loads(row["acceptance_json"])),
        validation_plan=tuple(json.loads(row["validation_plan_json"])),
        blockers=tuple(json.loads(row["blockers_json"])),
        discovered_gaps=tuple(json.loads(row["discovered_gaps_json"])),
        verified_delta=verified_delta,
        next_slice=row["next_slice"],
        failure_signatures=tuple(json.loads(row["failure_signatures_json"])),
        artifact_pointers=tuple(json.loads(row["artifact_pointers_json"])),
        idempotency_key=row["idempotency_key"],
        resume_context=json.loads(row["resume_context_json"]),
        active_slice_id=row["active_slice_id"],
        active_command_id=int(row["active_command_id"])
        if row["active_command_id"] is not None
        else None,
        last_result_status=row["last_result_status"],
        last_result_summary=row["last_result_summary"],
        last_result_at=row["last_result_at"],
        no_delta_count=int(row["no_delta_count"]),
        consecutive_failure_count=int(row["consecutive_failure_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
