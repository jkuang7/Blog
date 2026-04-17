"""Host-readiness checks for live ORX operation."""

from __future__ import annotations

import os
import shutil
from typing import Any

from .env import load_repo_env
from .runtime_state import DaemonStateService
from .storage import CURRENT_SCHEMA_VERSION, Storage


class HostDoctorService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.daemon_state = DaemonStateService(storage)

    def payload(self) -> dict[str, Any]:
        checks = [
            self._runtime_check(),
            self._tmux_check(),
            self._linear_key_check(),
            self._daemon_check(),
        ]
        blockers = [
            check["summary"]
            for check in checks
            if check["severity"] == "blocker"
        ]
        warnings = [
            check["summary"]
            for check in checks
            if check["severity"] == "warning"
        ]
        return {
            "ok": not blockers,
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
        }

    def _runtime_check(self) -> dict[str, Any]:
        paths = self.storage.paths
        db_exists = paths.db_path.exists()
        schema_version = self.storage.current_version()
        ok = db_exists and schema_version == CURRENT_SCHEMA_VERSION
        return {
            "name": "runtime",
            "ok": ok,
            "severity": "ok" if ok else "blocker",
            "summary": (
                "Runtime home and database are ready."
                if ok
                else "Runtime database is missing or not bootstrapped to the current schema."
            ),
            "details": {
                "home": str(paths.home),
                "db_path": str(paths.db_path),
                "db_exists": db_exists,
                "schema_version": schema_version,
                "expected_schema_version": CURRENT_SCHEMA_VERSION,
            },
        }

    def _tmux_check(self) -> dict[str, Any]:
        tmux_path = shutil.which("tmux")
        ok = tmux_path is not None
        return {
            "name": "tmux",
            "ok": ok,
            "severity": "ok" if ok else "blocker",
            "summary": "tmux is available." if ok else "tmux is not installed or not on PATH.",
            "details": {
                "path": tmux_path,
            },
        }

    def _linear_key_check(self) -> dict[str, Any]:
        load_repo_env()
        configured = any(
            value.strip()
            for value in (
                os.environ.get("ORX_LINEAR_API_KEY", ""),
                os.environ.get("LINEAR_API_KEY", ""),
            )
        )
        return {
            "name": "linear_api_key",
            "ok": configured,
            "severity": "ok" if configured else "blocker",
            "summary": (
                "Linear materialization key is configured."
                if configured
                else "Linear materialization key is not configured."
            ),
            "details": {
                "configured": configured,
            },
        }

    def _daemon_check(self) -> dict[str, Any]:
        record = self.daemon_state.get_last_tick()
        if record is None:
            return {
                "name": "daemon_last_tick",
                "ok": False,
                "severity": "warning",
                "summary": "No persisted daemon tick is recorded yet.",
                "details": {
                    "present": False,
                },
            }
        tick = record.value.get("tick")
        return {
            "name": "daemon_last_tick",
            "ok": True,
            "severity": "ok",
            "summary": f"Last daemon tick is available ({tick}).",
            "details": {
                "present": True,
                "tick": tick,
                "updated_at": record.updated_at,
            },
        }
