from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from orx.config import resolve_runtime_paths
from orx.doctor import HostDoctorService
from orx.runtime_state import DaemonStateService
from orx.storage import CURRENT_SCHEMA_VERSION, Storage


class HostDoctorServiceTests(unittest.TestCase):
    def test_payload_reports_ready_host_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            storage.bootstrap()
            DaemonStateService(storage).record_last_tick(
                {
                    "home": temp_dir,
                    "db_path": str(storage.paths.db_path),
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "tick": "idle",
                    "proposal_materialization": {
                        "status": "idle",
                        "eligible": 0,
                        "materialized": 0,
                        "idempotent": 0,
                        "failed": 0,
                        "disabled_reason": None,
                        "errors": [],
                    },
                }
            )
            with patch.dict(
                os.environ,
                {"ORX_LINEAR_API_KEY": "secret-value"},
                clear=False,
            ), patch("shutil.which", return_value="/usr/bin/tmux"):
                payload = HostDoctorService(storage).payload()

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["blockers"], [])
            linear_check = next(
                check for check in payload["checks"] if check["name"] == "linear_api_key"
            )
            self.assertTrue(linear_check["details"]["configured"])
            self.assertNotIn("secret-value", str(payload))

    def test_payload_reports_missing_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(resolve_runtime_paths(temp_dir))
            with patch.dict(
                os.environ,
                {"ORX_ENV_DISABLE": "1"},
                clear=True,
            ), patch("shutil.which", return_value=None):
                payload = HostDoctorService(storage).payload()

            self.assertFalse(payload["ok"])
            self.assertIn(
                "Runtime database is missing or not bootstrapped to the current schema.",
                payload["blockers"],
            )
            self.assertIn("tmux is not installed or not on PATH.", payload["blockers"])
            self.assertIn("Linear materialization key is not configured.", payload["blockers"])
            self.assertIn("No persisted daemon tick is recorded yet.", payload["warnings"])


if __name__ == "__main__":
    unittest.main()
