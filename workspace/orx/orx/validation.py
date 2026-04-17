"""Durable validation evidence ledger for ORX."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .storage import Storage


VALIDATION_RESULTS = {"passed", "failed", "blocked", "degraded"}
VALIDATION_CONFIDENCE = {"confirmed", "degraded", "unknown"}


@dataclass(frozen=True)
class ValidationRecord:
    validation_id: int
    issue_key: str
    runner_id: str
    surface: str
    tool: str
    result: str
    confidence: str
    summary: str
    details: dict[str, Any]
    blockers: list[str]
    created_at: str


class ValidationLedgerService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def record(
        self,
        *,
        issue_key: str,
        runner_id: str,
        surface: str,
        tool: str,
        result: str,
        confidence: str,
        summary: str,
        details: dict[str, Any] | None = None,
        blockers: list[str] | None = None,
    ) -> ValidationRecord:
        normalized_result = result.strip().lower()
        normalized_confidence = confidence.strip().lower()
        if normalized_result not in VALIDATION_RESULTS:
            raise ValueError(f"Unsupported validation result {result!r}.")
        if normalized_confidence not in VALIDATION_CONFIDENCE:
            raise ValueError(f"Unsupported validation confidence {confidence!r}.")

        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO validation_records(
                    issue_key,
                    runner_id,
                    surface,
                    tool,
                    result,
                    confidence,
                    summary,
                    details_json,
                    blockers_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_key,
                    runner_id,
                    surface.strip(),
                    tool.strip(),
                    normalized_result,
                    normalized_confidence,
                    summary.strip(),
                    json.dumps(details or {}, sort_keys=True),
                    json.dumps(blockers or []),
                    _utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM validation_records ORDER BY validation_id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        return _row_to_validation(row)

    def list(
        self,
        *,
        issue_key: str | None = None,
        runner_id: str | None = None,
        limit: int = 20,
    ) -> list[ValidationRecord]:
        query = """
            SELECT * FROM validation_records
            WHERE 1 = 1
        """
        params: list[object] = []
        if issue_key is not None:
            query += " AND issue_key = ?"
            params.append(issue_key)
        if runner_id is not None:
            query += " AND runner_id = ?"
            params.append(runner_id)
        query += " ORDER BY validation_id DESC LIMIT ?"
        params.append(limit)
        with self.storage.session() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_row_to_validation(row) for row in rows]

    def latest(self, *, issue_key: str, runner_id: str) -> ValidationRecord | None:
        records = self.list(issue_key=issue_key, runner_id=runner_id, limit=1)
        return records[0] if records else None


def _row_to_validation(row: Any) -> ValidationRecord:
    details = json.loads(row["details_json"])
    blockers = json.loads(row["blockers_json"])
    if not isinstance(details, dict):
        raise ValueError("validation details must decode to a JSON object")
    if not isinstance(blockers, list):
        raise ValueError("validation blockers must decode to a JSON array")
    return ValidationRecord(
        validation_id=int(row["validation_id"]),
        issue_key=str(row["issue_key"]),
        runner_id=str(row["runner_id"]),
        surface=str(row["surface"]),
        tool=str(row["tool"]),
        result=str(row["result"]),
        confidence=str(row["confidence"]),
        summary=str(row["summary"]),
        details=details,
        blockers=[item for item in blockers if isinstance(item, str)],
        created_at=str(row["created_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
