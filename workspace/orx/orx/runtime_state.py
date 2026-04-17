"""Compact durable runtime state for daemon/operator visibility."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .storage import Storage


DAEMON_LAST_TICK_KEY = "daemon:last_tick"


@dataclass(frozen=True)
class RuntimeStateRecord:
    key: str
    value: dict[str, Any]
    updated_at: str


class RuntimeStateStore:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def put_json(self, *, key: str, value: dict[str, Any]) -> RuntimeStateRecord:
        now = _utc_now()
        with self.storage.session() as connection:
            connection.execute(
                """
                INSERT INTO runtime_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, sort_keys=True), now),
            )
            row = connection.execute(
                "SELECT key, value, updated_at FROM runtime_state WHERE key = ?",
                (key,),
            ).fetchone()
        assert row is not None
        return _row_to_record(row)

    def get_json(self, *, key: str) -> RuntimeStateRecord | None:
        if not self.storage.paths.db_path.exists():
            return None
        with self.storage.session() as connection:
            try:
                row = connection.execute(
                    "SELECT key, value, updated_at FROM runtime_state WHERE key = ?",
                    (key,),
                ).fetchone()
            except sqlite3.OperationalError as error:
                if "no such table" in str(error):
                    return None
                raise
        return _row_to_record(row) if row is not None else None


class DaemonStateService:
    def __init__(self, storage: Storage) -> None:
        self.store = RuntimeStateStore(storage)

    def record_last_tick(self, payload: dict[str, Any]) -> RuntimeStateRecord:
        return self.store.put_json(key=DAEMON_LAST_TICK_KEY, value=payload)

    def get_last_tick(self) -> RuntimeStateRecord | None:
        return self.store.get_json(key=DAEMON_LAST_TICK_KEY)


def _row_to_record(row: Any) -> RuntimeStateRecord:
    value = json.loads(row["value"])
    if not isinstance(value, dict):
        raise ValueError(f"runtime_state {row['key']!r} did not contain a JSON object")
    return RuntimeStateRecord(
        key=str(row["key"]),
        value=value,
        updated_at=str(row["updated_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
