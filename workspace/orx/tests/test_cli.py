from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orx.storage import CURRENT_SCHEMA_VERSION


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "bin" / "orx"


class CliTests(unittest.TestCase):
    def test_init_bootstraps_runtime_and_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self._run("--json", "--home", temp_dir, "init")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "init")
            self.assertEqual(payload["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertTrue(Path(payload["db_path"]).exists())

    def test_doctor_reports_blockers_before_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self._run("--json", "--home", temp_dir, "doctor")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "doctor")
            self.assertFalse(payload["ok"])
            self.assertIn(
                "Runtime database is missing or not bootstrapped to the current schema.",
                payload["blockers"],
            )
            self.assertIn(
                "Linear materialization key is not configured.",
                payload["blockers"],
            )

    def test_daemon_run_once_bootstraps_without_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self._run("--json", "--home", temp_dir, "daemon", "run", "--once")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "daemon run")
            self.assertEqual(payload["tick"], "idle")
            self.assertEqual(payload["stopped"], "once")
            self.assertEqual(payload["schema_version"], CURRENT_SCHEMA_VERSION)
            self.assertEqual(payload["proposal_materialization"]["status"], "idle")

    def test_daemon_run_once_degrades_cleanly_without_linear_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self._run("--json", "--home", temp_dir, "daemon", "run", "--once")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["proposal_materialization"]["status"], "idle")

    def test_dispatch_context_emits_restart_safe_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "register-project",
                "--project-key",
                "alpha",
                "--display-name",
                "Alpha",
                "--repo-root",
                "/tmp/alpha",
                "--owning-bot",
                "alpha_bot",
            )

            completed = self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "context",
                "--project-key",
                "alpha",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "dispatch context")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["context"]["project"]["project_key"], "alpha")
            self.assertIsNone(payload["context"]["issue"])
            self.assertIsNone(payload["context"]["recovery"])
            self.assertEqual(payload["context"]["runtime"]["active_issue_key"], None)

    def test_dispatch_drift_emits_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "register-project",
                "--project-key",
                "alpha",
                "--display-name",
                "Alpha",
                "--repo-root",
                "/tmp/alpha",
                "--owning-bot",
                "alpha_bot",
            )

            completed = self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "drift",
                "--project-key",
                "alpha",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "dispatch drift")
            self.assertTrue(payload["ok"])
            self.assertIn("checked_at", payload["drift"])
            self.assertIn("repo_root_exists", payload["drift"]["checks"])

    def test_dispatch_deregister_project_removes_registry_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "register-project",
                "--project-key",
                "alpha",
                "--display-name",
                "Alpha",
                "--repo-root",
                "/tmp/alpha",
                "--owning-bot",
                "alpha_bot",
            )

            completed = self._run(
                "--json",
                "--home",
                temp_dir,
                "dispatch",
                "deregister-project",
                "--project-key",
                "alpha",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["command"], "dispatch deregister-project")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["project"]["project_key"], "alpha")
            dashboard = self._run("--json", "--home", temp_dir, "dispatch", "dashboard")
            self.assertEqual(json.loads(dashboard.stdout)["projects"], [])

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("ORX_LINEAR_API_KEY", None)
        env.pop("LINEAR_API_KEY", None)
        env["ORX_ENV_DISABLE"] = "1"
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )


if __name__ == "__main__":
    unittest.main()
